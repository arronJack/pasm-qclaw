# -*- coding: utf-8 -*-
"""GPU 渲染器 —— 把骨骼姿态画成一张带透明通道的 QImage。

为什么是「离屏 FBO → QImage」而不是直接用 QOpenGLWidget：
  桌面宠物窗口开了 ``WA_TranslucentBackground``（小人要贴在桌面上），
  而 QOpenGLWidget 对透明背景支持很差（默认不透明、与半透明窗口合成有坑）。
  走离屏渲染再交给 QPainter 合成，透明背景、气泡、名字、状态全部原样保留，
  且 PetAvatar 的对外接口一个字都不用改。

顶点数据一律「展开后」上传（``vbytes_flat``），用 ``glDrawArrays`` 绘制：
PySide6 的 ``glDrawElements`` 对 ``void* indices`` 参数拒收 NULL、传 bytes
又会取对象地址而非偏移 0，语义不可控；展开顶点只多一点显存，换来零风险。

抗锯齿用 **2× 超采样**而不是 MSAA：MSAA 的 FBO 读回要走 resolve，
而 2× SSAA 只是渲染到 2 倍分辨率再平滑缩放 —— 顺带把 alpha 边缘也
一并柔化了，透明背景下的边缘质量反而更好。
"""

import math

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QMatrix4x4, QVector3D, QColor, QPainter

from . import glconst as G
from . import m4
from .mat import MAT_GLOW, MAT_VISOR, resolve

# —— 光照（相机空间固定方向，让角色转起来明暗有变化）——
KEY_DIR = (0.46, 0.78, 0.62)
FILL_DIR = (-0.62, 0.14, 0.38)
AMBIENT_TOP = 0.50
AMBIENT_BOTTOM = 0.30

_GLSL_VS = """#version 330 core
layout(location = 0) in vec3 aPos;
layout(location = 1) in vec3 aNrm;
uniform mat4 uModel;
uniform mat4 uViewProj;
uniform mat4 uNormal;
out vec3 vW;
out vec3 vN;
void main() {
    vec4 w = uModel * vec4(aPos, 1.0);
    vW = w.xyz;
    vN = mat3(uNormal) * aNrm;
    gl_Position = uViewProj * w;
}
"""

_GLSL_FS = """#version 330 core
in vec3 vW;
in vec3 vN;
uniform vec3 uBase;
uniform vec3 uEmis;
uniform vec3 uRimC;
uniform float uMetal;
uniform float uRough;
uniform float uRimK;
uniform float uCoat;
uniform vec3 uCam;
uniform vec3 uKey;
uniform vec3 uFill;
uniform float uAmbTop;
uniform float uAmbBot;
out vec4 fragColor;
void main() {
    vec3 N = normalize(vN);
    vec3 V = normalize(uCam - vW);
    // 半球环境光：上亮下暗 —— 低成本但很有效的体积感来源
    vec3 amb = uBase * mix(uAmbBot, uAmbTop, N.y * 0.5 + 0.5);
    float ndl = max(dot(N, uKey), 0.0);
    float ndf = max(dot(N, uFill), 0.0);
    vec3 diff = uBase * (ndl * 0.95 + ndf * 0.32);
    vec3 H = normalize(uKey + V);
    float ndh = max(dot(N, H), 0.0);
    // 指数别开太大：rough=0.22 时原来的 mix(24,300) 会算出 239，
    // 高光缩成单个像素、肉眼读不到，金属感直接消失。14~80 才有"漆面光斑"。
    float sp = pow(ndh, mix(14.0, 80.0, 1.0 - uRough));
    vec3 spec = mix(vec3(sp * 0.95), uBase * sp * 2.0, uMetal);
    // —— 清漆层：更窄更锐的第二高光 + 菲涅尔掠射反射 ——
    // 这是"厚漆金属"与"塑料"的分水岭：塑料只有一个宽高光，
    // 漆面金属在宽高光之上还有一层镜面清漆的窄光斑，边缘因掠射而发亮。
    float spc = pow(ndh, mix(46.0, 320.0, 1.0 - uRough));
    float fres = pow(1.0 - max(dot(N, V), 0.0), 4.0);
    vec3 coat = (vec3(spc) * 0.60 + uRimC * fres * 0.95) * uCoat;
    float rim = pow(1.0 - max(dot(N, V), 0.0), 2.4) * uRimK;
    vec3 col = amb + diff * 0.74 + spec * 1.35 + coat + uRimC * rim + uEmis * 0.95;
    // 色调映射：把上限压在 1.0 附近，避免发光件与高光死白成一坨白斑。
    // （原式 (col+0.80)*1.90 会让 col>0.9 的像素直接顶到 255 纯白。）
    col = col / (col + vec3(0.94)) * 1.86;
    fragColor = vec4(col, 1.0);
}
"""


