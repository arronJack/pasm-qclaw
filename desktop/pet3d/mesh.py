# -*- coding: utf-8 -*-
"""程序化网格生成：所有形体都由参数算出，不依赖任何外部 3D 资产。

这是本引擎最重要的产品性选择 —— 因为几何是算出来的，小人才能
**随成长档位连续变形**（头身比、四肢长短、装甲件数全部是参数），
而不是加载几套预制模型互相切换。代价是造型上限不如手工建模，
收益是零资产依赖 + 无限档位 + 包体几乎不增长。

顶点布局：交错 ``[x, y, z, nx, ny, nz]``；法线统一由三角形面法线
做面积加权平均得到（对所有参数化曲面通用，省掉逐生成器推导解析法线）。
"""

import math
from array import array

from . import glconst as G


def _sgnpow(x, p):
    a = abs(x)
    if a < 1e-9:
        return 0.0
    return math.copysign(a ** p, x)


class Mesh(object):
    """一份可用于 GPU 的静态几何。"""

    __slots__ = ("name", "vbytes", "ibytes", "vbytes_flat", "nflat",
                 "nverts", "ntris", "vbo", "ibo", "vao", "uploaded",
                 "bmin", "bmax")

    def __init__(self, name, positions, indices):
        self.name = name
        self.nverts = len(positions)
        self.ntris = len(indices) // 3
        # 局部 AABB（相机自动取景用）
        if positions:
            xs = [q[0] for q in positions]
            ys = [q[1] for q in positions]
            zs = [q[2] for q in positions]
            self.bmin = (min(xs), min(ys), min(zs))
            self.bmax = (max(xs), max(ys), max(zs))
        else:
            self.bmin = self.bmax = (0.0, 0.0, 0.0)
        normals = _vertex_normals(positions, indices)
        flat = array("f")
        ap = flat.append
        for i, p in enumerate(positions):
            n = normals[i]
            ap(p[0]); ap(p[1]); ap(p[2])
            ap(n[0]); ap(n[1]); ap(n[2])
        self.vbytes = flat.tobytes()
        self.ibytes = array("I", indices).tobytes()
        # 展开顶点：给 glDrawArrays 用。见模块顶部说明与 renderer 的注释。
        _v = flat
        self.vbytes_flat = array(
            "f", [_v[i * 6 + j] for i in indices for j in range(6)]).tobytes()
        self.nflat = len(indices)
        # GPU 侧句柄（渲染器首帧惰性创建）
        self.vbo = None
        self.ibo = None
        self.vao = None
        self.uploaded = False


def _vertex_normals(positions, indices):
    """面积加权顶点法线（叉积长度天然正比于面积，故无需再乘面积）。"""
    acc = [[0.0, 0.0, 0.0] for _ in range(len(positions))]
    for k in range(0, len(indices), 3):
        i0, i1, i2 = indices[k], indices[k + 1], indices[k + 2]
        p0, p1, p2 = positions[i0], positions[i1], positions[i2]
        ax, ay, az = p1[0] - p0[0], p1[1] - p0[1], p1[2] - p0[2]
        bx, by, bz = p2[0] - p0[0], p2[1] - p0[1], p2[2] - p0[2]
        cx = ay * bz - az * by
        cy = az * bx - ax * bz
        cz = ax * by - ay * bx
        for idx in (i0, i1, i2):
            a = acc[idx]
            a[0] += cx; a[1] += cy; a[2] += cz
    out = []
    for a in acc:
        l = math.sqrt(a[0] * a[0] + a[1] * a[1] + a[2] * a[2])
        if l < 1e-12:
            out.append((0.0, 1.0, 0.0))
        else:
            out.append((a[0] / l, a[1] / l, a[2] / l))
    return out


def _revolution(rings, seg, name, close_bottom=False, close_top=False):
    """把 [(y, radius), ...] 旋转体化成网格。rings 从下往上给。

    极点（radius≈0）自然退化，不需要特殊处理 —— 会生成退化三角形，
    法线计算会因为叉积为零而被面积加权自动忽略，视觉上无影响。
    """
    positions = []
    for (y, r) in rings:
        if r < 1e-9:
            positions.append((0.0, y, 0.0))
            for _ in range(seg - 1):
                positions.append((0.0, y, 0.0))
        else:
            for j in range(seg):
                a = 2.0 * math.pi * j / seg
                positions.append((r * math.cos(a), y, r * math.sin(a)))

    indices = []
    nring = len(rings)
    for i in range(nring - 1):
        base0 = i * seg
        base1 = (i + 1) * seg
        for j in range(seg):
            j2 = (j + 1) % seg
            a = base0 + j
            b = base0 + j2
            c = base1 + j2
            d = base1 + j
            indices.extend((a, d, c))
            indices.extend((a, c, b))
    return Mesh(name, positions, indices)


