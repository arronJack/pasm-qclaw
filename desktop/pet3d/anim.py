# -*- coding: utf-8 -*-
"""动作库 —— 把「动作名 + 情绪 + 模式」翻译成一组关节角，并做帧间平滑。

两层结构：
  · **target**：算出本帧各关节的"目标角"（纯函数，无状态，好测）
  · **step**：用指数阻尼把当前角拉向目标角（``m4.damp``）——
    这一步是"动作流畅自然"的关键：动作切换时不会瞬间跳变，
    而且阻尼与帧率无关，30fps / 60fps 手感一致。

动作枚举与 2D 版严格一致（v0.30.11 扩充后）：
  wave / hop / peek / ball / dance / spin / think /
  nod / stretch / arms / bounce / fly / cheer / pat / point / heart
（另有 read / sleep / walk / work 四个由模式驱动的持续姿态）。
"""

import math

from . import m4

TAU = math.pi * 2.0

# 参与动画的关节（顺序固定，便于复用缓冲）
ROT_BONES = ("hip", "spine", "chest", "neck", "head", "antenna",
             "upperArmL", "lowerArmL", "handL",
             "upperArmR", "lowerArmR", "handR",
             "thighL", "shinL", "footL",
             "thighR", "shinR", "footR")

# 关节跟随速度：躯干慢、四肢快（这样"抬手"利落、"身体倾"柔和）
RATE = {
    "head": 7.0, "neck": 7.0, "antenna": 9.0,
    "spine": 6.0, "chest": 6.0, "hip": 6.0,
    "upperArmL": 10.0, "lowerArmL": 12.0, "handL": 14.0,
    "upperArmR": 10.0, "lowerArmR": 12.0, "handR": 14.0,
    "thighL": 11.0, "shinL": 13.0, "footL": 12.0,
    "thighR": 11.0, "shinR": 13.0, "footR": 12.0,
}
RATE_ROOT = 8.0
RATE_OFF = 9.0


def _z(v):
    return [float(v), 0.0, 0.0]


