# -*- coding: utf-8 -*-
"""PASM 3D 小人引擎 —— 基础设施层。

设计约束（为什么这么写）：

1. **零新增依赖**。PySide6 自带的 QOpenGLShaderProgram / QOpenGLBuffer /
   QOpenGLFunctions_3_3_Core 已覆盖本渲染器用到的全部调用，因此不引
   PyOpenGL（多一个二进制依赖、打包多一组 DLL）。桌面端已经吃过
   QWebEngineView「+150MB 包体」的教训，这里不再重蹈。
2. **GL 常量硬编码**。PySide6 不导出 GL_* 宏（它们是 C 预处理宏，不是枚举），
   而 OpenGL 规范把这些数值钉死了 —— 硬编码是安全且唯一的做法。
   本文件是**全仓唯一**允许出现 GL 字面量的地方。
3. **优雅降级**。任何一步失败都不抛到界面层：渲染器侧统一捕获并回退 2D。
"""

# —— 缓冲区 / 清屏 ——
GL_DEPTH_BUFFER_BIT = 0x00000100
GL_COLOR_BUFFER_BIT = 0x00004000

# —— 像素读取（glReadPixels 自读 FBO）——
# 实测：QOpenGLFramebufferObject.toImage() 在离屏上下文里会返回**全黑不透明图**，
# 内容与 alpha 都读不到；而 glReadPixels 给出的 RGBA 完全正确。
GL_RGBA = 0x1908
GL_PACK_ALIGNMENT = 0x0D05

# —— 纹理内部格式 ——
# FBO 必须显式要 RGBA8：Qt 的默认内部格式在部分驱动上会退化成 RGB，
# 读回来 alpha 全 255 —— 桌面宠物就会变成一个黑方块（透明背景失效）。
GL_RGBA8 = 0x8058

# —— 数据类型 ——
GL_UNSIGNED_BYTE = 0x1401
GL_UNSIGNED_INT = 0x1405
GL_FLOAT = 0x1406

# —— 图元 ——
GL_POINTS = 0x0000
GL_LINES = 0x0001
GL_TRIANGLES = 0x0004

# —— 开关 ——
GL_DEPTH_TEST = 0x0B71
GL_CULL_FACE = 0x0B44
GL_BLEND = 0x0BE2
GL_MULTISAMPLE = 0x809D
GL_LINE_SMOOTH = 0x0B20

# —— 面 / 深度函数 ——
GL_FRONT = 0x0404
GL_BACK = 0x0405
GL_CCW = 0x0901
GL_CW = 0x0900
GL_LESS = 0x0201
GL_LEQUAL = 0x0203

# —— 混合因子 ——
GL_ZERO = 0x0000
GL_ONE = 0x0001
GL_SRC_ALPHA = 0x0302
GL_ONE_MINUS_SRC_ALPHA = 0x0303
GL_SRC_COLOR = 0x0300
GL_ONE_MINUS_SRC_COLOR = 0x0301

# —— 缓冲区绑定目标 ——
GL_ARRAY_BUFFER = 0x8892
GL_ELEMENT_ARRAY_BUFFER = 0x8893
GL_STATIC_DRAW = 0x88E4
GL_DYNAMIC_DRAW = 0x88E8

# 每个顶点：位置 3 + 法线 3 = 6 个 float
VERTEX_FLOATS = 6
VERTEX_BYTES = VERTEX_FLOATS * 4