# ——————————————————————————————————————————————————————
# 基础形体
# ——————————————————————————————————————————————————————

def sphere(radius, seg=24, ring=14, name=None):
    rings = []
    for i in range(ring + 1):
        th = math.pi * i / ring          # 0 = 北极
        rings.append((radius * math.cos(th), radius * math.sin(th)))
    rings.reverse()                       # 旋转体要求从下往上
    return _revolution(rings, seg, name or "sph%.3f" % radius)


def capsule(radius, height, seg=24, cap_ring=5, name=None):
    """沿 Y 轴的胶囊：圆柱高 height，两端半球半径 radius。"""
    half = height * 0.5
    rings = []
    # 下极点
    rings.append((-half - radius, 0.0))
    for i in range(1, cap_ring):          # 下半球
        th = (math.pi * 0.5) * i / cap_ring
        rings.append((-half - radius * math.cos(th), radius * math.sin(th)))
    rings.append((-half, radius))
    rings.append((half, radius))
    for i in range(cap_ring - 1, 0, -1):  # 上半球
        th = (math.pi * 0.5) * i / cap_ring
        rings.append((half + radius * math.cos(th), radius * math.sin(th)))
    rings.append((half + radius, 0.0))
    return _revolution(rings, seg, name or "cap%.3f_%.3f" % (radius, height))


def superellipsoid(a, b, c, e1=0.45, e2=0.45, seg=24, ring=14, name=None):
    """超椭球 —— 椭球 / 圆角盒之间的连续族（Barr 参数化）。

    **指数就是 e 本身**：e=1 为标准椭球，e→0 趋近立方体，
    中间值即圆角盒（例如 e=0.45 已是相当方的机体壳）。
    机甲壳体全部由它生成 —— 圆角半径是个连续参数，比硬边盒子
    "科技感"强，而且能和成长档位一起平滑变化。
    """
    positions = []
    for i in range(ring + 1):
        v = -math.pi * 0.5 + math.pi * i / ring
        cv = _sgnpow(math.cos(v), e1)
        sv = _sgnpow(math.sin(v), e1)
        for j in range(seg):
            u = -math.pi + 2.0 * math.pi * j / seg
            cu = _sgnpow(math.cos(u), e2)
            su = _sgnpow(math.sin(u), e2)
            positions.append((a * cv * cu, b * sv, c * cv * su))

    indices = []
    for i in range(ring):
        b0 = i * seg
        b1 = (i + 1) * seg
        for j in range(seg):
            j2 = (j + 1) % seg
            indices.extend((b0 + j, b1 + j, b1 + j2))
            indices.extend((b0 + j, b1 + j2, b0 + j2))
    return Mesh(name or "sbox%.3f_%.3f_%.3f" % (a, b, c), positions, indices)


def cylinder(r0, r1, height, seg=20, cap_top=True, cap_bottom=True, name=None):
    """直圆柱 / 圆台（天线、关节轴、柱销）。顶点序：先底环，再顶环。"""
    positions = []
    for j in range(seg):
        a = 2.0 * math.pi * j / seg
        positions.append((r0 * math.cos(a), 0.0, r0 * math.sin(a)))
    for j in range(seg):
        a = 2.0 * math.pi * j / seg
        positions.append((r1 * math.cos(a), height, r1 * math.sin(a)))
    return _with_caps(positions, seg, r0, r1, height, cap_top, cap_bottom,
                      name or "cyl%.3f_%.3f" % (r0, height))


def m_positions(mesh):
    """从已完成的 Mesh 里取回顶点位置（调试/派生造型用）。"""
    f = array("f")
    f.frombytes(mesh.vbytes)
    out = []
    for i in range(mesh.nverts):
        k = i * G.VERTEX_FLOATS
        out.append((f[k], f[k + 1], f[k + 2]))
    return out