class Animator(object):
    """有状态的姿态求解器：每帧 update() 一次。"""

    def __init__(self, rig):
        self.rig = rig
        self.cur = {n: [0.0, 0.0, 0.0] for n in ROT_BONES}
        self.root_rot = [0.0, 0.0, 0.0]
        self.root_off = [0.0, 0.0, 0.0]
        self.t = 0.0
        self._act = ""
        self._act_t0 = 0.0
        self.rate_boost = 1.0

    def set_action(self, act, now=None):
        """切换动作（记录起点以让动作时间从 0 开始，避免中途跳相位）。"""
        act = act or ""
        if act != self._act:
            self._act = act
            self._act_t0 = self.t if now is None else now

    # ---------- 目标姿态 ----------
    def target(self, act, mode, expr, look=0.0, speaking=False, valence=0.0,
               facing=0.0):
        """返回 (关节目标 dict, root_rot, root_off)。

        facing: -1 朝左 / 0 朝前 / +1 朝右。走路的**朝向**由它决定 ——
        比"正面朝前横向平移"自然得多。转身过程交给 `update()` 里既有的
        阻尼插值（root_rot 有 RATE_ROOT 限速），所以天然是"先转再走"。
        """
        t = self.t
        R = {n: [0.0, 0.0, 0.0] for n in ROT_BONES}
        rr = [0.0, 0.0, 0.0]
        ro = [0.0, 0.0, 0.0]

        breathing = math.sin(t * 1.7)
        # 呼吸基底：所有姿态共享，让静止时也"活着"
        R["chest"][0] += breathing * 0.016
        R["spine"][0] += breathing * 0.010
        ro[1] += breathing * 0.0035
        R["antenna"][0] += math.sin(t * 2.3 + 0.6) * 0.085
        R["antenna"][2] += math.sin(t * 1.9) * 0.05
        # 目光跟随
        R["head"][1] += look * 0.42
        R["head"][2] += look * 0.06
        R["neck"][1] += look * 0.16
        # 手臂自然下垂微张
        R["upperArmL"][2] += -0.085
        R["upperArmR"][2] += 0.085
        R["lowerArmL"][0] += -0.06
        R["lowerArmR"][0] += -0.06

        live = act or ""
        if mode == "sleep":
            live = "sleep"
        elif mode == "read":
            live = "read"
        elif mode == "walk" and not live:
            live = "walk"
        elif mode == "work" and not live:
            live = "work"

        at = t - self._act_t0

        # —— 朝向：走路（或明确给了 facing）时整个身体转过去 ——
        # 角度取 1.32 rad（约 76°）：侧身走，但还留一点正面朝向，
        # 用户仍能看到脸和表情（全侧身会像"背对着你走掉"）。
        if facing:
            rr[1] += facing * 1.32

        fn = _ACTS.get(live)
        if fn is not None:
            fn(R, rr, ro, t, at, valence)
            # —— 转身未到位时收小步幅 ——
            # 目标朝向与当前朝向差得多，说明还在转身：这时候迈大步会变成
            # "边转边平移"，正是用户说的别扭感。收小后读起来就是"先转身再走"。
            if live == "walk":
                _d = abs(rr[1] - self.root_rot[1])
                if _d > 0.22:
                    _k = max(0.08, 1.0 - _d / 1.30)
                    for _b in ("thighL", "shinL", "footL",
                               "thighR", "shinR", "footR",
                               "upperArmL", "lowerArmL",
                               "upperArmR", "lowerArmR"):
                        R[_b][0] *= _k
                        R[_b][1] *= _k
                        R[_b][2] *= _k
        else:
            _idle_extra(R, rr, ro, t, valence)

        if speaking:
            # 说话时头部有轻微点头 + 天线活跃（区别于静止）
            R["head"][0] += math.sin(t * 9.0) * 0.045
            R["neck"][0] += math.sin(t * 9.0 - 0.4) * 0.022
            R["antenna"][0] += math.sin(t * 7.0) * 0.10
            R["handL"][0] += math.sin(t * 3.1) * 0.05
            R["handR"][0] += math.sin(t * 3.1 + 1.1) * 0.05

        _expr_layer(R, rr, ro, expr, t)
        return R, rr, ro

    # ---------- 帧推进 ----------
    def update(self, dt, act, mode, expr, look=0.0, speaking=False,
               valence=0.0, facing=0.0):
        if dt <= 0.0:
            dt = 1.0 / 60.0
        if dt > 0.25:
            dt = 0.25                    # 长时间挂起后不要"补帧"，直接跳
        self.t += dt
        self.set_action(act)
        tgt, rr, ro = self.target(act, mode, expr, look, speaking, valence,
                                  facing)

        k = self.rate_boost
        for name in ROT_BONES:
            c = self.cur[name]
            g = tgt[name]
            r = RATE[name] * k
            c[0] = m4.damp(c[0], g[0], r, dt)
            c[1] = m4.damp(c[1], g[1], r, dt)
            c[2] = m4.damp(c[2], g[2], r, dt)

        for i in range(3):
            self.root_rot[i] = m4.damp_angle(self.root_rot[i], rr[i], RATE_ROOT * k, dt) \
                if i == 1 else m4.damp(self.root_rot[i], rr[i], RATE_ROOT * k, dt)
            self.root_off[i] = m4.damp(self.root_off[i], ro[i], RATE_OFF * k, dt)

        self._apply()

    def jump(self, act, mode, expr, look=0.0, speaking=False, valence=0.0,
             facing=0.0):
        """立刻到位（首帧/重建后调用），避免从零姿态"长出来"。"""
        self.set_action(act)
        tgt, rr, ro = self.target(act, mode, expr, look, speaking, valence,
                                  facing)
        for name in ROT_BONES:
            self.cur[name] = list(tgt[name])
        self.root_rot = list(rr)
        self.root_off = list(ro)
        self._apply()          # 必须写回骨骼，否则首帧渲染的是零姿态

    def _apply(self):
        rig = self.rig
        rig.reset()
        rig.set_rot("root", self.root_rot[0], self.root_rot[1], self.root_rot[2])
        rig.set_off("root", self.root_off[0], self.root_off[1], self.root_off[2])
        for name in ROT_BONES:
            c = self.cur[name]
            rig.set_rot(name, c[0], c[1], c[2])
        rig.update()


