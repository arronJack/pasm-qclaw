# -*- coding: utf-8 -*-
"""角色骨骼与装配 —— 把 4 档成长参数翻译成一副可动的人形骨架。

骨架（18 根骨骼，标准人形）：

    root ─ hip ─┬─ spine ─ chest ─┬─ neck ─ head ─┬─ earL / earR / antenna
                │                 ├─ upperArmL ─ lowerArmL ─ handL
                │                 └─ upperArmR ─ lowerArmR ─ handR
                ├─ thighL ─ shinL ─ footL
                └─ thighR ─ shinR ─ footR

坐标约定：脚底 y = 0，角色面向 +Z，左半身取负 X。
成长档位（0幼儿 → 3青年）驱动的是**比例**：头身比、四肢长短、装甲件数。
"""

import math

from . import mat as MAT
from . import mesh as MESH
from . import m4

# —— 4 档体型参数 ——
# head/limb 等是该部位的特征尺寸；armor = 是否长出击肩甲（青年期解锁）
# v0.30.3：头部整体放大（尤其青年期 0.095→0.126），让面甲/眼缝/冠角细节可见；
# 成长方向本就是"躯干长高、四肢变长"，符合"长高长大"。
PROFILES = {
    0: dict(head=0.172, neck=0.028, torso_h=0.230, torso_w=0.208, torso_d=0.170,
            up_arm=0.104, lo_arm=0.098, thigh=0.132, shin=0.118,
            arm_r=0.060, leg_r=0.064, antenna=0.080, armor=0, core=0.038,
            foot_h=0.066, foot_l=0.118, visor_h=0.62, ear_l=0.100),
    1: dict(head=0.152, neck=0.033, torso_h=0.278, torso_w=0.212, torso_d=0.174,
            up_arm=0.134, lo_arm=0.126, thigh=0.170, shin=0.156,
            arm_r=0.062, leg_r=0.067, antenna=0.110, armor=1, core=0.040,
            foot_h=0.072, foot_l=0.126, visor_h=0.58, ear_l=0.106),
    2: dict(head=0.138, neck=0.038, torso_h=0.320, torso_w=0.216, torso_d=0.178,
            up_arm=0.162, lo_arm=0.152, thigh=0.212, shin=0.198,
            arm_r=0.065, leg_r=0.071, antenna=0.140, armor=2, core=0.042,
            foot_h=0.078, foot_l=0.134, visor_h=0.54, ear_l=0.112),
    3: dict(head=0.126, neck=0.043, torso_h=0.362, torso_w=0.220, torso_d=0.182,
            up_arm=0.190, lo_arm=0.178, thigh=0.248, shin=0.234,
            arm_r=0.068, leg_r=0.075, antenna=0.170, armor=3, core=0.044,
            foot_h=0.084, foot_l=0.142, visor_h=0.50, ear_l=0.118),
    4: dict(head=0.108, neck=0.050, torso_h=0.452, torso_w=0.228, torso_d=0.190,
            up_arm=0.226, lo_arm=0.214, thigh=0.300, shin=0.286,
            arm_r=0.072, leg_r=0.081, antenna=0.190, armor=4, core=0.048,
            foot_h=0.094, foot_l=0.156, visor_h=0.48, ear_l=0.124),
}

_DEFAULT = PROFILES[1]

_LIMB_SEG = 20      # 四肢圆周分段（小尺寸下够了，省顶点）
_LIMB_CAP = 4
_BODY_SEG = 26
_BODY_RING = 15


def profile_for(growth):
    g = int(growth)
    if g < 0:
        g = 0
    if g > 4:
        g = 4
    return _DEFAULT if g not in PROFILES else PROFILES[g]


class Bone(object):
    __slots__ = ("name", "parent", "pos", "rot", "scl", "off", "world", "children")

    def __init__(self, name, parent=None, pos=(0.0, 0.0, 0.0)):
        self.name = name
        self.parent = parent
        self.pos = pos                 # 静止位置（骨骼长度偏移）
        self.rot = [0.0, 0.0, 0.0]     # 运行时关节角
        self.scl = (1.0, 1.0, 1.0)
        self.off = [0.0, 0.0, 0.0]     # 运行时额外位移
        self.world = m4.ident()
        self.children = []

    def local(self):
        return m4.compose(
            (self.pos[0] + self.off[0], self.pos[1] + self.off[1], self.pos[2] + self.off[2]),
            (self.rot[0], self.rot[1], self.rot[2]),
            self.scl)

    def reset(self):
        self.rot[0] = self.rot[1] = self.rot[2] = 0.0
        self.off[0] = self.off[1] = self.off[2] = 0.0


