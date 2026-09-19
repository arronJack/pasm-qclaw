# -*- coding: utf-8 -*-
"""3D 材质与配色 —— 动力装甲的四色体系。

机甲"好看"的三个来源，按重要性排序：

1. **分片与层次**：亮甲 / 暗副甲 / 深色关节 三个明度层，靠几何分片 + 材质区分实现。
   一整块同色的壳是最"塑料"的形态 —— 所以每个部位都由「外层甲片 + 内层关节」两层构成。
2. **色相对比**：主甲色 vs 副甲色 vs 金色饰条 vs 发光核心。
   钢铁侠的辨识度就是「深红主甲 + 金饰 + 青白反应堆」这三色关系，不是单色好不好看。
3. **清漆层**：锐利高光 + 菲涅尔边缘反射。装甲是**漆面金属**，
   比纯金属多了镜面清漆的高光点，比塑料多了环境反射 —— 这是 coat 参数的职责。

皮肤键名沿用 pet_avatar.py 的取值（用户设置不必迁移），但每套皮肤都升级为装甲四色：
shell / shell2 / trim / joint / glow。
"""


def _c(r, g, b):
    """0-255 → 0-1 三通道。"""
    return (r / 255.0, g / 255.0, b / 255.0)


# —— 装甲配色表 ——
# 每套：shell 主甲 / shell2 副甲 / trim 饰条（金）/ joint 关节（深）/ glow 发光
SKINS = {
    # 钢铁侠式：深红主甲 + 金饰 + 青白方舟反应堆
    "iron": {
        "shell":  _c(206, 38, 44),      # 亮红漆面主甲（钢铁侠 Mark 的经典红）
        "shell2": _c(206, 152, 52),     # 金副甲（胸板/肩甲/面甲）
        "trim":   _c(240, 198, 92),     # 亮金饰条
        "joint":  _c(28, 29, 34),       # 深灰关节
        "glow":   _c(150, 232, 255),    # 青白反应堆
        "rim":    _c(255, 214, 140),
    },
    # 帝皇铠甲式：亮金主甲 + 黑副甲 + 金橙发光
    "royal": {
        "shell":  _c(210, 168, 70),     # 亮金主甲（帝皇铠甲的金）
        "shell2": _c(152, 116, 46),     # 暗金副甲（与主甲同色系、低一档明度）
        "trim":   _c(248, 224, 158),    # 白金饰条
        "joint":  _c(58, 44, 26),       # 暗金关节
        "glow":   _c(255, 202, 74),     # 金橙发光
        "rim":    _c(255, 236, 178),
    },
    # 未来科技蓝：钢蓝主甲 + 银副甲 + 金饰 + 青光
    "tech": {
        "shell":  _c(34, 58, 122),      # 钢蓝主甲
        "shell2": _c(150, 172, 196),    # 银灰副甲
        "trim":   _c(210, 168, 74),     # 金饰条
        "joint":  _c(24, 28, 38),       # 深钢关节
        "glow":   _c(96, 216, 255),     # 青光
        "rim":    _c(158, 226, 255),
    },
    # 柔粉机甲：玫红主甲 + 白粉副甲 + 淡金饰
    "cute": {
        "shell":  _c(198, 52, 104),     # 玫红主甲
        "shell2": _c(246, 224, 230),    # 白粉副甲
        "trim":   _c(238, 196, 122),    # 淡金饰条
        "joint":  _c(52, 34, 48),       # 深紫关节
        "glow":   _c(255, 138, 186),    # 粉光
        "rim":    _c(255, 186, 214),
    },
}

DEFAULT_SKIN = "royal"

# 情绪 → 附加到「边缘光 / 自发光」上的色调偏移。
# 只改氛围光不改机体本色 —— 与 2D 版"生气时保留机体本色，只靠氛围光变色"一致。
MOOD_TINT = {
    "calm":      (0.00, 0.00, 0.00),
    "joy":       (0.05, 0.10, 0.02),
    "proud":     (0.08, 0.06, 0.00),
    "curious":   (0.00, 0.06, 0.10),
    "shy":       (0.12, -0.02, 0.06),
    "aggrieved": (-0.06, -0.04, 0.06),
    "angry":     (0.16, -0.06, -0.06),
    "sleepy":    (-0.02, -0.02, 0.04),
}