# ——————————————————————————————————————————————————————
# 各动作（R=关节角表, rr=整体旋转, ro=整体位移, t=总时间, at=动作内时间）
# ——————————————————————————————————————————————————————

def _idle_extra(R, rr, ro, t, v):
    R["hip"][2] += math.sin(t * 1.1) * 0.018
    if v > 0.2:
        ro[1] += abs(math.sin(t * 2.2)) * 0.012


def _act_walk(R, rr, ro, t, at, v):
    p = t * TAU * 1.45
    s, c = math.sin(p), math.cos(p)
    R["thighL"][0] += -s * 0.62
    R["shinL"][0] += max(0.0, math.sin(p + 0.85)) * 0.80
    R["footL"][0] += s * 0.22
    R["thighR"][0] += s * 0.62
    R["shinR"][0] += max(0.0, math.sin(-p + 0.85)) * 0.80
    R["footR"][0] += -s * 0.22
    R["upperArmL"][0] += s * 0.50
    R["upperArmR"][0] += -s * 0.50
    R["lowerArmL"][0] += -0.22 - max(0.0, s) * 0.28
    R["lowerArmR"][0] += -0.22 - max(0.0, -s) * 0.28
    R["spine"][0] += 0.055
    R["head"][0] += -0.030
    ro[1] += abs(math.sin(p)) * 0.022 - 0.010


def _act_wave(R, rr, ro, t, at, v):
    w = math.sin(t * 7.4)
    R["upperArmR"][2] += 2.28
    R["upperArmR"][0] += -0.30 + w * 0.10
    R["lowerArmR"][2] += 0.34 + w * 0.52
    R["lowerArmR"][0] += -0.24
    R["head"][2] += 0.085
    R["head"][0] += -0.045
    rr[2] += -0.035
    ro[1] += abs(math.sin(t * 3.7)) * 0.006


def _act_hop(R, rr, ro, t, at, v):
    p = t * TAU * 1.05
    h = abs(math.sin(p))
    ro[1] += h * 0.135
    sq = math.sin(p * 2.0)
    R["thighL"][0] += -0.38 * (1.0 - h) - 0.16
    R["thighR"][0] += -0.38 * (1.0 - h) - 0.16
    R["shinL"][0] += 0.74 * (1.0 - h)
    R["shinR"][0] += 0.74 * (1.0 - h)
    R["upperArmL"][2] += -0.55 - h * 0.55
    R["upperArmR"][2] += 0.55 + h * 0.55
    R["upperArmL"][0] += -sq * 0.18
    R["upperArmR"][0] += -sq * 0.18
    R["head"][0] += -0.07 * h
    R["antenna"][0] += -sq * 0.24


def _act_peek(R, rr, ro, t, at, v):
    e = m4.ease_out(min(1.0, at / 0.45), 2.4)
    swing = math.sin(t * 1.5) * 0.05
    R["head"][2] += (0.30 + swing) * e
    R["head"][1] += (-0.34 + swing * 0.6) * e
    R["head"][0] += -0.05 * e
    R["spine"][2] += 0.11 * e
    R["chest"][2] += 0.07 * e
    rr[2] += 0.05 * e
    ro[0] += -0.012 * e
    R["upperArmL"][2] += -0.20 * e
    R["upperArmR"][2] += 0.16 * e


