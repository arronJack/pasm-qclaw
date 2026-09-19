# -*- coding: utf-8 -*-
"""列主序 4x4 矩阵 / 向量数学（纯 Python，无 numpy）。

为什么不用 numpy：desktop 端实测根本没用到 numpy，为渲染器引进来
等于凭空给打包加一个几十 MB 的二进制依赖。这里每帧的矩阵运算量是
「20 个骨骼 × 常数次 4x4 乘法」量级（约 2 千次浮点操作），纯 Python
耗时在 0.2ms 内 —— 顶点变换在 GLSL 里由 GPU 完成，CPU 只算骨骼矩阵。

约定与 OpenGL 一致：**列主序**，``m[col * 4 + row]``；乘法的语义是
``mul(a, b)`` 表示「先施加 b，再施加 a」（即 GL 的 ``a * b``）。
"""

import math

FLOATS = 16


def ident():
    return [1.0, 0.0, 0.0, 0.0,
            0.0, 1.0, 0.0, 0.0,
            0.0, 0.0, 1.0, 0.0,
            0.0, 0.0, 0.0, 1.0]


def mul(a, b):
    """返回 a * b（列主序）。展开写以避免循环开销。"""
    return [
        a[0] * b[0] + a[4] * b[1] + a[8] * b[2] + a[12] * b[3],
        a[1] * b[0] + a[5] * b[1] + a[9] * b[2] + a[13] * b[3],
        a[2] * b[0] + a[6] * b[1] + a[10] * b[2] + a[14] * b[3],
        a[3] * b[0] + a[7] * b[1] + a[11] * b[2] + a[15] * b[3],

        a[0] * b[4] + a[4] * b[5] + a[8] * b[6] + a[12] * b[7],
        a[1] * b[4] + a[5] * b[5] + a[9] * b[6] + a[13] * b[7],
        a[2] * b[4] + a[6] * b[5] + a[10] * b[6] + a[14] * b[7],
        a[3] * b[4] + a[7] * b[5] + a[11] * b[6] + a[15] * b[7],

        a[0] * b[8] + a[4] * b[9] + a[8] * b[10] + a[12] * b[11],
        a[1] * b[8] + a[5] * b[9] + a[9] * b[10] + a[13] * b[11],
        a[2] * b[8] + a[6] * b[9] + a[10] * b[10] + a[14] * b[11],
        a[3] * b[8] + a[7] * b[9] + a[11] * b[10] + a[15] * b[11],

        a[0] * b[12] + a[4] * b[13] + a[8] * b[14] + a[12] * b[15],
        a[1] * b[12] + a[5] * b[13] + a[9] * b[14] + a[13] * b[15],
        a[2] * b[12] + a[6] * b[13] + a[10] * b[14] + a[14] * b[15],
        a[3] * b[12] + a[7] * b[13] + a[11] * b[14] + a[15] * b[15],
    ]


def mul_all(*ms):
    """链式相乘 mul_all(a, b, c) == a * b * c（左到右）。"""
    if not ms:
        return ident()
    r = ms[0]
    for m in ms[1:]:
        r = mul(r, m)
    return r


def translate(x, y, z):
    return [1.0, 0.0, 0.0, 0.0,
            0.0, 1.0, 0.0, 0.0,
            0.0, 0.0, 1.0, 0.0,
            float(x), float(y), float(z), 1.0]


def scale(x, y=None, z=None):
    if y is None:
        y = x
    if z is None:
        z = x
    return [float(x), 0.0, 0.0, 0.0,
            0.0, float(y), 0.0, 0.0,
            0.0, 0.0, float(z), 0.0,
            0.0, 0.0, 0.0, 1.0]


def scale_v(s):
    return scale(s[0], s[1], s[2])


def rot_x(a):
    c, s = math.cos(a), math.sin(a)
    return [1.0, 0.0, 0.0, 0.0,
            0.0, c, s, 0.0,
            0.0, -s, c, 0.0,
            0.0, 0.0, 0.0, 1.0]


def rot_y(a):
    c, s = math.cos(a), math.sin(a)
    return [c, 0.0, -s, 0.0,
            0.0, 1.0, 0.0, 0.0,
            s, 0.0, c, 0.0,
            0.0, 0.0, 0.0, 1.0]


def rot_z(a):
    c, s = math.cos(a), math.sin(a)
    return [c, s, 0.0, 0.0,
            -s, c, 0.0, 0.0,
            0.0, 0.0, 1.0, 0.0,
            0.0, 0.0, 0.0, 1.0]


def euler(x, y, z):
    """欧拉角（弧度）→ 矩阵，施加顺序 X→Y→Z（即 Rz*Ry*Rx）。"""
    return mul(mul(rot_z(z), rot_y(y)), rot_x(x))