def _clamp01(v):
    return 0.0 if v < 0.0 else (1.0 if v > 1.0 else v)


def _mix(a, b, t):
    return (a[0] + (b[0] - a[0]) * t,
            a[1] + (b[1] - a[1]) * t,
            a[2] + (b[2] - a[2]) * t)


def _scale(c, k):
    return (_clamp01(c[0] * k), _clamp01(c[1] * k), _clamp01(c[2] * k))


def _diy_pal(pal):
    """v0.30.11 #12 形象 DIY：把「色相/饱和/明度」施加到整套装甲色。

    2D 与 3D **共用** pet_tuning.transform_rgb，两条路径颜色一致。
    默认单位变换 → 直接返回原字典（零开销、零回归）；读不到 pet_tuning 也不报错。
    """
    try:
        import pet_tuning as _PT
        if _PT.look_is_identity():
            return pal
        out = {}
        for k, val in pal.items():
            if isinstance(val, (tuple, list)) and len(val) == 3:
                out[k] = tuple(_PT.transform_rgb(val))
            else:
                out[k] = val
        return out
    except Exception:                       # noqa: BLE001
        return pal


class Palette(object):
    """一帧渲染所需的全部颜色 + 材质参数。

    成熟度 maturity（0=幼儿 1=青年）会：
      · 把主甲漆面压深一档（长大 = 更沉稳的机漆）
      · 提高金属度、降低粗糙度（越长越像抛光过的装甲）
      · 提高清漆强度（青年期的装甲读起来更"厚"）
    """

    __slots__ = ("shell", "shell2", "shell_dark", "joint", "trim", "visor",
                 "emissive", "rim", "accent", "ear", "metallic", "rough",
                 "coat", "glow")

    def __init__(self, skin="royal", growth=1, expr="calm", valence=0.0):
        pal = SKINS.get(skin) or SKINS[DEFAULT_SKIN]
        pal = _diy_pal(pal)                 # v0.30.11 #12 形象 DIY 配色
        m = _clamp01(growth / 3.0)

        # 主甲：漆面。**不要再额外压暗** —— 底色一压，整机就成暗红砖块，
        # 清漆这层提亮也救不回来（漆面高光只作用在掠射与高光斑上）。
        self.shell = _mix(pal["shell"], _scale(pal["shell"], 0.70), 0.34 * m)
        # 副甲：亮色块（胸板/肩甲/面甲），青年期更亮更"贵气"
        self.shell2 = _mix(pal["shell2"], _scale(pal["shell2"], 1.12), 0.35 * m)
        self.shell_dark = _mix(pal["shell"], pal["joint"], 0.62)
        self.joint = _mix(pal["joint"], (0.04, 0.05, 0.07), 0.30)
        # 金饰条：几乎不随成长变化 —— 它是整套装甲的"锚点色"
        self.trim = pal["trim"]
        self.accent = pal["trim"]
        self.ear = pal["shell2"]

        # 面罩 / 发光：情绪只调亮度不改色相
        v = _clamp01((valence + 1.0) * 0.5)
        self.visor = _mix(pal["glow"], pal["trim"], 0.10 + 0.22 * (1.0 - v))
        self.emissive = self.visor

        tint = MOOD_TINT.get(expr, (0.0, 0.0, 0.0))
        self.rim = (_clamp01(pal["rim"][0] + tint[0]),
                    _clamp01(pal["rim"][1] + tint[1]),
                    _clamp01(pal["rim"][2] + tint[2]))

        # 漆面金属：随成长更亮更滑
        self.metallic = 0.52 + 0.26 * m
        self.rough = 0.34 - 0.12 * m
        self.coat = 0.70 + 0.30 * m
        self.glow = 0.70 + 0.30 * m