def get_functions(ctx):
    """从当前 OpenGL 上下文取出 3.3 core 的 glXXX 函数集。

    返回已 ``initializeOpenGLFunctions()`` 的对象；失败返回 ``None``
    （调用方据此降级到 2D，绝不抛异常）。

    注意：PySide6 的 ``QOpenGLContext`` **没有** C++ 的 ``versionFunctions<T>()``
    模板方法，只能用 ``QOpenGLVersionFunctionsFactory``。
    """
    try:
        from PySide6.QtOpenGL import (QOpenGLVersionFunctionsFactory,
                                      QOpenGLVersionProfile, QOpenGLFunctions_3_3_Core)
        from PySide6.QtGui import QSurfaceFormat
    except Exception:
        return None

    try:
        prof = QOpenGLVersionProfile()
        prof.setVersion(3, 3)
        prof.setProfile(QSurfaceFormat.CoreProfile)
        f = QOpenGLVersionFunctionsFactory.get(prof, ctx)
        if f is not None and not isinstance(f, QOpenGLFunctions_3_3_Core):
            f = f.cast() if hasattr(f, "cast") else f
        if isinstance(f, QOpenGLFunctions_3_3_Core):
            f.initializeOpenGLFunctions()
            return f
    except Exception:
        pass

    # 兜底：直接实例化。实测在已 makeCurrent 的 3.3 context 下可用。
    try:
        from PySide6.QtOpenGL import QOpenGLFunctions_3_3_Core
        d = QOpenGLFunctions_3_3_Core()
        d.initializeOpenGLFunctions()
        return d
    except Exception:
        return None


def make_surface_format(multisample=0):
    """构造离屏渲染用的表面格式（3.3 core + 深度 + 可选 MSAA）。"""
    from PySide6.QtGui import QSurfaceFormat
    f = QSurfaceFormat()
    f.setVersion(3, 3)
    f.setProfile(QSurfaceFormat.CoreProfile)
    f.setDepthBufferSize(24)
    f.setStencilBufferSize(8)
    f.setAlphaBufferSize(8)
    if multisample and multisample > 0:
        f.setSamples(int(multisample))
    return f


def probe():
    """探测本机能否跑 GPU 3D。返回 ``(ok: bool, info: str)``。

    只做一次轻量上下文创建 —— 不做任何绘制，成本约几十毫秒，
    可在启动时或首次需要渲染时调用，结果应缓存。
    """
    try:
        from PySide6.QtGui import QGuiApplication, QOpenGLContext, QOffscreenSurface
    except Exception as e:
        return False, "PySide6 OpenGL 模块缺失: %s" % e

    if QGuiApplication.instance() is None:
        return False, "尚无 QGuiApplication（需在界面初始化后探测）"

    fmt = make_surface_format()
    try:
        surf = QOffscreenSurface()
        surf.setFormat(fmt)
        surf.create()
        if not surf.isValid():
            return False, "离屏表面创建失败"

        ctx = QOpenGLContext()
        ctx.setFormat(fmt)
        if not ctx.create():
            return False, "OpenGL 上下文创建失败"
        if not ctx.makeCurrent(surf):
            return False, "上下文激活失败"

        got = ctx.format()
        info = "OpenGL %d.%d (%s)" % (got.majorVersion(), got.minorVersion(),
                                      "core" if got.profile() else "compat")
        gl = get_functions(ctx)
        if gl is None:
            ctx.doneCurrent()
            return False, "glXXX 函数集获取失败"
        err = 0
        try:
            err = gl.glGetError()
        except Exception:
            err = -1
        try:
            from PySide6.QtOpenGL import QOpenGLShaderProgram, QOpenGLShader
            pr = QOpenGLShaderProgram()
            ok_v = pr.addShaderFromSourceCode(
                QOpenGLShader.ShaderTypeBit.Vertex,
                "#version 330 core\nlayout(location=0) in vec3 p;\n"
                "void main(){ gl_Position = vec4(p,1.0); }\n")
            ok_f = pr.addShaderFromSourceCode(
                QOpenGLShader.ShaderTypeBit.Fragment,
                "#version 330 core\nout vec4 c;\nvoid main(){ c = vec4(1.0); }\n")
            ok_l = pr.link()
            if not (ok_v and ok_f and ok_l):
                ctx.doneCurrent()
                return False, "GLSL 330 编译/链接失败: %s" % (pr.log()[:120],)
        except Exception as e:
            ctx.doneCurrent()
            return False, "shader 测试异常: %s" % e
        ctx.doneCurrent()
        return True, "%s, glGetError=%s, GLSL330 OK" % (info, err)
    except Exception as e:
        return False, "探测异常: %s" % (type(e).__name__, e)