def compose(pos=(0.0, 0.0, 0.0), rot=(0.0, 0.0, 0.0), scl=(1.0, 1.0, 1.0)):
    """T * R * S —— 骨骼局部矩阵的标准组合。"""
    return mul_all(translate(pos[0], pos[1], pos[2]),
                   euler(rot[0], rot[1], rot[2]),
                   scale(scl[0], scl[1], scl[2]))


def perspective(fovy_deg, aspect, near, far):
    f = 1.0 / math.tan(math.radians(fovy_deg) * 0.5)
    nf = 1.0 / (near - far)
    return [f / aspect, 0.0, 0.0, 0.0,
            0.0, f, 0.0, 0.0,
            0.0, 0.0, (far + near) * nf, -1.0,
            0.0, 0.0, 2.0 * far * near * nf, 0.0]


def ortho(l, r, b, t, n, f):
    return [2.0 / (r - l), 0.0, 0.0, 0.0,
            0.0, 2.0 / (t - b), 0.0, 0.0,
            0.0, 0.0, -2.0 / (f - n), 0.0,
            -(r + l) / (r - l), -(t + b) / (t - b), -(f + n) / (f - n), 1.0]


def look_at(eye, center, up=(0.0, 1.0, 0.0)):
    fx, fy, fz = (center[0] - eye[0], center[1] - eye[1], center[2] - eye[2])
    fl = math.sqrt(fx * fx + fy * fy + fz * fz) or 1.0
    fx, fy, fz = fx / fl, fy / fl, fz / fl

    sx = fy * up[2] - fz * up[1]
    sy = fz * up[0] - fx * up[2]
    sz = fx * up[1] - fy * up[0]
    sl = math.sqrt(sx * sx + sy * sy + sz * sz) or 1.0
    sx, sy, sz = sx / sl, sy / sl, sz / sl

    ux = sy * fz - sz * fy
    uy = sz * fx - sx * fz
    uz = sx * fy - sy * fx

    return [sx, ux, -fx, 0.0,
            sy, uy, -fy, 0.0,
            sz, uz, -fz, 0.0,
            -(sx * eye[0] + sy * eye[1] + sz * eye[2]),
            -(ux * eye[0] + uy * eye[1] + uz * eye[2]),
            (fx * eye[0] + fy * eye[1] + fz * eye[2]),
            1.0]


def normal_mat3(model):
    """模型矩阵左上 3x3 的逆转置（列主序，返回 9 个 float）。

    只有含非等比缩放时才真正需要它，但机体到处是非等比（胶囊拉伸），
    所以统一走这条路径，避免光照被缩放带歪。
    """
    a, b, c = model[0], model[4], model[8]
    d, e, f = model[1], model[5], model[9]
    g, h, i = model[2], model[6], model[10]

    det = a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)
    if abs(det) < 1e-12:
        return [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]
    id_ = 1.0 / det

    # 逆矩阵（列主序）→ 转置得到逆转置
    inv = [(e * i - f * h) * id_, (c * h - b * i) * id_, (b * f - c * e) * id_,
           (f * g - d * i) * id_, (a * i - c * g) * id_, (c * d - a * f) * id_,
           (d * h - e * g) * id_, (b * g - a * h) * id_, (a * e - b * d) * id_]
    # inv 已是「转置后的逆」（即逆转置），列主序排列
    return inv


def xform_point(m, p):
    x, y, z = p
    return (m[0] * x + m[4] * y + m[8] * z + m[12],
            m[1] * x + m[5] * y + m[9] * z + m[13],
            m[2] * x + m[6] * y + m[10] * z + m[14])


def blend(a, b, t):
    """矩阵逐元素线性插值（骨骼平滑过渡够用，且天然连续）。"""
    t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
    u = 1.0 - t
    return [a[i] * u + b[i] * t for i in range(16)]


def lerp(a, b, t):
    return a + (b - a) * t


def smoothstep(t):
    t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
    return t * t * (3.0 - 2.0 * t)


def ease_out(t, power=3.0):
    t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
    return 1.0 - (1.0 - t) ** power


def ease_in_out(t):
    t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
    return t * t * (3.0 - 2.0 * t)


def damp(cur, target, rate, dt):
    """指数趋近，与帧率无关 —— 用于关节角平滑，避免动作"跳"。"""
    if rate <= 0.0:
        return target
    k = 1.0 - math.exp(-rate * dt)
    return cur + (target - cur) * k


def wrap_pi(a):
    """把角度折到 (-pi, pi]，防止连续旋转时绕远路。"""
    while a > math.pi:
        a -= 2.0 * math.pi
    while a <= -math.pi:
        a += 2.0 * math.pi
    return a


def damp_angle(cur, target, rate, dt):
    return cur + wrap_pi(target - cur) * (1.0 - math.exp(-rate * dt))