# —— 部件材质档（渲染器据此选 shader 参数）——
MAT_SHELL = "shell"        # 主装甲：漆面金属
MAT_SHELL2 = "shell2"      # 副装甲：亮色块（胸板/肩甲/面甲）
MAT_JOINT = "joint"        # 关节：深色哑光（装甲缝的来源）
MAT_TRIM = "trim"          # 金色饰条：镜面金属
MAT_VISOR = "visor"        # 面罩 / 眼：强自发光
MAT_GLOW = "glow"          # 反应堆 / 发光点：纯发光
MAT_ACCENT = "accent"      # 强调件（沿用，等同 TRIM）
MAT_EAR = "ear"            # 科技耳
MAT_DARK = "dark"          # 深色内衬（露出的内构）

# 材质名 → (基色名, 自发光名, 金属度, 粗糙度, 边缘光系数, 清漆强度)
# 清漆强度 = 0 表示这一档没有漆面高光（关节是哑光、发光件不需要）
#
# ⚠️ 这里的名字只能指向 Palette 的**颜色**字段（三元组），不能指向标量字段。
# Palette 里同时有颜色（shell/shell2/joint/trim/visor/rim）和标量（metallic/
# rough/coat/glow），二者共用同一个命名空间 —— 早先把 "glow" 当颜色名用过，
# resolve 返回了 float，渲染直接崩。_color_of 负责兜住这类错误。
_TABLE = {
    MAT_SHELL:  ("shell", None,    1.00, 1.00, 0.62, 1.00),
    MAT_SHELL2: ("shell2", None,   1.00, 0.88, 0.80, 1.05),
    MAT_JOINT:  ("joint", None,    0.55, 1.55, 0.30, 0.00),   # 哑光、更粗糙
    MAT_TRIM:   ("trim", None,     1.12, 0.42, 1.10, 1.30),   # 镜面金
    MAT_VISOR:  ("joint", "visor", 0.60, 0.22, 1.35, 0.55),   # 同上：靠自发光发亮
    MAT_GLOW:   ("joint", "visor", 0.00, 0.30, 1.60, 0.00),   # 基色暗+自发光亮=不过曝
    MAT_ACCENT: ("trim", None,     1.10, 0.48, 1.00, 1.25),
    MAT_EAR:    ("shell2", None,   0.90, 0.30, 1.15, 0.90),
    MAT_DARK:   ("joint", None,    0.62, 1.15, 0.40, 0.00),
}


def _color_of(pal, name, fallback):
    """安全取色：名字指向标量时退回 fallback，绝不让 resolve 吐出非三元组。

    渲染器对颜色做 float(t[0]) 解包，拿到标量会直接抛 TypeError ——
    而 renderer.render() 的降级保护会把这个异常转成"渲染不可用"，
    表现为桌面上的小人**静默变回 2D**，排查成本极高。所以在这里挡住。
    """
    if not name:
        return (0.0, 0.0, 0.0)
    v = getattr(pal, name, None)
    if isinstance(v, (tuple, list)) and len(v) >= 3:
        return (v[0], v[1], v[2])
    return fallback


def resolve(pal, mat):
    """把材质名解析成渲染器要的 uniform 组。

    返回 (base, emissive, metallic, rough, rim_k, coat)。
    metallic / rough / rim_k / coat 都是**相对系数**，最后与 Palette 的
    整体金属度、粗糙度相乘 —— 这样"成长带来的抛光感"能自动作用于所有部件，
    而单件材质只负责它相对其他件的差异。
    """
    row = _TABLE.get(mat)
    if row is None:
        row = _TABLE[MAT_SHELL]
    base_name, emis_name, mk, rk, rimk, coatk = row
    base = _color_of(pal, base_name, pal.shell)
    emis = _color_of(pal, emis_name, (0.0, 0.0, 0.0))
    metal = _clamp01(pal.metallic * mk)
    rough = _clamp01(pal.rough * rk)
    coat = _clamp01(pal.coat * coatk) if coatk > 0.0 else 0.0
    return (base, emis, metal, rough, rimk, coat)