def _act_ball(R, rr, ro, t, at, v):
    b = abs(math.sin(t * TAU * 0.75))
    ro[1] += b * 0.028
    R["upperArmL"][0] += -1.16
    R["upperArmR"][0] += -1.16
    R["upperArmL"][2] += -0.42
    R["upperArmR"][2] += 0.42
    R["lowerArmL"][0] += -1.02 - b * 0.12
    R["lowerArmR"][0] += -1.02 - b * 0.12
    R["lowerArmL"][2] += -0.18
    R["lowerArmR"][2] += 0.18
    R["spine"][0] += 0.11
    R["head"][0] += 0.14 - b * 0.06


def _act_dance(R, rr, ro, t, at, v):
    p = t * TAU * 1.15
    s = math.sin(p)
    rr[2] += s * 0.15
    rr[1] += math.sin(p * 0.5) * 0.22
    ro[1] += abs(math.sin(p * 2.0)) * 0.030
    R["hip"][2] += s * 0.10
    R["spine"][2] += s * 0.08
    R["head"][2] += s * 0.10
    R["head"][0] += math.sin(p * 2.0) * 0.09
    R["upperArmL"][2] += -1.30 - s * 0.62
    R["upperArmR"][2] += 1.30 - s * 0.62
    R["upperArmL"][0] += -0.28 - s * 0.30
    R["upperArmR"][0] += -0.28 + s * 0.30
    R["lowerArmL"][2] += -0.34 + s * 0.34
    R["lowerArmR"][2] += 0.34 + s * 0.34
    R["antenna"][0] += -s * 0.26


def _act_spin(R, rr, ro, t, at, v):
    rr[1] += at * TAU * 0.95
    R["upperArmL"][2] += -1.12
    R["upperArmR"][2] += 1.12
    R["upperArmL"][0] += -0.20
    R["upperArmR"][0] += -0.20
    rr[2] += math.sin(at * TAU * 0.95) * 0.045
    ro[1] += abs(math.sin(at * TAU * 0.95 * 2)) * 0.018


def _act_think(R, rr, ro, t, at, v):
    br = math.sin(t * 0.9) * 0.03
    R["upperArmR"][0] += -0.72
    R["upperArmR"][2] += 1.02 + br * 0.4
    R["lowerArmR"][0] += -0.62
    R["lowerArmR"][2] += 1.62
    R["handR"][0] += -0.30
    R["head"][0] += 0.16
    R["head"][2] += -0.10
    R["spine"][0] += 0.07
    R["upperArmL"][0] += 0.10
    R["upperArmL"][2] += -0.30
    R["lowerArmL"][0] += -0.55


def _act_read(R, rr, ro, t, at, v):
    br = math.sin(t * 1.3) * 0.012
    ro[1] += br
    R["upperArmL"][0] += -1.00
    R["upperArmR"][0] += -1.00
    R["upperArmL"][2] += -0.30
    R["upperArmR"][2] += 0.30
    R["lowerArmL"][0] += -0.86
    R["lowerArmR"][0] += -0.86
    R["head"][0] += 0.26
    R["spine"][0] += 0.07
    R["antenna"][0] += -0.12


def _act_sleep(R, rr, ro, t, at, v):
    br = math.sin(t * 0.72)
    ro[1] += br * 0.010 - 0.012
    R["head"][0] += 0.46 + br * 0.02
    R["head"][2] += 0.20
    R["spine"][0] += 0.11
    R["hip"][0] += 0.05
    R["upperArmL"][2] += 0.10
    R["upperArmR"][2] += -0.10
    R["upperArmL"][0] += 0.08
    R["upperArmR"][0] += 0.08
    R["lowerArmL"][0] += -0.18
    R["lowerArmR"][0] += -0.18
    R["antenna"][0] += -0.34
    R["thighL"][0] += -0.06
    R["thighR"][0] += -0.06