def _with_caps(positions, seg, r0, r1, height, cap_top, cap_bottom, name):
    idx = []
    for j in range(seg):
        j2 = (j + 1) % seg
        idx.extend((j, seg + j, seg + j2))
        idx.extend((j, seg + j2, j2))
    # 顶点顺序：先底面环(0..seg-1)，再顶面环(seg..2*seg-1)
    if cap_bottom and r0 > 1e-9:
        c = len(positions)
        positions.append((0.0, 0.0, 0.0))
        for j in range(seg):
            idx.extend((c, (j + 1) % seg, j))
    if cap_top and r1 > 1e-9:
        c = len(positions)
        positions.append((0.0, height, 0.0))
        base = seg
        for j in range(seg):
            idx.extend((c, base + j, base + (j + 1) % seg))
    return Mesh(name, positions, idx)


def torus(R, r, seg=24, ring=10, name=None):
    """圆环 —— 关节发光环 / 天线基座。"""
    positions = []
    for i in range(seg):
        a = 2.0 * math.pi * i / seg
        ca, sa = math.cos(a), math.sin(a)
        for j in range(ring):
            b = 2.0 * math.pi * j / ring
            rad = R + r * math.cos(b)
            positions.append((rad * ca, r * math.sin(b), rad * sa))
    indices = []
    for i in range(seg):
        i2 = (i + 1) % seg
        for j in range(ring):
            j2 = (j + 1) % ring
            a = i * ring + j
            b = i2 * ring + j
            c = i2 * ring + j2
            d = i * ring + j2
            indices.extend((a, b, c))
            indices.extend((a, c, d))
    return Mesh(name or "tor%.3f" % R, positions, indices)