class Part(object):
    """挂在某根骨骼上的一个几何体。"""
    __slots__ = ("name", "mesh", "bone", "off", "scl", "rot", "mat", "visible")

    def __init__(self, name, mesh, bone, off=(0.0, 0.0, 0.0), scl=(1.0, 1.0, 1.0),
                 rot=(0.0, 0.0, 0.0), mat=MAT.MAT_SHELL, visible=True):
        self.name = name
        self.mesh = mesh
        self.bone = bone
        self.off = off
        self.scl = scl
        self.rot = rot
        self.mat = mat
        self.visible = visible

    def model(self, bones):
        b = bones[self.bone]
        local = m4.compose(self.off, self.rot, self.scl)
        return m4.mul(b.world, local)


class Rig(object):
    """一副装配好的角色。构造时按体型档位生成全部几何。"""

    def __init__(self, growth=1, hide=()):
        self.profile = profile_for(growth)
        self.growth = int(growth)
        #: v0.30.11 #12 形象 DIY：要**不装配**的部件组（"ears" / "antenna"）。
        #: 3D 里"隐藏"只能靠重建时不加这些部件 —— 骨骼照旧保留（不影响姿态）。
        self.hide = frozenset(hide or ())
        self.bones = {}
        self.order = []
        self.parts = []
        self._build_bones()
        self._build_parts()
        self.update()
        # 静立姿态下的包围盒：相机取景以它为基准，这样挥手/跳跃时
        # 画面不会跟着缩放或漂移（动态 AABB 会让小人"一边动一边晃"）。
        self.base_bounds = self.bounds()

    # ---------- 骨架 ----------
    def _bone(self, name, parent=None, pos=(0.0, 0.0, 0.0)):
        b = Bone(name, parent, pos)
        self.bones[name] = b
        self.order.append(name)
        if parent is not None:
            self.bones[parent].children.append(name)
        return b

    def _build_bones(self):
        P = self.profile
        # 踝高取 foot_h*0.78：脚中心在 foot_h*0.5，脚部件偏移 -foot_h*0.28，
        # 两者相减正好让脚底落在 y=0（否则小人在桌面上会陷进地面）。
        ankle = P["foot_h"] * 0.78
        knee = ankle + P["shin"]
        hip = knee + P["thigh"]
        self.hip_y = hip

        self._bone("root")
        self._bone("hip", "root", (0.0, hip, 0.0))
        self._bone("spine", "hip", (0.0, 0.010, 0.0))
        self._bone("chest", "spine", (0.0, P["torso_h"] * 0.46, 0.0))
        self._bone("neck", "chest", (0.0, P["torso_h"] * 0.50, 0.0))
        self._bone("head", "neck", (0.0, P["neck"], 0.0))
        self._bone("antenna", "head", (0.0, P["head"] * 1.66, -P["head"] * 0.30))

        for side, sx in (("L", -1.0), ("R", 1.0)):
            self._bone("ear" + side, "head",
                       (sx * P["head"] * 0.88, P["head"] * 0.06, -P["head"] * 0.06))
            # 肩关节只比躯干半宽外移一点（原来 *0.55 会让肩甲整个悬在躯干外）
            ax = sx * (P["torso_w"] * 0.50 + P["arm_r"] * 0.32)
            self._bone("upperArm" + side, "chest", (ax, P["torso_h"] * 0.30, 0.0))
            self._bone("lowerArm" + side, "upperArm" + side, (0.0, -P["up_arm"], 0.0))
            self._bone("hand" + side, "lowerArm" + side, (0.0, -P["lo_arm"], 0.0))
            # 腿间距按腿粗定（原来用 torso_w*0.26，粗腿会在大腿处互相穿插）
            lx = sx * (P["leg_r"] * 1.30)
            self._bone("thigh" + side, "hip", (lx, 0.0, 0.0))
            self._bone("shin" + side, "thigh" + side, (0.0, -P["thigh"], 0.0))
            self._bone("foot" + side, "shin" + side, (0.0, -P["shin"], 0.0))

        self.update()

    # ---------- 几何装配 ----------
    def _build_parts(self):
        """帝皇铠甲式装配 —— 大块流线曲面 + 三个标志性元素。

        与上一版的关键差别是**从「分片」走向「流线」**：
          · 胸甲合成一整块（原来是左右两片 + 中缝金条，读起来是"两块板"）
          · 加入**裙甲** —— 铠甲类的灵魂：腰下四片向外张开的垂甲
          · **肩甲加大外张** —— 帝皇铠甲最标志的宽肩弧罩
          · 头顶加冠与双角、肘膝加尖、小腿加鳍 —— 轮廓锐化
          · 部件数减少、单件面积增大 —— 碎片化本身就是"丑"的主因

        每个部位仍是「外层甲片 + 深色内构」两层，缝里露出机械内构；
        但缝要**窄**，宽缝会把形体读成"断开的几截"。
        """
        P = self.profile
        hd = P["head"]
        T = P["torso_h"]
        W = P["torso_w"]
        D = P["torso_d"]
        ar = P["arm_r"]
        lr = P["leg_r"]
        armor = int(P.get("armor", 1))
        sq = D / max(1e-6, W)          # 躯干宽深比：椭圆截面甲片要用它压扁
        add = self.parts.append

        SH, SH2, JT = MAT.MAT_SHELL, MAT.MAT_SHELL2, MAT.MAT_JOINT
        TR, VI, GL, DK = MAT.MAT_TRIM, MAT.MAT_VISOR, MAT.MAT_GLOW, MAT.MAT_DARK

        # 弧面甲片角度助手：角色面向 +Z（a=90°），+X 是角色右侧、-X 是左侧
        PW = math.pi * 0.5

        def front(half):
            """正面朝向的整段（以 +Z 为中心）。"""
            return (PW - half, PW + half)

        def half_front(sx, inner, outer):
            """前胸左右半片：sx=+1 取 +X 侧，sx=-1 取 -X 侧。"""
            if sx > 0:
                return (PW - outer, PW - inner)
            return (PW + inner, PW + outer)

        def side(sx, half):
            """四肢的外侧包覆段：右肢以 a=0 为中心，左肢以 a=180° 为中心。"""
            c = 0.0 if sx > 0 else math.pi
            return (c - half, c + half)

        def spike_rot(sx, out, back=0.0):
            """尖刺的旋转：+Y 起、向外倾 out 弧度、向后倾 back 弧度。

            绕 Z 轴转 θ 会把 +Y 转向 (-sinθ, cosθ) —— 所以右肢（+X）
            要用**负**角度才能朝外，这点反直觉，写错过一次。
            """
            return (back, 0.0, sx * -out)

        # ══════════════ 头：流线头盔 + 暗色面甲 + 整条眼缝 + 冠与双角 ══════════════
        # 头盔：e=0.78 比 0.70 圆润 —— 头是全身唯一该"圆"的部位，圆才显优雅
        add(Part("helmet",
                 MESH.superellipsoid(hd * 1.00, hd * 0.96, hd * 0.99, 0.78, 0.78, 28, 18),
                 "head", (0.0, hd * 0.94, -hd * 0.02), mat=SH))
        # 面甲：暗色（黑曜），嵌在头盔前 —— 亮头盔 + 暗面甲才是铠甲的对比语法。
        # （此前是"亮头盔 + 亮面甲"，两块同色亮面直接糊成一团。）
        add(Part("faceplate",
                 MESH.superellipsoid(hd * 0.76, hd * 0.66, hd * 0.52, 0.62, 0.62, 26, 16),
                 "head", (0.0, hd * 0.96, hd * 0.46), mat=JT))
        # 眼缝：**一整条**横向发光带（原来左右两片小方块，读成"两只眼睛"，
        # 铠甲的眼部是一条光缝）。z 必须超出面甲表面，否则被包住看不见。
        add(Part("visor",
                 MESH.bevel_plate(hd * 1.14, hd * 0.125, hd * 0.20, 0.26, 26, 12),
                 "head", (0.0, hd * 1.10, hd * 0.92), mat=VI))
        # 额心宝石
        add(Part("brow_gem", MESH.sphere(hd * 0.13, 14, 10), "head",
                 (0.0, hd * 1.40, hd * 0.66), mat=GL))
        # 头顶冠（前后的脊）—— 帝皇冠的意味
        add(Part("crown",
                 MESH.fin(hd * 0.86, hd * 0.42, hd * 0.18, 0.20),
                 "head", (0.0, hd * 1.66, -hd * 0.06), rot=(-0.18, 0.0, 0.0), mat=TR))
        # 双角：向后外侧张开，头部的"锐利轮廓"来源
        # （v0.30.11 #12：可由「形象 DIY → 头饰（耳/角）」关掉）
        if "ears" not in self.hide:
            for side_n, sx in (("L", -1.0), ("R", 1.0)):
                add(Part("horn" + side_n, MESH.spike(hd * 0.105, hd * 0.92, 14),
                         "head", (sx * hd * 0.54, hd * 1.42, -hd * 0.14),
                         rot=spike_rot(sx, 0.72, 0.30), mat=TR))
        # 颈：短、被头盔与锁骨甲夹住
        add(Part("neck", MESH.cylinder(hd * 0.34, hd * 0.32, P["neck"] * 1.4, 14),
                 "neck", (0.0, -P["neck"] * 0.34, 0.0), mat=JT))

        # ══════════════ 躯干：深色内构 + 整块胸甲 + 徽记 + 腹甲 + 裙甲 ══════════════
        add(Part("torso_inner",
                 MESH.superellipsoid(W * 0.400, T * 0.440, D * 0.400, 0.62, 0.62, 24, 14),
                 "chest", (0.0, T * 0.02, 0.0), mat=DK))
        # 胸甲：**一整块**流线弧面（覆盖 186°，几乎整个前半）。
        # 半径走 0.446→0.532→0.512→0.376 的外凸曲线 —— 这就是"流线"的来源。
        add(Part("chest_plate",
                 MESH.shell([(-T * 0.26, W * 0.446), (T * 0.00, W * 0.532),
                             (T * 0.26, W * 0.512), (T * 0.44, W * 0.376)],
                            *front(1.62), seg=30, thick=W * 0.056, squash=sq),
                 "chest", (0.0, 0.0, 0.0), mat=SH))
        # 胸肌隆起：叠在胸甲上，给胸口体积（左右各一，但不切断主甲）
        for side_n, sx in (("L", -1.0), ("R", 1.0)):
            a0, a1 = half_front(sx, 0.16, 1.14)
            add(Part("pec" + side_n,
                     MESH.shell([(T * 0.04, W * 0.552), (T * 0.28, W * 0.574),
                                 (T * 0.40, W * 0.522)],
                                a0, a1, seg=18, thick=W * 0.050, squash=sq),
                     "chest", (0.0, 0.0, 0.0), mat=TR))
        # 胸口徽记：金环 + 发光核心（占满胸甲中央，不做小碎钻）
        add(Part("crest_ring", MESH.torus(W * 0.170, W * 0.036, 28, 8), "chest",
                 (0.0, T * 0.20, D * 0.566), rot=(math.pi * 0.5, 0.0, 0.0), mat=TR))
        add(Part("crest_core", MESH.sphere(W * 0.098, 22, 14), "chest",
                 (0.0, T * 0.20, D * 0.576), mat=GL))
        # 腹甲：一整块流线（向下收窄），与胸甲之间只留一道窄缝
        add(Part("abdomen",
                 MESH.shell([(-T * 0.360, W * 0.404), (-T * 0.150, W * 0.472),
                             (-T * 0.020, W * 0.496)],
                            *front(1.46), seg=26, thick=W * 0.050, squash=sq),
                 "chest", (0.0, 0.0, 0.0), mat=SH2))
        # 细腰（深色）：机甲要"宽肩细腰"，腰环必须比胸窄一档
        add(Part("waist_band",
                 MESH.shell([(-T * 0.462, W * 0.330), (-T * 0.372, W * 0.356)],
                            *(-math.pi, math.pi), seg=28, thick=W * 0.058, squash=sq),
                 "chest", (0.0, 0.0, 0.0), mat=JT))
        # ★ 裙甲：四片向下外张的垂甲 —— 铠甲类最标志的部件。
        # 半径向下由 0.34W 张到 0.50W，形成"裙摆"，同时盖住大腿根。
        for nm, ac in (("skirtF", math.pi * 0.5), ("skirtB", -math.pi * 0.5),
                       ("skirtR", 0.0), ("skirtL", math.pi)):
            add(Part(nm,
                     MESH.shell([(-T * 0.010, W * 0.342), (-T * 0.150, W * 0.430),
                                 (-T * 0.280, W * 0.500)],
                                ac - 0.52, ac + 0.52, seg=18, thick=W * 0.048, squash=sq),
                     "hip", (0.0, 0.0, 0.0), mat=SH))
        if armor >= 2:
            # 背鳍：向后上方的两片尖鳍，给侧面轮廓"锐度"
            for side_n, sx in (("L", -1.0), ("R", 1.0)):
                add(Part("backSpike" + side_n, MESH.spike(hd * 0.13, hd * 1.5, 12),
                         "chest", (sx * W * 0.26, T * 0.30, -D * 0.44),
                         rot=spike_rot(sx, 0.42, -0.62), mat=TR))
        if armor >= 3:
            add(Part("backpack",
                     MESH.superellipsoid(W * 0.290, T * 0.280, D * 0.130, 0.50, 0.50, 20, 12),
                     "chest", (0.0, T * 0.18, -D * 0.460), mat=SH))
            # v0.30.11 #12：天线可由「形象 DIY → 全息天线」关掉
            if "antenna" not in self.hide:
                add(Part("antenna_stem",
                         MESH.cylinder(hd * 0.042, hd * 0.024,
                                       P["antenna"] * 0.55, 12),
                         "antenna", (0.0, 0.0, 0.0), mat=JT))
                add(Part("antenna_tip", MESH.sphere(hd * 0.075, 16, 10), "antenna",
                         (0.0, P["antenna"] * 0.55, 0.0), mat=GL))

        # ══════════════ 四肢 ══════════════
        for side_n, sx in (("L", -1.0), ("R", 1.0)):
            # —— 肩：★ 宽大外张的弧罩（帝皇铠甲最标志的元素）——
            add(Part("shoulderJoint" + side_n, MESH.sphere(ar * 0.92, 16, 10),
                     "upperArm" + side_n, (0.0, 0.0, 0.0), mat=JT))
            # 半径由 1.24ar 张到 1.86ar 再收到 1.40ar —— 上宽下收的"披肩"
            add(Part("pauldron" + side_n,
                     MESH.shell([(-ar * 1.10, ar * 1.24), (ar * 0.06, ar * 1.86),
                                 (ar * 0.82, ar * 1.40)],
                                *side(sx, 2.10), seg=22, thick=ar * 0.22),
                     "upperArm" + side_n, (0.0, 0.0, 0.0), mat=SH))
            add(Part("pauldronTip" + side_n, MESH.spike(ar * 0.30, ar * 0.86, 14),
                     "upperArm" + side_n, (sx * ar * 1.58, ar * 0.36, 0.0),
                     rot=spike_rot(sx, 1.05, 0.0), mat=TR))
            # —— 上臂：一整块甲，下端一直包到肘 ——
            add(Part("upperArmInner" + side_n,
                     MESH.capsule(ar * 0.86, P["up_arm"] * 0.60, 16, 4),
                     "upperArm" + side_n, (0.0, -P["up_arm"] * 0.50, 0.0), mat=JT))
            add(Part("bicepPlate" + side_n,
                     MESH.shell([(-P["up_arm"] * 0.96, ar * 1.10),
                                 (-P["up_arm"] * 0.50, ar * 1.32),
                                 (-P["up_arm"] * 0.02, ar * 1.26)],
                                *side(sx, 1.85), seg=16, thick=ar * 0.20),
                     "upperArm" + side_n, (0.0, 0.0, 0.0), mat=SH))
            # —— 肘 / 前臂 ——
            add(Part("elbowJoint" + side_n, MESH.sphere(ar * 0.88, 16, 10),
                     "lowerArm" + side_n, (0.0, 0.0, 0.0), mat=JT))
            add(Part("lowerArmInner" + side_n,
                     MESH.capsule(ar * 0.80, P["lo_arm"] * 0.56, 16, 4),
                     "lowerArm" + side_n, (0.0, -P["lo_arm"] * 0.50, 0.0), mat=JT))
            add(Part("forearmPlate" + side_n,
                     MESH.shell([(-P["lo_arm"] * 0.94, ar * 1.10),
                                 (-P["lo_arm"] * 0.50, ar * 1.30),
                                 (-P["lo_arm"] * 0.02, ar * 1.22)],
                                *side(sx, 1.90), seg=16, thick=ar * 0.19),
                     "lowerArm" + side_n, (0.0, 0.0, 0.0), mat=SH))
            add(Part("elbowSpike" + side_n, MESH.spike(ar * 0.22, ar * 0.66, 12),
                     "lowerArm" + side_n, (sx * ar * 1.02, -ar * 0.34, -ar * 0.42),
                     rot=spike_rot(sx, 1.15, -0.55), mat=TR))
            if armor >= 2:
                add(Part("wristRing" + side_n, MESH.torus(ar * 1.20, ar * 0.145, 22, 8),
                         "lowerArm" + side_n, (0.0, -P["lo_arm"] * 0.90, 0.0),
                         rot=(math.pi * 0.5, 0.0, 0.0), mat=GL))
            # —— 手：流线手套 + 掌心发光 ——
            add(Part("palmCore" + side_n, MESH.sphere(ar * 0.36, 14, 10),
                     "hand" + side_n, (0.0, -ar * 0.20, ar * 0.54), mat=GL))
            add(Part("glove" + side_n,
                     MESH.superellipsoid(ar * 0.86, ar * 0.94, ar * 0.72, 0.46, 0.46, 20, 14),
                     "hand" + side_n, (0.0, -ar * 0.58, 0.0),
                     mat=SH))

            # —— 腿 ——
            add(Part("hipJoint" + side_n, MESH.sphere(lr * 0.90, 16, 10),
                     "thigh" + side_n, (0.0, 0.0, 0.0), mat=JT))
            add(Part("thighInner" + side_n,
                     MESH.capsule(lr * 0.84, P["thigh"] * 0.66, 16, 4),
                     "thigh" + side_n, (0.0, -P["thigh"] * 0.52, 0.0), mat=JT))
            add(Part("thighPlate" + side_n,
                     MESH.shell([(-P["thigh"] * 0.96, lr * 0.98),
                                 (-P["thigh"] * 0.44, lr * 1.26),
                                 (-P["thigh"] * 0.08, lr * 1.22)],
                                *side(sx, 2.05), seg=16, thick=lr * 0.20),
                     "thigh" + side_n, (0.0, 0.0, 0.0), mat=SH))
            # —— 膝：护膝 + 向前的膝尖（锐化正面轮廓）——
            add(Part("kneeJoint" + side_n, MESH.sphere(lr * 0.86, 16, 10),
                     "shin" + side_n, (0.0, 0.0, 0.0), mat=JT))
            add(Part("kneeGuard" + side_n,
                     MESH.superellipsoid(lr * 0.72, lr * 0.58, lr * 0.80, 0.44, 0.44, 18, 12),
                     "shin" + side_n, (0.0, -lr * 0.14, lr * 0.62), mat=TR))
            add(Part("kneeSpike" + side_n, MESH.spike(lr * 0.24, lr * 0.72, 12),
                     "shin" + side_n, (0.0, -lr * 0.36, lr * 0.92),
                     rot=(-1.05, 0.0, 0.0), mat=TR))
            # —— 小腿：一整块甲（下宽上窄的流线），后侧加鳍 ——
            add(Part("shinInner" + side_n,
                     MESH.capsule(lr * 0.80, P["shin"] * 0.62, 16, 4),
                     "shin" + side_n, (0.0, -P["shin"] * 0.52, 0.0), mat=JT))
            add(Part("shinPlate" + side_n,
                     MESH.shell([(-P["shin"] * 0.94, lr * 0.96),
                                 (-P["shin"] * 0.54, lr * 1.30),
                                 (-P["shin"] * 0.06, lr * 1.24)],
                                *side(sx, 2.10), seg=16, thick=lr * 0.19),
                     "shin" + side_n, (0.0, 0.0, 0.0), mat=SH))
            add(Part("shinFin" + side_n, MESH.fin(lr * 1.80, lr * 1.05, lr * 0.30, 0.45),
                     "shin" + side_n, (0.0, -P["shin"] * 0.60, -lr * 1.04),
                     rot=(-0.24, 0.0, 0.0), mat=TR))
            # —— 靴（底精确贴 y=0）+ 上翘的脚尖 ——
            add(Part("boot" + side_n,
                     MESH.superellipsoid(lr * 0.76, P["foot_h"] * 0.48,
                                         P["foot_l"] * 0.58, 0.44, 0.44, 22, 14),
                     "foot" + side_n, (0.0, -P["foot_h"] * 0.30, P["foot_l"] * 0.28),
                     mat=SH))
            add(Part("toe" + side_n,
                     MESH.superellipsoid(lr * 0.58, P["foot_h"] * 0.26,
                                         P["foot_l"] * 0.30, 0.40, 0.40, 18, 12),
                     "foot" + side_n, (0.0, -P["foot_h"] * 0.44, P["foot_l"] * 0.80),
                     mat=TR if armor >= 3 else SH))

    # ---------- 姿态 ----------
    def reset(self):
        for b in self.bones.values():
            b.reset()
        self.update()

    def set_rot(self, name, rx=0.0, ry=0.0, rz=0.0):
        b = self.bones.get(name)
        if b is not None:
            b.rot[0] = rx
            b.rot[1] = ry
            b.rot[2] = rz

    def set_off(self, name, x=0.0, y=0.0, z=0.0):
        b = self.bones.get(name)
        if b is not None:
            b.off[0] = x
            b.off[1] = y
            b.off[2] = z

    def update(self):
        """按拓扑序刷新世界矩阵（order 已保证父在子前）。"""
        for name in self.order:
            b = self.bones[name]
            if b.parent is None:
                b.world = b.local()
            else:
                b.world = m4.mul(self.bones[b.parent].world, b.local())

    def bone_world(self, name):
        b = self.bones.get(name)
        return b.world if b is not None else m4.ident()

    def bounds(self):
        """所有可见部件变换后的世界 AABB： (xmin,ymin,zmin,xmax,ymax,zmax)。

        用部件的 8 个 AABB 角点做变换（比包围球精确得多，细长的手脚不会被
        高估成一大团）。相机取景靠它，保证任何姿势与档位都完整入画。
        """
        lo = [1e9, 1e9, 1e9]
        hi = [-1e9, -1e9, -1e9]
        for p in self.parts:
            if not p.visible:
                continue
            m = p.model(self.bones)
            a, b = p.mesh.bmin, p.mesh.bmax
            for cx in (a[0], b[0]):
                for cy in (a[1], b[1]):
                    for cz in (a[2], b[2]):
                        wx = m[0] * cx + m[4] * cy + m[8] * cz + m[12]
                        wy = m[1] * cx + m[5] * cy + m[9] * cz + m[13]
                        wz = m[2] * cx + m[6] * cy + m[10] * cz + m[14]
                        if wx < lo[0]: lo[0] = wx
                        if wy < lo[1]: lo[1] = wy
                        if wz < lo[2]: lo[2] = wz
                        if wx > hi[0]: hi[0] = wx
                        if wy > hi[1]: hi[1] = wy
                        if wz > hi[2]: hi[2] = wz
        if lo[0] > hi[0]:
            return (0.0, 0.0, 0.0, 0.0, 1.0, 0.0)
        return (lo[0], lo[1], lo[2], hi[0], hi[1], hi[2])

    def head_world_pos(self):
        return m4.xform_point(self.bones["head"].world, (0.0, 0.0, 0.0))

    def top_y(self):
        """当前姿态下的最高点。"""
        return self.bounds()[4]