def _act_work(R, rr, ro, t, at, v):
    """工作态：双手前伸敲击，节奏感明显 —— 让"正在干活"一眼可见。"""
    p = t * TAU * 2.1
    tap = math.sin(p)
    R["upperArmL"][0] += -0.92 + tap * 0.05
    R["upperArmR"][0] += -0.92 - tap * 0.05
    R["upperArmL"][2] += -0.34
    R["upperArmR"][2] += 0.34
    R["lowerArmL"][0] += -0.94 - max(0.0, tap) * 0.12
    R["lowerArmR"][0] += -0.94 - max(0.0, -tap) * 0.12
    R["handL"][0] += -0.20 - max(0.0, tap) * 0.16
    R["handR"][0] += -0.20 - max(0.0, -tap) * 0.16
    R["head"][0] += 0.17
    R["spine"][0] += 0.09
    R["antenna"][0] += tap * 0.12


def _act_fly(R, rr, ro, t, at, v):
    """飞天转圈 —— 无聊时的自娱自乐：升空 + 自转 + 手脚张开。

    升空高度刻意压在 0.30 以内：相机取景是按**静立包围盒**算的，
    抬太高会飞出画面（`_camera` 用 `base_bounds`，不随动作重算）。
    """
    up = m4.ease_out(min(1.0, at / 0.75), 2.0)
    ro[1] += up * 0.30
    rr[1] += at * 2.15                       # 自转
    R["upperArmL"][2] += -1.20 * up
    R["upperArmR"][2] += 1.20 * up
    R["upperArmL"][0] += -0.28 * up
    R["upperArmR"][0] += -0.28 * up
    R["lowerArmL"][0] += -0.18 * up
    R["lowerArmR"][0] += -0.18 * up
    R["thighL"][0] += -0.26 * up
    R["thighR"][0] += -0.26 * up
    R["shinL"][0] += 0.34 * up
    R["shinR"][0] += 0.34 * up
    R["head"][0] += -0.12 * up
    R["antenna"][0] += math.sin(at * 6.2) * 0.38 * up
    R["hip"][2] += math.sin(at * 3.1) * 0.07 * up


def _act_stretch(R, rr, ro, t, at, v):
    """伸懒腰 —— 沉稳/内向的舒展：双臂上举过头、身体后仰、踮一下脚。"""
    e = m4.ease_out(min(1.0, at / 0.65), 2.0)
    sway = math.sin(t * 1.6) * 0.055
    R["upperArmL"][2] += -2.05 * e
    R["upperArmR"][2] += 2.05 * e
    R["lowerArmL"][2] += -0.38 * e
    R["lowerArmR"][2] += 0.38 * e
    R["spine"][0] += -0.22 * e
    R["chest"][0] += -0.15 * e
    R["head"][0] += -0.20 * e + sway * 0.35
    R["thighL"][0] += -0.07 * e
    R["thighR"][0] += -0.07 * e
    ro[1] += 0.028 * e


def _act_arms(R, rr, ro, t, at, v):
    """抱臂 —— 毒舌/沉稳的招牌姿势：双臂交叉、微微侧头打量。"""
    e = m4.ease_out(min(1.0, at / 0.5), 2.2)
    sway = math.sin(t * 1.25) * 0.05
    R["upperArmL"][0] += -0.95 * e
    R["upperArmL"][2] += -0.58 * e
    R["lowerArmL"][0] += -1.28 * e
    R["lowerArmL"][2] += -0.32 * e
    R["upperArmR"][0] += -0.95 * e
    R["upperArmR"][2] += 0.58 * e
    R["lowerArmR"][0] += -1.28 * e
    R["lowerArmR"][2] += 0.32 * e
    R["head"][2] += (0.17 + sway) * e
    R["neck"][0] += 0.05 * e
    R["hip"][2] += sway * 0.6