def _qm(mat):
    """列主序 list[16] → QMatrix4x4（Qt 构造函数按行主序读参数）。"""
    return QMatrix4x4(mat[0], mat[4], mat[8], mat[12],
                      mat[1], mat[5], mat[9], mat[13],
                      mat[2], mat[6], mat[10], mat[14],
                      mat[3], mat[7], mat[11], mat[15])


def _normal4(model):
    """法线矩阵（逆转置）扩成 4x4，统一走 QMatrix4x4 通道。"""
    n = m4.normal_mat3(model)
    return [n[0], n[1], n[2], 0.0,
            n[3], n[4], n[5], 0.0,
            n[6], n[7], n[8], 0.0,
            0.0, 0.0, 0.0, 1.0]


def _v3(t):
    return QVector3D(float(t[0]), float(t[1]), float(t[2]))


class GLUnavailable(Exception):
    pass


class Pet3DRenderer(object):
    """离屏 GL 渲染器。构造不碰 GL；首次 render() 惰性初始化。"""

    def __init__(self, supersample=2, fov=19.0):
        self.ss = max(1, int(supersample))
        self.fov = float(fov)
        self.ready = False
        self.error = ""
        self.info = ""
        self._ctx = None
        self._surf = None
        self._gl = None
        self._prog = None
        self._fbo = None
        self._fbo_size = (0, 0)
        self._u = {}
        self._mesh_uploaded = set()
        self.draw_calls = 0

    # ---------- 初始化 ----------
    def init(self):
        if self.ready:
            return True
        try:
            from PySide6.QtGui import (QGuiApplication, QOpenGLContext,
                                       QOffscreenSurface, QSurfaceFormat)
            from PySide6.QtOpenGL import (QOpenGLShaderProgram, QOpenGLShader)
        except Exception as e:
            self.error = "PySide6 OpenGL 模块不可用: %s" % e
            return False

        if QGuiApplication.instance() is None:
            self.error = "尚无 QGuiApplication"
            return False

        fmt = G.make_surface_format()
        try:
            self._surf = QOffscreenSurface()
            self._surf.setFormat(fmt)
            self._surf.create()
            if not self._surf.isValid():
                self.error = "离屏表面无效"
                return False
            self._ctx = QOpenGLContext()
            self._ctx.setFormat(fmt)
            if not self._ctx.create():
                self.error = "上下文创建失败"
                return False
            if not self._ctx.makeCurrent(self._surf):
                self.error = "上下文激活失败"
                return False
            self._gl = G.get_functions(self._ctx)
            if self._gl is None:
                self.error = "glXXX 函数集获取失败"
                return False

            prog = QOpenGLShaderProgram()
            if not prog.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Vertex, _GLSL_VS):
                self.error = "顶点着色器编译失败: %s" % prog.log()[:200]
                return False
            if not prog.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Fragment, _GLSL_FS):
                self.error = "片元着色器编译失败: %s" % prog.log()[:200]
                return False
            if not prog.link():
                self.error = "着色器链接失败: %s" % prog.log()[:200]
                return False
            prog.bind()
            for nm in ("uModel", "uViewProj", "uNormal", "uBase", "uEmis", "uRimC",
                       "uMetal", "uRough", "uRimK", "uCoat", "uCam", "uKey", "uFill",
                       "uAmbTop", "uAmbBot"):
                self._u[nm] = prog.uniformLocation(nm)
            self._prog = prog

            got = self._ctx.format()
            self.info = "OpenGL %d.%d %s / SSAA %dx" % (
                got.majorVersion(), got.minorVersion(),
                "core" if got.profile() else "compat", self.ss)
            self.ready = True
            return True
        except Exception as e:
            self.error = "%s: %s" % (type(e).__name__, e)
            return False
        finally:
            try:
                if self._ctx is not None:
                    self._ctx.doneCurrent()
            except Exception:
                pass

    def release(self):
        try:
            if self._ctx is not None and self._surf is not None:
                self._ctx.makeCurrent(self._surf)
                for m in list(getattr(self, "_meshes", {}).values()):
                    try:
                        if m.vbo is not None:
                            m.vbo.destroy()
                        if m.ibo is not None:
                            m.ibo.destroy()
                        if m.vao is not None:
                            m.vao.destroy()
                    except Exception:
                        pass
                self._meshes = {}
                self._mesh_uploaded.clear()
                self._ctx.doneCurrent()
        except Exception:
            pass
        self._fbo = None
        self._prog = None
        self._gl = None
        self._ctx = None
        self._surf = None
        self.ready = False

    # ---------- 资源上传 ----------
    def _upload(self, mesh):
        gl = self._gl
        from PySide6.QtOpenGL import (QOpenGLBuffer, QOpenGLVertexArrayObject)
        # 只上传展开顶点（glDrawArrays 用）。不上传索引缓冲 —— 见文件头说明：
        # PySide6 的 glDrawElements 指针参数语义不可控，索性绕开。
        vbo = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
        vbo.create()
        vbo.bind()
        data = mesh.vbytes_flat
        try:
            vbo.allocate(data, len(data))
        except TypeError:
            vbo.allocate(len(data))
            vbo.write(0, data, len(data))
        vbo.setUsagePattern(QOpenGLBuffer.UsagePattern.StaticDraw)

        vao = QOpenGLVertexArrayObject()
        vao.create()
        vao.bind()
        vbo.bind()
        self._prog.enableAttributeArray(0)
        self._prog.setAttributeBuffer(0, G.GL_FLOAT, 0, 3, G.VERTEX_BYTES)
        self._prog.enableAttributeArray(1)
        self._prog.setAttributeBuffer(1, G.GL_FLOAT, 12, 3, G.VERTEX_BYTES)
        vao.release()
        vbo.release()

        mesh.vbo, mesh.vao = vbo, vao
        mesh.uploaded = True
        return mesh

    def _ensure_meshes(self, rig):
        cache = getattr(self, "_meshes", None)
        if cache is None:
            cache = self._meshes = {}
        for idx, p in enumerate(rig.parts):
            key = (id(rig), idx)
            if key in cache and cache[key] is p.mesh:
                continue
            if not p.mesh.uploaded:
                self._upload(p.mesh)
            cache[key] = p.mesh

    # ---------- 相机 ----------
    def _camera(self, rig):
        """自动取景：以**静立姿态**的包围盒构固定画框。

        刻意不用当前帧的动态 AABB —— 那样挥手抬臂会让画面左右漂移、
        跳跃会让小人"一边动一边缩放"。取静立基准 + 足够余量，画面稳定。
        宽度与高度取较大者，否则张臂动作会被左右裁掉。
        """
        try:
            bb = getattr(rig, "base_bounds", None) or rig.bounds()
            x0, y0, z0, x1, y1, z1 = bb
            h = max(1e-3, y1 - y0)
            w = max(1e-3, x1 - x0)
        except Exception:
            y0, y1, z1 = 0.0, 1.2, 0.2
            h, w = 1.2, 0.5
        # 余量取「够动作张开、又不留大白边」的平衡点：
        # 原来 h*1.14 / w*1.30 让角色只占画框 23%，实际显示时小人偏小。
        # 收到 h*1.06 / w*1.14，仍能容下挥手与蹦跳（宽度按 h*0.80 兜底）。
        need = max(h * 1.06, w * 1.14, h * 0.80)
        cx = 0.0                       # 始终以角色中线居中，不随动作漂移
        cy = (y0 + y1) * 0.5
        dist = need / (2.0 * math.tan(math.radians(self.fov) * 0.5)) + (z1 + 0.02)
        return (cx, cy, dist), (cx, cy, 0.0)

    # ---------- 渲染 ----------
    def render(self, rig, pal, size_px, bg=(0.0, 0.0, 0.0, 0.0)):
        """渲染一帧，返回带 alpha 的 QImage；失败返回 None（调用方降级 2D）。"""
        if not self.ready and not self.init():
            return None
        try:
            return self._render(rig, pal, size_px, bg)
        except Exception as e:
            self.error = "%s: %s" % (type(e).__name__, e)
            self.ready = False
            return None

    def _read_pixels(self, w, h):
        """用 glReadPixels 自读当前 FBO 的颜色缓冲，返回 RGBA8888 的 QImage。

        为什么不用 ``QOpenGLFramebufferObject.toImage()``：
        实测在离屏上下文里它返回的是**全黑不透明图** —— 内容与 alpha 都读不到，
        但 glReadPixels 给出的像素（含 alpha=0 的透明背景）完全正确。
        这条路同时保证了「角色可见」与「背景透明」两件事。

        OpenGL 的原点在左下，所以必须垂直翻转；用 .copy() 让 QImage 持有
        自己的数据副本（否则 bytes 被回收后指针悬空）。
        """
        import ctypes
        gl = self._gl
        iw, ih = int(w), int(h)
        n = iw * ih * 4
        buf = (ctypes.c_ubyte * n)()
        try:
            gl.glPixelStorei(G.GL_PACK_ALIGNMENT, 1)
        except Exception:
            pass
        gl.glReadPixels(0, 0, iw, ih, G.GL_RGBA, G.GL_UNSIGNED_BYTE, buf)
        raw = bytes(buf)
        img = QImage(raw, iw, ih, QImage.Format_RGBA8888).copy()
        return img.mirrored(False, True)

    def _render(self, rig, pal, size_px, bg):
        from PySide6.QtOpenGL import (QOpenGLFramebufferObject,
                                      QOpenGLFramebufferObjectFormat)
        gl = self._gl
        ctx = self._ctx
        if not ctx.makeCurrent(self._surf):
            return None
        try:
            w = int(size_px * self.ss)
            h = w
            if self._fbo is None or self._fbo_size != (w, h):
                fmt = QOpenGLFramebufferObjectFormat()
                # ⚠️ 深度附件必须是 CombinedDepthStencil（渲染缓冲），
                # 不能用 Attachment.Depth（深度**纹理**）—— 后者在部分驱动上
                # 根本不生效，表现为**深度测试静默失效**：所有部件按绘制顺序
                # 互相覆盖，71 个部件糊成一团。这个 bug 极隐蔽：
                # 不报错、不崩溃，只是"画得很丑"。
                fmt.setAttachment(QOpenGLFramebufferObject.Attachment.CombinedDepthStencil)
                try:
                    fmt.setInternalTextureFormat(G.GL_RGBA8)
                except Exception:
                    pass
                self._fbo = QOpenGLFramebufferObject(w, h, fmt)
                self._fbo_size = (w, h)
                self._mesh_uploaded.clear()
            fbo = self._fbo
            if not fbo.isValid() or not fbo.bind():
                return None
            try:
                self._draw(rig, pal, w, h, bg)
                img = self._read_pixels(w, h)
            finally:
                fbo.release()

            if img is None or img.isNull():
                return None
            if self.ss > 1:
                img = img.scaled(int(size_px), int(size_px),
                                 Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
            return img
        finally:
            ctx.doneCurrent()

    def _draw(self, rig, pal, w, h, bg):
        gl = self._gl
        prog = self._prog
        self._ensure_meshes(rig)

        gl.glViewport(0, 0, w, h)
        gl.glClearColor(bg[0], bg[1], bg[2], bg[3])
        gl.glClear(G.GL_COLOR_BUFFER_BIT | G.GL_DEPTH_BUFFER_BIT)
        gl.glEnable(G.GL_DEPTH_TEST)
        gl.glDepthFunc(G.GL_LEQUAL)
        try:
            gl.glDepthMask(True)      # 显式开启深度写入：上一帧若被别处关掉会静默失效
        except Exception:
            pass
        gl.glEnable(G.GL_CULL_FACE)
        gl.glCullFace(G.GL_BACK)
        gl.glFrontFace(G.GL_CCW)
        gl.glDisable(G.GL_BLEND)

        prog.bind()
        eye, target = self._camera(rig)
        proj = m4.perspective(self.fov, 1.0, 0.05, 40.0)
        view = m4.look_at(eye, target, (0.0, 1.0, 0.0))
        vp = m4.mul(proj, view)

        u = self._u
        prog.setUniformValue(u["uViewProj"], _qm(vp))
        prog.setUniformValue(u["uCam"], _v3(eye))
        prog.setUniformValue(u["uKey"], _v3(KEY_DIR))
        prog.setUniformValue(u["uFill"], _v3(FILL_DIR))
        # ⚠️ 标量 uniform 必须用 setUniformValue1f：
        # PySide6 的 setUniformValue(loc, float) 会解析成 **int 重载**，
        # 把 0.48 静默截断成 0 —— 表现为「环境光、高光、清漆、边缘光全部消失，
        # 只剩漫反射」，画面又暗又平、像哑光塑料。不报错、不崩溃，极难发现。
        prog.setUniformValue1f(u["uAmbTop"], float(AMBIENT_TOP))
        prog.setUniformValue1f(u["uAmbBot"], float(AMBIENT_BOTTOM))

        bones = rig.bones
        calls = 0
        for p in rig.parts:
            if not p.visible or not p.mesh.uploaded:
                continue
            model = p.model(bones)
            base, emis, metal, rough, rimk, coat = resolve(pal, p.mat)
            prog.setUniformValue(u["uModel"], _qm(model))
            prog.setUniformValue(u["uNormal"], _qm(_normal4(model)))
            prog.setUniformValue(u["uBase"], _v3(base))
            prog.setUniformValue(u["uEmis"], _v3(emis))
            prog.setUniformValue(u["uRimC"], _v3(pal.rim))
            prog.setUniformValue1f(u["uMetal"], float(metal))
            prog.setUniformValue1f(u["uRough"], float(rough))
            prog.setUniformValue1f(u["uRimK"], float(rimk))
            prog.setUniformValue1f(u["uCoat"], float(coat))
            p.mesh.vao.bind()
            gl.glDrawArrays(G.GL_TRIANGLES, 0, p.mesh.nflat)
            p.mesh.vao.release()
            calls += 1
        self.draw_calls = calls
        prog.release()


# —— 模块级单例：整个应用共用一个上下文，避免反复创建/销毁 ——
_INSTANCE = None


def renderer():
    global _INSTANCE
    if _INSTANCE is None:
        _INSTANCE = Pet3DRenderer()
    return _INSTANCE


def shutdown():
    global _INSTANCE
    if _INSTANCE is not None:
        _INSTANCE.release()
        _INSTANCE = None


def probe():
    """轻量探测本机 GPU 3D 可用性（结果缓存在单例里）。"""
    r = renderer()
    if not r.ready:
        r.init()
    return r.ready, (r.info or r.error)