def box(w, h, d, name=None):
    """硬边盒（少量部件用，例如面罩外框）。"""
    x, y, z = w * 0.5, h * 0.5, d * 0.5
    p = [(-x, -y, -z), (x, -y, -z), (x, y, -z), (-x, y, -z),
         (-x, -y, z), (x, -y, z), (x, y, z), (-x, y, z)]
    faces = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4),
             (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
    idx = []
    for f in faces:
        idx.extend((f[0], f[1], f[2], f[0], f[2], f[3]))
    return Mesh(name or "box%.2f" % w, p, idx)


def cone(radius, height, seg=16, name=None):
    rings = [(0.0, radius), (height, 0.0)]
    return _revolution(rings, seg, name or "cone%.3f" % height)


def quad(w, h, name=None):
    """朝向 +Z 的平面（地面阴影贴片 / 全息面板）。"""
    x, y = w * 0.5, h * 0.5
    p = [(-x, -y, 0.0), (x, -y, 0.0), (x, y, 0.0), (-x, y, 0.0)]
    return Mesh(name or "quad", p, [0, 1, 2, 0, 2, 3])

# ——————————————————————————————————————————————————————
# 机甲专用形体
# ——————————————————————————————————————————————————————

def shell(rings, a0, a1, seg=20, thick=0.022, taper=1.0, squash=1.0, name=None):
    """弧形装甲片 —— 绕 Y 轴的扇段实体，这是"分片装甲"的基本单元。

    rings : [(y, r_outer), ...] 从下往上给，半径可变化 → 能做出"隆起"的胸甲
    a0/a1 : 角度范围（弧度；0 = +X 方向，绕 Y 轴逆时针）
    taper : 顶端角度收窄系数（<1 = 上窄下宽，正是肩甲/胸甲的梯形轮廓）
    thick : 甲片厚度（内表面 = 外表面沿径向内缩 thick）
    squash: z 向半径 = r * squash —— 躯干是椭圆的（宽 > 深），
            圆形甲片贴上去会在前后浮空、左右嵌进去，必须按躯干宽深比压扁

    六个面全部闭合：外弧 + 内弧 + 两侧壁 + 上下壁 —— 这样背面抽壳、
    开背面剔除、开深度测试都不会漏光。
    """
    nr = len(rings)
    mid = (a0 + a1) * 0.5
    half = (a1 - a0) * 0.5

    outer = []
    inner = []
    for i, (y, r) in enumerate(rings):
        u = i / float(max(1, nr - 1))
        sc = 1.0 + (taper - 1.0) * u
        hh = half * sc
        for j in range(seg + 1):
            a = mid - hh + 2.0 * hh * j / seg
            ca, sa = math.cos(a), math.sin(a)
            outer.append((r * ca, y, r * squash * sa))
            ri = max(0.004, r - thick)
            inner.append((ri * ca, y, ri * squash * sa))

    nrow = seg + 1
    positions = outer + inner
    base_i = nr * nrow

    def O(i, j):
        return i * nrow + j

    def I(i, j):
        return base_i + i * nrow + j

    idx = []
    # 外弧面（法线朝外）
    for i in range(nr - 1):
        for j in range(seg):
            idx.extend((O(i, j), O(i + 1, j), O(i + 1, j + 1)))
            idx.extend((O(i, j), O(i + 1, j + 1), O(i, j + 1)))
    # 内弧面（法线朝内）
    for i in range(nr - 1):
        for j in range(seg):
            idx.extend((I(i, j), I(i + 1, j + 1), I(i + 1, j)))
            idx.extend((I(i, j), I(i, j + 1), I(i + 1, j + 1)))
    # 两侧壁
    for i in range(nr - 1):
        idx.extend((O(i, 0), I(i, 0), I(i + 1, 0)))
        idx.extend((O(i, 0), I(i + 1, 0), O(i + 1, 0)))
        s = seg
        idx.extend((O(i, s), O(i + 1, s), I(i + 1, s)))
        idx.extend((O(i, s), I(i + 1, s), I(i, s)))
    # 上下封口
    for j in range(seg):
        idx.extend((O(0, j), O(0, j + 1), I(0, j + 1)))
        idx.extend((O(0, j), I(0, j + 1), I(0, j)))
        idx.extend((O(nr - 1, j), I(nr - 1, j + 1), O(nr - 1, j + 1)))
        idx.extend((O(nr - 1, j), I(nr - 1, j), I(nr - 1, j + 1)))
    return Mesh(name or "shell%.3f" % (a1 - a0), positions, idx)


def bevel_plate(w, h, d, e=0.28, seg=30, ring=18, name=None):
    """倒角装甲块 —— 比 box 柔和、比球硬朗，用来做肩甲/膝甲/脚甲。

    e 越小越方正；e=0.28 左右已有明显"切角"轮廓。
    """
    return superellipsoid(w * 0.5, h * 0.5, d * 0.5, e, e, seg, ring,
                          name or "plate%.2f_%.2f_%.2f" % (w, h, d))


def fin(length, height, thick, sweep=0.35, name=None):
    """鳍 / 脊 —— 头顶中央脊、肩甲外尖、小腿后鳍。

    沿 Y 立起的一片薄板，前缘按 sweep 后掠收细。
    """
    hx, hz = thick * 0.5, length * 0.5
    p = []
    # 底缘 4 点 + 顶缘 4 点（顶缘后掠且收薄）
    for (yy, sc, back) in ((0.0, 1.0, 0.0), (height, 0.42, sweep * length)):
        p.extend([(-hx * sc, yy, -hz * sc + back),
                  (hx * sc, yy, -hz * sc + back),
                  (hx * sc, yy, hz * sc + back),
                  (-hx * sc, yy, hz * sc + back)])
    idx = [0, 1, 2, 0, 2, 3,
           4, 6, 5, 4, 7, 6,
           0, 4, 5, 0, 5, 1,
           1, 5, 6, 1, 6, 2,
           2, 6, 7, 2, 7, 3,
           3, 7, 4, 3, 4, 0]
    return Mesh(name or "fin%.2f" % height, p, idx)


def spike(radius, height, seg=12, name=None):
    """尖锥（肩甲外尖、膝甲尖）。"""
    rings = [(0.0, radius), (height * 0.72, radius * 0.30), (height, 0.0)]
    return _revolution(rings, seg, name or "spk%.3f" % height)


def torus_arc(R, r, a0, a1, seg=20, ring=8, name=None):
    """弧形管（发光环带的一段，避免整圈把甲片盖住）。"""
    positions = []
    for i in range(seg + 1):
        a = a0 + (a1 - a0) * i / seg
        ca, sa = math.cos(a), math.sin(a)
        for j in range(ring):
            b = 2.0 * math.pi * j / ring
            rad = R + r * math.cos(b)
            positions.append((rad * ca, r * math.sin(b), rad * sa))
    indices = []
    for i in range(seg):
        for j in range(ring):
            j2 = (j + 1) % ring
            a = i * ring + j
            b = (i + 1) * ring + j
            c = (i + 1) * ring + j2
            d = i * ring + j2
            indices.extend((a, b, c))
            indices.extend((a, c, d))
    return Mesh(name or "arc%.2f" % R, positions, indices)