def _act_bounce(R, rr, ro, t, at, v):
    """弹跳撒欢 —— 活泼/调皮的连跳：比 hop 更快更碎，双臂上下甩。"""
    p = t * TAU * 1.85
    h = abs(math.sin(p))
    sq = math.sin(p * 2.0)
    ro[1] += h * 0.085
    R["thighL"][0] += -0.24 * (1.0 - h) - 0.08
    R["thighR"][0] += -0.24 * (1.0 - h) - 0.08
    R["shinL"][0] += 0.44 * (1.0 - h)
    R["shinR"][0] += 0.44 * (1.0 - h)
    R["upperArmL"][0] += -sq * 0.44
    R["upperArmR"][0] += -sq * 0.44
    R["upperArmL"][2] += -0.30 - h * 0.26
    R["upperArmR"][2] += 0.30 + h * 0.26
    R["head"][0] += -0.05 * h
    R["antenna"][0] += -sq * 0.30


def _act_nod(R, rr, ro, t, at, v):
    """点头致意 —— 温和/可靠的礼貌动作：两次点头 + 微微欠身。"""
    e = m4.ease_out(min(1.0, at / 0.35), 2.0)
    n = math.sin(at * 5.2)
    R["neck"][0] += (0.16 + n * 0.15) * e
    R["head"][0] += (0.10 + n * 0.11) * e
    R["chest"][0] += 0.07 * e
    R["upperArmL"][2] += -0.14 * e
    R["upperArmR"][2] += 0.14 * e
    R["antenna"][0] += -n * 0.16 * e


def _act_cheer(R, rr, ro, t, at, v):
    """欢呼 —— 双手高举过头 + 连蹦（庆祝 / 开心）。"""
    e = m4.ease_out(min(1.0, at / 0.45), 2.2)
    p = t * TAU * 1.6
    j = abs(math.sin(p))
    ro[1] += j * 0.075
    R["upperArmL"][2] += -2.25 * e
    R["upperArmR"][2] += 2.25 * e
    R["upperArmL"][0] += -0.18 * e
    R["upperArmR"][0] += -0.18 * e
    R["lowerArmL"][2] += -0.42 * e + math.sin(p) * 0.22
    R["lowerArmR"][2] += 0.42 * e + math.sin(p) * 0.22
    R["head"][0] += -0.14 * e
    R["spine"][0] += -0.05 * e
    R["thighL"][0] += -0.16 * e
    R["thighR"][0] += -0.16 * e
    R["antenna"][0] += -math.sin(p) * 0.30


def _act_pat(R, rr, ro, t, at, v):
    """鼓掌 —— 双手在胸前相击（开心 / 捧场）。"""
    e = m4.ease_out(min(1.0, at / 0.35), 2.0)
    p = t * TAU * 2.4
    c = abs(math.sin(p))
    R["upperArmL"][0] += -0.62 * e
    R["upperArmR"][0] += -0.62 * e
    R["upperArmL"][2] += -0.30 * e + c * 0.10
    R["upperArmR"][2] += 0.30 * e - c * 0.10
    R["lowerArmL"][0] += -1.05 * e
    R["lowerArmR"][0] += -1.05 * e
    R["lowerArmL"][2] += -0.30 * e
    R["lowerArmR"][2] += 0.30 * e
    R["handL"][0] += -0.20 * e - c * 0.14
    R["handR"][0] += -0.20 * e - c * 0.14
    R["head"][0] += (0.06 - c * 0.05) * e
    R["spine"][0] += 0.05 * e
    R["antenna"][0] += c * 0.16


def _act_point(R, rr, ro, t, at, v):
    """指一指 —— 右臂斜向指出（好奇 / 展示）。"""
    e = m4.ease_out(min(1.0, at / 0.40), 2.2)
    poke = math.sin(t * 2.0) * 0.05
    R["upperArmR"][2] += 1.85 * e
    R["upperArmR"][0] += -0.30 * e + poke * 0.5
    R["lowerArmR"][2] += 0.30 * e
    R["lowerArmR"][0] += -0.14 * e
    R["handR"][0] += -0.28 * e
    R["upperArmL"][2] += -0.16 * e
    R["head"][1] += -0.22 * e
    R["head"][2] += 0.10 * e
    R["head"][0] += -0.04 * e
    rr[2] += -0.02 * e
    R["antenna"][0] += math.sin(t * 3.0) * 0.14


def _act_heart(R, rr, ro, t, at, v):
    """比心 —— 双手在胸前合拢、微微低头（亲昵 / 害羞）。"""
    e = m4.ease_out(min(1.0, at / 0.5), 2.0)
    br = math.sin(t * 1.4) * 0.02
    R["upperArmL"][0] += -0.80 * e
    R["upperArmR"][0] += -0.80 * e
    R["upperArmL"][2] += -0.42 * e
    R["upperArmR"][2] += 0.42 * e
    R["lowerArmL"][0] += -1.30 * e
    R["lowerArmR"][0] += -1.30 * e
    R["lowerArmL"][2] += -0.30 * e
    R["lowerArmR"][2] += 0.30 * e
    R["head"][0] += 0.14 * e + br
    R["neck"][0] += 0.06 * e
    R["spine"][0] += 0.04 * e
    R["antenna"][0] += br * 2.0


_ACTS = {
    "walk": _act_walk,
    "wave": _act_wave,
    "hop": _act_hop,
    "peek": _act_peek,
    "ball": _act_ball,
    "dance": _act_dance,
    "spin": _act_spin,
    "think": _act_think,
    "read": _act_read,
    "sleep": _act_sleep,
    "work": _act_work,
    # —— 性格辨识动作（v0.30.1）——
    "fly": _act_fly,          # 飞天转圈：无聊时的自娱自乐
    "stretch": _act_stretch,  # 伸懒腰：沉稳 / 内向
    "arms": _act_arms,        # 抱臂：毒舌 / 沉稳
    "bounce": _act_bounce,    # 弹跳撒欢：活泼 / 调皮
    "nod": _act_nod,          # 点头致意：温和 / 可靠
    # —— v0.30.11 动作扩充 ——
    "cheer": _act_cheer,      # 欢呼：双手高举 + 连蹦
    "pat":   _act_pat,        # 鼓掌：双手胸前相击
    "point": _act_point,      # 指一指：右臂斜指
    "heart": _act_heart,      # 比心：双手合拢胸前
}


# —— 情绪叠加层：不改动作，只"上一层色"——
def _expr_layer(R, rr, ro, expr, t):
    if expr == "happy" or expr == "joy" or expr == "proud":
        R["head"][0] += -0.06
        R["chest"][0] += -0.03
        ro[1] += abs(math.sin(t * 2.6)) * 0.010
        R["upperArmL"][2] += -0.10
        R["upperArmR"][2] += 0.10
    elif expr == "curious":
        R["head"][2] += 0.16
        R["head"][0] += -0.03
        R["antenna"][0] += -0.10
    elif expr == "shy":
        R["head"][0] += 0.20
        R["head"][2] += 0.13
        rr[1] += 0.10
        R["upperArmL"][2] += 0.06
        R["upperArmR"][2] += -0.06
    elif expr == "aggrieved":
        R["head"][0] += 0.24
        R["antenna"][0] += -0.30
        R["upperArmL"][2] += 0.10
        R["upperArmR"][2] += -0.10
    elif expr == "angry":
        R["head"][0] += -0.05
        R["spine"][0] += 0.06
        R["upperArmL"][0] += -0.30
        R["upperArmR"][0] += -0.30
        R["lowerArmL"][0] += -0.55
        R["lowerArmR"][0] += -0.55
        R["antenna"][0] += 0.16
    elif expr == "sleepy":
        R["head"][0] += 0.18
        R["antenna"][0] += -0.22
        ro[1] += -0.006


def acts():
    """全部可用动作名（供设置界面/自检引用）。"""
    return tuple(sorted(_ACTS.keys()))
