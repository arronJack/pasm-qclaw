"""PetAvatar —— 全息科技感 Q 版机器人（拟 3D 光影：渐变辉光/粒子/能量环/LED 表情）。

- 输入：valence / arousal / serotonin / speaking / mode(idle|walk|sleep|talk)
- 手势内建：单击(on_click) 双击(on_double_click) 按住拖动(on_drag)
- 纯 QPainter 绘制，无外部图片资源；所有行为接口与旧版兼容
"""
from __future__ import annotations

import math
import random
import time
from typing import Optional

import qt_compat as qt
from qt_compat import (QColor, QConicalGradient, QFont, QLinearGradient,
                       QPainter, QPainterPath, QPen, QRadialGradient, QPointF,
                       QRectF, Qt, QTimer, QWidget)

import pet_tuning as PT       # v0.30.9 观感调参（飞机大小 / 尾气浓淡 / 飞天高度）


def _now():
    return time.time()


def clamp(v, lo=-1.0, hi=1.0):
    return max(lo, min(hi, v))


# v0.30.11（#8 动作扩充）：2D 与 3D 共用同一份动作全集。
# 此前 set_act 只放行 7 个动作 → behavior 引擎抽到的 nod/stretch/arms/bounce
# 被**静默丢弃**（3D 通道画得出、2D 通道画不出 → 2D 机器上永远看不到）。
# 这里补齐白名单，作为唯一来源。
_KNOWN_ACTS = (
    "", "dance", "ball", "peek", "hop", "wave", "spin", "think",
    "nod", "stretch", "arms", "bounce", "fly",
    "cheer", "pat", "point", "heart",
)


def _look_color(c):
    """v0.30.11 #12 形象 DIY：把「色相/饱和/明度」变换施加到一个 QColor。

    默认（单位变换）下 `transform_rgb` 原样返回 → 这里**原对象返回、零开销**，
    出厂观感一个像素不变。任何异常都退回原色（观感参数绝不能让小人画不出来）。
    """
    try:
        r, g, b = c.redF(), c.greenF(), c.blueF()
        r2, g2, b2 = PT.transform_rgb((r, g, b))
        if abs(r2 - r) < 1e-6 and abs(g2 - g) < 1e-6 and abs(b2 - b) < 1e-6:
            return c
        out = QColor(c)
        out.setRgbF(r2, g2, b2, c.alphaF())
        return out
    except Exception:                       # noqa: BLE001
        return c


class PetAvatar(QWidget):
    def __init__(self, size: int = 96, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, int(size * 1.40))   # v0.28.3：顶部 0.28× 留给工作气泡
        self.valence = 0.0
        self.arousal = 0.3
        self.serotonin = 0.5
        self.speaking = False
        self.skin = "tech"        # tech | cute | mecha
        self.growth = 2           # 0幼儿 1童年 2少年 3青年
        self.mode = "idle"        # idle | walk | sleep | read | talk
        # 情绪状态机（P3）：expr 是"此刻表情词"，act 是"正在做的动作"，均有超时回落到安静
        self.expr = "calm"        # calm|joy|aggrieved|angry|shy|sleepy|curious|proud
        self._expr_until = 0.0
        self.act = ""             # ""|dance|ball|peek|hop
        self._act_until = 0.0
        self.gender = "none"      # none|female|male（外观配件，与自称语气分开）
        self.work_label = ""      # v0.28.3 当前工作标签（头顶上方气泡显示）
        # v0.30.6：气泡开关。桌面浮窗的小人需要它（能看到「它在干什么」），
        # 但聊天页里小人与文字卡片同框，头顶再顶个气泡就顶到卡片外边了 ——
        # 所以由调用方决定，默认保持原样（开）。
        self.bubble_on = True
        # —— v0.29：GPU 3D 渲染状态 ——
        # 惰性创建 + 失败一次永久降级：渲染层的问题绝不能每帧重试、拖慢界面。
        self._3d = None
        self._3d_off = False
        self._3d_info = ""
        # 3D 动画用：上一帧时间戳。Animator 的内部时钟必须靠真实 dt 推进，
        # 否则呼吸/摇摆/天线全都不动（详见 _render_3d_face 注释）。
        self._3d_last_t = 0.0
        # 朝向：-1 朝左 / 0 朝前 / +1 朝右。走路时整个身体转过去 ——
        # 正面朝前"横着平移"看着很别扭（用户反馈）。2D 路径不受影响。
        self.facing = 0.0
        self._skin_set = False    # 用户是否显式选过皮肤（决定 3D 走哪套配色）
        self.on_click: Optional[callable] = None
        self.on_double_click: Optional[callable] = None
        self.on_drag: Optional[callable] = None
        self._phase = 0.0
        # —— v0.30.9 飞天形态（小志：先变飞机再飞、落地前变回人形、带尾气粒子）——
        self.fly = 0.0                # 0=人形 1=飞机（渐进形变，不是瞬间切换）
        self._fly_target = 0.0
        self._fly_dir = (0.0, -1.0)   # 飞行方向（单位向量；尾气与机头都照它画）
        self._trail = []              # 尾气粒子：[x, y, r, age]
        self._blink = 0
        self._blink_cd = random.uniform(2.2, 5.5)
        self._look = 0.0
        self._poke = 0.0
        self._zz = 0.0
        self._p0 = None
        self._g0 = None
        self._dragging = False
        self._moved = False
        self._last_release = 0.0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(50)

    # ---------- 外部接口（保持兼容） ----------
    def set_state(self, valence, arousal, serotonin=None, speaking=False):
        self.valence = clamp(valence)
        self.arousal = clamp(arousal, 0.0, 1.0)
        if serotonin is not None:
            self.serotonin = clamp(serotonin, 0.0, 1.0)
        self.speaking = speaking
        # 修复「走路像飘」：说话时不要覆盖 walk/sleep/read 模式，否则四肢摆动被冻结、只剩窗口平移
        if speaking and self.mode not in ("walk", "sleep", "read"):
            self.mode = "talk"
        self.update()

    def set_skin(self, skin: str):
        if skin in ("tech", "cute", "mecha"):
            self.skin = skin
            self._skin_set = True
        self.update()

    def set_growth(self, g: int):
        self.growth = max(0, min(4, int(g)))   # 值域 0~4，与 growth.STAGES 对齐（成年=4）
        self.update()

    def set_mode(self, mode: str):
        if mode in ("idle", "walk", "sleep", "read"):
            self.mode = mode
            self.speaking = False
        self.update()

    def poke(self):
        self._poke = 1.0

    # ---------- 表情 / 动作 / 性别（P3 人格层接口） ----------
    def set_expr(self, name: str, seconds: float = 3.5):
        """设置情绪表情词（超时自动回落到 calm）。"""
        if name in ("calm", "joy", "aggrieved", "angry", "shy", "sleepy",
                    "curious", "proud"):
            self.expr = name
            self._expr_until = time.monotonic() + max(0.8, seconds)
        self.update()

    def set_act(self, act: str, seconds: float = 4.0):
        """触发一个动作。动作全集须与 desktop/pet_behavior.py 的 ALL_ACTS
        以及 pet3d/anim.py 的 _ACTS 保持一致（见模块级 _KNOWN_ACTS）。

        v0.30.11（#8）修复：此前这里写死 7 个动作，behavior 引擎抽出的
        nod/stretch/arms/bounce 会被静默丢弃 —— 表现为"小人动作少"
        （引擎以为在做，画面上什么都没发生）。
        """
        if act in _KNOWN_ACTS:
            self.act = act
            self._act_until = time.monotonic() + max(0.6, seconds)
        self.update()

    def set_gender(self, g: str):
        """性别只影响外观配件（蝴蝶结/护目饰），自称与语气由人格配置负责。"""
        if g in ("none", "female", "male"):
            self.gender = g
        self.update()

    # ---------- v0.30.9 飞天：飞机形态 + 尾气 ----------
    def set_fly(self, on: bool):
        """进入 / 退出**飞机形态**（渐进形变，由 _tick 推进，约 0.9s 完成）。"""
        self._fly_target = 1.0 if on else 0.0
        self.update()

    def set_fly_motion(self, dx: float, dy: float):
        """告诉小人"此刻往哪个方向飞"—— 机头、机翼朝向与尾气方向都按它走。"""
        try:
            n = math.hypot(float(dx), float(dy))
            if n > 1e-6:
                self._fly_dir = (float(dx) / n, float(dy) / n)
        except Exception:
            pass

    def fly_morph(self) -> float:
        """当前形变进度 0..1（自检/诊断用）。"""
        return float(self.fly)

    def _emit_trail(self):
        """尾气粒子：从机尾（飞行方向的反侧）吐一颗，带随机抖动。"""
        # v0.30.9：上限/大小都不再写死 —— 由设置面板的「尾气浓淡」决定
        if len(self._trail) > PT.trail_cap():
            return
        dx, dy = self._fly_dir
        bx = self.width() * 0.5 - dx * self.width() * 0.20
        by = self.height() * 0.5 - dy * self.height() * 0.20
        _sz = PT.trail_size()
        self._trail.append([bx + random.uniform(-2.2, 2.2),
                            by + random.uniform(-2.2, 2.2),
                            (2.0 + random.uniform(0.0, 1.6)) * _sz, 0.0])

    def set_bubble(self, on: bool):
        """v0.30.6：是否显示头顶工作气泡（默认 True，与原行为一致）。

        聊天页关掉它：那里小人和文字卡片并排，气泡会顶出卡片。
        桌面浮窗保持开启 —— 那是它唯一能「说出」自己在干什么的地方。
        """
        on = bool(on)
        if on == getattr(self, "bubble_on", True):
            return
        self.bubble_on = on
        self.update()

    def set_work(self, label: str):
        """v0.27.4：把"当前正在做的工作"显示在头像下方（空串=清空）。

        真机需求（小志）："桌面小人如果是工作的状态，也希望呈现出不同的工作内容。"
        """
        label = (label or "").strip()
        if label == getattr(self, "work_label", ""):
            return
        self.work_label = label
        self.update()

    def _active_expr(self) -> str:
        now = time.monotonic()
        if self._expr_until and now < self._expr_until:
            return self.expr
        if self._expr_until:
            self.expr, self._expr_until = "calm", 0.0
        return "calm"

    def _active_act(self) -> str:
        now = time.monotonic()
        if self._act_until and now < self._act_until:
            return self.act
        if self._act_until:
            self.act, self._act_until = "", 0.0
        return ""

    # ---------- v0.30.9 飞天绘制 ----------
    def _paint_trail(self, p, w, h, s):
        """尾气：由亮到暗、由小到大的圆斑 + 机尾喷口火焰。

        粒子坐标是**控件坐标**，永远不会画到别的窗口上；
        上限/寿命由 `pet_tuning` 的「尾气浓淡」决定（默认档 70 颗 / 1.5s）。
        """
        if not self._trail:
            return
        dx, dy = self._fly_dir
        p.setPen(Qt.NoPen)
        for x, y, r, age in self._trail:
            a = max(0.0, 1.0 - age / max(0.01, PT.trail_life()))
            rr = r * s * 3.0
            p.setBrush(QColor(255, int(168 + 60 * a), int(90 + 40 * a), int(155 * a)))
            p.drawEllipse(QPointF(x, y), rr, rr)
            p.setBrush(QColor(255, 255, 255, int(130 * a * a)))
            p.drawEllipse(QPointF(x, y), rr * 0.45, rr * 0.45)
        if self.fly > 0.6:                              # 机尾喷口（跟飞行方向）
            _ex = w * 0.5 - dx * w * 0.24
            _ey = h * 0.5 - dy * h * 0.24
            _fl = (0.6 + 0.4 * abs(math.sin(self._phase * 9.0))) * s * 7.0
            p.setBrush(QColor(253, 186, 116, 215))
            p.drawEllipse(QPointF(_ex - dx * _fl * 0.5, _ey - dy * _fl * 0.5),
                          _fl * 0.5 + 2.4 * s, _fl * 0.5 + 2.4 * s)
            p.setBrush(QColor(255, 255, 255, 205))
            p.drawEllipse(QPointF(_ex, _ey), 3.0 * s, 3.0 * s)

    def _paint_morphing(self, p, w, h, cx, cy, s):
        """变形过程提示：一圈跟着呼吸放大的光环（"要变身了"的读秒感）。"""
        k = min(1.0, self.fly / 0.5)
        rr = (16 + 10 * k + 3 * math.sin(self._phase * 7.0)) * s
        p.setPen(QPen(QColor(56, 189, 248, int(200 * k)), 1.6 * s))
        p.setBrush(QColor(224, 242, 254, int(60 * k)))
        p.drawEllipse(QPointF(cx, cy + 6 * s), rr, rr * 0.42)
        p.setPen(QPen(QColor(255, 255, 255, int(180 * k)), 1.2 * s))
        p.drawEllipse(QPointF(cx, cy + 6 * s), rr * 0.55, rr * 0.23)

    def _paint_fly_form(self, p, w, h, cx, cy, s):
        """飞机形态：机体 + 后掠翼 + 尾翼 + 机头锥 + 座舱光，整机按飞行方向旋转。

        为什么整机跟着方向转：小人是**正面朝用户**画的，飞机从正面看只有一个小十字，
        完全看不出"在飞"；按速度方向旋转后就是"头朝前飞"，一眼就懂。
        """
        from PySide6.QtGui import QPolygonF
        fly = self.fly
        dx, dy = self._fly_dir
        grow = 0.50 + 0.50 * min(1.0, (fly - 0.5) / 0.5)   # 像从身体里"展开"
        p.save()
        p.translate(cx, cy)
        p.rotate(math.degrees(math.atan2(dy, dx)) + 90.0)
        # v0.30.9：grow 是「从身体里展开」的动画进度（0.5→1.0），
        # 后面再乘用户设的飞机大小系数 —— 两条相乘，动画不受影响。
        _K = PT.fly_scale()
        p.scale(grow * _K, grow * _K)
        for sgn in (-1, 1):                                # 后掠机翼
            p.setPen(QPen(QColor(125, 211, 252, 210), 1.2 * s))
            p.setBrush(QColor(186, 230, 253, 190))
            p.drawPolygon(QPolygonF([QPointF(sgn * 6 * s, 4 * s),
                                     QPointF(sgn * 46 * s, -16 * s),
                                     QPointF(sgn * 44 * s, -2 * s),
                                     QPointF(sgn * 7 * s, 12 * s)]))
        for sgn in (-1, 1):                                # 水平尾翼
            p.setBrush(QColor(148, 197, 245, 200))
            p.drawPolygon(QPolygonF([QPointF(sgn * 4 * s, -24 * s),
                                     QPointF(sgn * 20 * s, -32 * s),
                                     QPointF(sgn * 5 * s, -18 * s)]))
        p.setBrush(QColor(96, 165, 250, 220))              # 垂直尾翼
        p.drawPolygon(QPolygonF([QPointF(-2 * s, -22 * s), QPointF(2 * s, -22 * s),
                                 QPointF(0.0, -40 * s)]))
        p.setPen(QPen(QColor(226, 232, 240, 240), 1.6 * s))  # 机体
        p.setBrush(QColor(241, 245, 249, 240))
        p.drawRoundedRect(QRectF(-11 * s, -26 * s, 22 * s, 56 * s), 10 * s, 10 * s)
        p.setBrush(QColor(56, 189, 248, 235))              # 机头锥
        p.setPen(Qt.NoPen)
        p.drawPolygon(QPolygonF([QPointF(-9 * s, 28 * s), QPointF(9 * s, 28 * s),
                                 QPointF(0.0, 44 * s)]))
        p.setBrush(QColor(224, 242, 254, 250))             # 座舱光
        p.drawEllipse(QPointF(0.0, 8 * s), 6.0 * s, 9.0 * s)
        p.setBrush(QColor(34, 211, 238, 220))
        p.drawEllipse(QPointF(0.0, 8 * s), 3.6 * s, 5.6 * s)
        for sgn in (-1, 1):                                # 翼尖灯
            p.setBrush(QColor(255, 214, 102, 230))
            p.drawEllipse(QPointF(sgn * 30 * s, -10 * s), 1.8 * s, 1.8 * s)
        p.restore()
    # ---------- 3D 渲染（v0.29） ----------
    def _skin3d(self) -> str:
        """2D 皮肤名 → 3D 皮肤名。

        用户**没显式选过**皮肤时走 royal（帝皇铠甲金）—— 这是 v0.29 的
        默认外观；显式选过就尊重用户：mecha 在 3D 里对应的就是 royal。
        """
        if not self._skin_set:
            return "royal"
        return {"tech": "tech", "cute": "cute", "mecha": "royal"}.get(self.skin, "royal")

    def _hide3d(self):
        """v0.30.11 #12：形象 DIY 关掉的头饰 → 3D 要隐藏的部件组。

        2D 是"画不画"的分支，3D 只能**重建 rig 时少加部件**，
        所以这里返回一个组名元组给 Scene.set_look。
        """
        try:
            h = []
            if not PT.acc_on("pet_acc_ears"):
                h.append("ears")
            if not PT.acc_on("pet_acc_antenna"):
                h.append("antenna")
            return tuple(h)
        except Exception:                   # noqa: BLE001
            return ()

    def _render_3d_face(self, w, h):
        """尝试渲染一帧 3D 头像；不可用/出错返回 None（调用方回退 2D）。

        三重保险（渲染层出问题绝不能让界面崩）：
          · available() 只探一次，失败即永久置 _3d_off，不每帧重试；
          · 任何异常都吞掉并永久降级，同时把原因记进 _3d_info 便于排查；
          · Scene 复用 —— 每次重建 Rig 都要重传 70+ 个 VBO，代价 30ms 级。
        """
        if self._3d_off:
            return None
        try:
            if self._3d is None:
                import pet3d
                ok, info = pet3d.available()
                if not ok:
                    self._3d_off = True
                    self._3d_info = "3D 不可用: %s" % info
                    return None
                self._3d = pet3d.Scene(int(self.growth), self._skin3d())
                self._3d_info = info
            sc = self._3d
            if sc.growth != int(self.growth):
                sc.set_growth(int(self.growth))
            sk = self._skin3d()
            if sc.skin != sk:
                sc.set_skin(sk)
            # v0.30.11 #12：头饰开关变了要重建 rig（部件增减只能重建）。
            # 重建后是新 rig（零姿态），必须 freeze 而不是 advance。
            _rebuilt = False
            try:
                _rebuilt = sc.set_look(self._hide3d())
            except Exception:                                   # noqa: BLE001
                _rebuilt = False
            # ★ 必须用 advance 推进内部时钟，不能用 freeze/jump。
            # freeze 走的是 Animator.jump()，它**不推进 self.t**；而 pet3d/anim.py
            # 的呼吸基底 `breathing = math.sin(t * 1.7)`、天线摆动全都依赖 t ——
            # 于是每次都算 t=0 的同一姿势，3D 变成**静止的雕塑**（实测连续帧差异
            # 0.00%，而 2D 是 5.10%）。用户的观感就是"看着是立体的、但动作是死的"。
            _act = self._active_act()
            _mode = "walk" if self.mode == "walk" else ""
            _expr = self._active_expr()
            _now = time.monotonic()
            _dt = (_now - self._3d_last_t) if self._3d_last_t else 0.0
            self._3d_last_t = _now
            if _dt <= 0.0 or _rebuilt:
                # 首帧 / 降级后重建 / 头饰开关重建：直接到位，避免"长"出来
                sc.freeze(_act, _mode, _expr, look=self._look,
                          speaking=self.speaking, valence=self.valence,
                          facing=self.facing)
            else:
                # 夹住 dt：窗口被拖动/系统卡顿后可能出现几秒的空档，
                # 不夹会让小人瞬间"跳"到很远的相位。
                sc.advance(min(_dt, 0.12), _act, _mode, _expr, look=self._look,
                           speaking=self.speaking, valence=self.valence,
                           facing=self.facing)
            px = int(min(w, h * 0.72) * max(1.0, float(self.devicePixelRatioF())))
            return sc.image(max(32, px))
        except Exception as e:
            self._3d_off = True
            self._3d = None
            self._3d_last_t = 0.0      # 重建后从 freeze 起步
            self._3d_info = "3D 已降级: %s: %s" % (type(e).__name__, e)
            return None

    def set_facing(self, v):
        """设置朝向（-1 左 / 0 正 / +1 右）。

        只影响 3D 的转身；2D 是正面朝向用户的设计，翻转反而更怪，故不参与。
        """
        try:
            v = float(v)
        except Exception:
            v = 0.0
        v = max(-1.0, min(1.0, v))
        if abs(v - self.facing) > 1e-6:
            self.facing = v
            self.update()

    def three_d_status(self) -> str:
        """给自检/诊断用：返回 3D 通道的当前状态描述。"""
        if self._3d_off:
            return self._3d_info or "3D 已关闭"
        if self._3d is None:
            return "3D 未启用（尚未渲染过）"
        return self._3d_info or "3D 就绪"

    # ---------- 动画 ----------
    def _tick(self):
        now = time.monotonic()
        if self._expr_until and now >= self._expr_until:
            self.expr, self._expr_until = "calm", 0.0
        if self._act_until and now >= self._act_until:
            self.act, self._act_until = "", 0.0
        self._phase += 0.10 if self.mode == "walk" else 0.055
        self._blink_cd -= 0.05
        if self._blink_cd <= 0:
            self._blink = 2
            self._blink_cd = random.uniform(2.2, 5.5)
        if self._blink > 0:
            self._blink -= 1
        if self._poke > 0:
            self._poke = max(0.0, self._poke - 0.08)
        self._look = math.sin(self._phase * 0.35) * 0.35
        # 飞天：形变推进 + 尾气粒子（_tick 每 50ms 一次）
        if abs(self.fly - self._fly_target) > 1e-3:
            _up = self._fly_target > self.fly
            self.fly = max(0.0, min(1.0, self.fly + (0.055 if _up else -0.045)))
        if self.fly > 0.55 and random.random() < PT.trail_prob():
            self._emit_trail()
        _LIFE = max(0.01, PT.trail_life())
        if self._trail:
            _alive = []
            for _t in self._trail:
                _t[3] += 0.05
                _t[2] += 0.42                 # 越飘越大（扩散）
                if _t[3] < _LIFE:
                    _alive.append(_t)
            self._trail = _alive
        self.update()

    # ---------- 手势（内建，注入事件实测通过） ----------
    def mousePressEvent(self, ev):  # noqa: N802
        if ev.button() == Qt.LeftButton:
            self._p0 = qt.ev_pos(ev)
            self._g0 = qt.ev_global_pos(ev)
            self._dragging = False
            self._moved = False
        super().mousePressEvent(ev)

    def mouseMoveEvent(self, ev):  # noqa: N802
        if (ev.buttons() & Qt.LeftButton) and self._p0 is not None:
            if (qt.ev_pos(ev) - self._p0).manhattanLength() > 5:
                self._moved = True
                self._dragging = True
            if self._dragging and self.on_drag:
                gp = qt.ev_global_pos(ev)
                self.on_drag(gp - self._g0)
                self._g0 = gp
        super().mouseMoveEvent(ev)

    def mouseReleaseEvent(self, ev):  # noqa: N802
        if ev.button() == Qt.LeftButton and self._p0 is not None:
            was_drag = self._dragging
            self._p0 = None
            self._dragging = False
            if not was_drag and not self._moved:
                self.poke()
                now = _now()
                if now - self._last_release < 0.4:
                    self._last_release = 0.0
                    if self.on_double_click:
                        self.on_double_click()
                else:
                    self._last_release = now
                    if self.on_click:
                        self.on_click()
            self._moved = False
        super().mouseReleaseEvent(ev)

    def mouseDoubleClickEvent(self, ev):  # noqa: N802
        if ev.button() == Qt.LeftButton and not self._moved:
            self.poke()
            if self.on_double_click:
                self.on_double_click()

    # ================= 绘制：全息科技机器人 =================
    def _paint_work_bubble(self, p, w, h, cx, s):
        """头顶工作气泡 —— 抽成方法是为了让 3D 路径复用同一份实现。

        气泡画在**头像区域之上**（y 从 2*s 起），所以 3D 换掉的是头像、
        不是整个绘制流程：无论走哪条路径，这个气泡都必须照常出现。
        """
        # —— v0.28.3 工作提示气泡：有活干时在**头顶上方**冒泡显示"正在做的事"
        # （v0.27.4 是头像下方的丑矩形，真机反馈改头顶气泡：白底圆角+青描边+小尾巴+柔影）
        if not getattr(self, "bubble_on", True):
            return                       # v0.30.6：关掉时不留空档、不画影
        wl = str(getattr(self, "work_label", "") or "")[:22]
        if wl:
            bh = 17 * s
            f = p.font()
            f.setPointSizeF(max(6.5, 8.5 * s))
            p.setFont(f)
            tw = p.fontMetrics().horizontalAdvance(wl)
            bw = min(w - 2 * s, tw + 16 * s)
            bx = cx - bw / 2.0
            by = 2.0 * s
            bb = by + bh
            # ① 柔和投影
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(15, 23, 42, 26))
            p.drawRoundedRect(int(bx + 1), int(by + 2), int(bw), int(bh),
                              int(bh / 2), int(bh / 2))
            # ② 尾巴：先填充再描两条斜边，指向头顶
            p.setBrush(QColor(255, 255, 255, 245))
            tail = QPainterPath()
            tail.moveTo(cx - 4.2 * s, bb - 1)
            tail.lineTo(cx + 4.2 * s, bb - 1)
            tail.lineTo(cx, bb + 5 * s)
            tail.closeSubpath()
            p.drawPath(tail)
            p.setPen(QPen(QColor(14, 165, 233, 230), 1.4 * s))
            p.drawLine(QPointF(cx - 4.2 * s, bb - 1), QPointF(cx, bb + 5 * s))
            p.drawLine(QPointF(cx + 4.2 * s, bb - 1), QPointF(cx, bb + 5 * s))
            # ③ 气泡主体：白底圆角 + 青描边（盖住尾巴根部接缝）
            p.setBrush(QColor(255, 255, 255, 245))
            p.setPen(QPen(QColor(14, 165, 233, 230), 1.4 * s))
            p.drawRoundedRect(int(bx), int(by), int(bw), int(bh),
            int(bh / 2), int(bh / 2))
            # ④ 文字：深色，带一个小闹钟点题
            p.setPen(QPen(QColor(15, 23, 42)))
            p.drawText(int(bx), int(by), int(bw), int(bh), Qt.AlignCenter, wl)


    def paintEvent(self, ev):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        # v0.28.3：窗口扩高至 1.40×（顶部留给工作气泡），头像整体下移保持原像素布局
        # （0.60 × 1.40 = 0.84 = 旧 0.56 + 下移 0.28，头像与旧版逐像素一致）
        cx, cy = w / 2, h * 0.60
        s = w / 120.0
        # —— v0.30.10：飞行/变形中**强制走 2D** —— 飞机形态只在 2D 分支绘制
        # （_paint_fly_form，531 行起）；若走 3D 优先通道则"变身"永远画不出来
        # （3D 通道没有飞机状态，整段直接 return）。着陆后 fly 回到 0，下一帧自然恢复 3D。
        # 这是「能飞天却不会变身」的唯一根因：用户机 3D 开着，飞机形态被 3D 通道吞掉了。
        self._flying_now = self.fly > 0.02
        _face3d = (None if self._flying_now else self._render_3d_face(w, h))
        if _face3d is not None:
            p.drawImage(QRectF(0.0, h * 0.28, w, h * 0.72), _face3d)
            self._paint_work_bubble(p, w, h, cx, s)
            self._paint_trail(p, w, h, s)          # ★ 3D 通道也要拖尾
            p.end()
            return
        # ★ v0.30.9：形变过半就直接画**飞机**（与人体叠在一起会成"四不像"）
        if self.fly >= 0.5:
            self._paint_fly_form(p, w, h, cx, cy, s)
            self._paint_trail(p, w, h, s)
            p.end()
            return
        if self.fly > 0.02:
            self._paint_morphing(p, w, h, cx, cy, s)      # 形变中的光晕提示
        expr = self._active_expr()
        act = self._active_act()
        sleeping = self.mode == "sleep"
        reading = self.mode == "read"
        if expr != "calm":                     # 显式表情优先于数值情绪
            happy = expr in ("joy", "proud")
            low = expr in ("aggrieved", "angry", "shy")
        else:
            happy = self.valence > 0.15
            low = self.valence < -0.15
        mood_hue = self.valence   # -1..1
        gray_body = low and expr not in ("angry",)   # 生气时保留机体本色，只靠氛围光变色
        P = {
            "body0": QColor(224, 242, 254), "body1": QColor(125, 211, 252),
            "body2": QColor(37, 99, 235),
            "acc": QColor(34, 211, 238), "acc2": QColor(56, 189, 248),
            "ear0": QColor(99, 102, 241), "ear1": QColor(56, 189, 248),
            "aura": QColor(34, 211, 238, 46), "low": QColor(129, 140, 248, 30),
            "happy": QColor(56, 189, 248, 60), "p0": QColor(103, 232, 249, 150),
        }
        if self.skin == "cute":
            P = {
                "body0": QColor(255, 241, 242), "body1": QColor(253, 164, 175),
                "body2": QColor(219, 39, 119),
                "acc": QColor(244, 114, 182), "acc2": QColor(236, 72, 153),
                "ear0": QColor(249, 168, 212), "ear1": QColor(244, 114, 182),
                "aura": QColor(244, 114, 182, 34), "low": QColor(168, 85, 247, 26),
                "happy": QColor(251, 113, 133, 50), "p0": QColor(253, 164, 175, 140),
            }
        if self.skin == "mecha":
            P = {
                "body0": QColor(51, 65, 85), "body1": QColor(100, 116, 139),
                "body2": QColor(15, 23, 42),
                "acc": QColor(251, 191, 36), "acc2": QColor(245, 158, 11),
                "ear0": QColor(148, 163, 184), "ear1": QColor(71, 85, 105),
                "aura": QColor(251, 191, 36, 26), "low": QColor(220, 38, 38, 40),
                "happy": QColor(52, 211, 153, 50), "p0": QColor(251, 191, 36, 150),
            }
        # —— v0.30.11 #12 形象 DIY：对皮肤调色板施加「色相/饱和/明度」变换 ——
        # 默认是单位变换（look_is_identity）→ 跳过整条变换，出厂观感零回归。
        if not PT.look_is_identity():
            P = {_k: _look_color(_c) for _k, _c in P.items()}
        # 成长→外观（v0.30.4：与 growth.STAGES 严格一一对应，共 5 档：幼儿/童年/少年/青年/成年）
        #   成长方向：幼年→成年**明显长高长大**。旧版 scale 仅 0.98~1.16（差距太小，看着像没长大），
        #   现拉到 0.72~1.34；头部随成长相对变小（头身比更趋近真人），手臂/腿随档位递增。
        #   arm = 常态手臂长度；leg=1 表示有脚（幼儿=0 为悬浮球）。
        # up / down = 该档「中心以上 / 中心以下」的实测尺寸（s=1、scale=1 时，
        # 离屏真实渲染量得，只取实心本体、排除柔光晕圈）。scale 由
        # 「目标实高 T ÷ (up+down)」反推 —— 于是"越大越高"是量出来的，
        # 不是拍脑袋填的；下面的安全钳制再用 up/down 保证任何窗口都装得下。
        G = {
            0: dict(scale=0.575, head_w=94, head_h=82, eye_r=9.6, ant=24, ear=1.18,
                    body_drop=28, leg=0, arm=10, up=79, down=69),   # 幼儿期：最矮、头大身小、悬浮球
            1: dict(scale=0.920, head_w=88, head_h=76, eye_r=8.6, ant=34, ear=1.10,
                    body_drop=29, leg=1, arm=13, up=50, down=69),   # 童年期：长出脚、开始抽条
            2: dict(scale=1.123, head_w=82, head_h=70, eye_r=7.8, ant=44, ear=1.04,
                    body_drop=31, leg=1, arm=16, up=58, down=44),   # 少年期：明显长高
            3: dict(scale=1.099, head_w=76, head_h=64, eye_r=7.0, ant=54, ear=0.98,
                    body_drop=33, leg=1, arm=20, up=69, down=47),   # 青年期：修长、天线高挑
            4: dict(scale=1.073, head_w=72, head_h=60, eye_r=6.6, ant=64, ear=0.94,
                    body_drop=35, leg=1, arm=24, up=79, down=51),   # 成年期：最高最修长、头身比最小
        }.get(int(self.growth), None) or dict(scale=1.06, head_w=82, head_h=70,
                                              eye_r=7.8, ant=44, ear=1.04,
                                              body_drop=31, leg=1, arm=16)
        g_scale = G["scale"]
        # v0.30.4：安全钳制 —— 用实测的 up/down 预算判断这一档在当前窗口里够不够地方，
        # 不够就整体等比缩一点。旧版成年档天线/脚会被自己的固定尺寸切掉（真机可见），
        # 这里保证 5 档在任意 size（桌面浮窗 120 / 主窗 76）下都完整。
        _up = float(G.get("up", 110)) * s * g_scale
        _dn = float(G.get("down", 80)) * s * g_scale
        if _up > cy or _dn > h - cy:
            g_scale *= min(cy / max(_up, 1e-6), (h - cy) / max(_dn, 1e-6), 1.0)
        p.translate(cx, cy)
        p.scale(g_scale, g_scale)
        if act == "spin":                    # 转圈：整体绕中心轻摆，配运动残影
            p.rotate(math.sin(self._phase * 9.0) * 14)
        p.translate(-cx, -cy)

        # —— 背景光晕（情绪色）——
        if expr == "angry":
            aura = QColor(248, 113, 113, 70)
        elif expr == "aggrieved":
            aura = QColor(129, 140, 248, 70)
        elif expr == "shy":
            aura = QColor(244, 114, 182, 50)
        elif expr == "joy" or expr == "proud":
            aura = QColor(250, 204, 21, 55)
        else:
            aura = P["happy"] if happy else (P["low"] if low else P["aura"])
        g = QRadialGradient(cx, cy - 8 * s, 62 * s)
        g.setColorAt(0.0, aura)
        g.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.setBrush(g)
        p.setPen(Qt.NoPen)
        p.drawEllipse(cx - 70 * s, cy - 78 * s, 140 * s, 140 * s)

        # —— 悬浮微粒轨道（3 点逆时针绕）——
        p.setPen(Qt.NoPen)
        for k in range(3):
            ang = self._phase * 0.9 + k * (2 * math.pi / 3)
            px = cx + math.cos(ang) * 34 * s
            py = cy - 10 * s + math.sin(ang) * 24 * s
            p.setBrush(P["p0"])
            p.drawEllipse(QPointF(px, py), 1.8 * s, 1.8 * s)

        bob = math.sin(self._phase * 1.4) * 1.4 * s - self._poke * 5.0 * s
        if self.mode == "walk":
            bob += math.sin(self._phase * 4.0) * 3.4 * s   # 正负起伏，走路上下自然颠
        if act == "dance":
            bob += abs(math.sin(self._phase * 6.0)) * 4.5 * s
        if act == "hop":
            bob -= abs(math.cos(self._phase * 7.0)) * 5.0 * s
        # —— v0.30.11 新动作的整体位移（bob 加到 cy：正=下沉，负=上浮）——
        if act == "bounce":
            bob -= abs(math.sin(self._phase * 8.0)) * 5.5 * s   # 连蹦（比 hop 更碎更快）
        elif act == "nod":
            bob += abs(math.sin(self._phase * 5.0)) * 2.2 * s   # 点头的上下轻颤
        elif act == "bow":
            bob += 4.5 * s * (0.5 + 0.5 * math.sin(self._phase * 2.2))  # 欠身下沉
        elif act == "cheer":
            bob -= abs(math.sin(self._phase * 6.5)) * 4.0 * s   # 欢呼时也随之起跳
        cy += bob

        # —— 头顶全息天线：杆 + 旋转光环 + 能量球（越长越成熟）——
        #     v0.30.11 #12：可由「形象 DIY → 全息天线」关掉
        if PT.acc_on("pet_acc_antenna"):
            ant_x = cx + 4 * s
            ant_top = cy - G["ant"] * s
            p.setPen(QPen(QColor(148, 163, 184, 220), 2.4 * s,
                          Qt.SolidLine, Qt.RoundCap))
            p.drawLine(QPointF(ant_x, cy - 26 * s), QPointF(ant_x + 4 * s, ant_top))
            # 旋转光环
            ring_r = 8.5 * s
            p.setPen(QPen(QColor(34, 211, 238, 190), 1.6 * s))
            arc = QRectF(ant_x + 4 * s - ring_r, ant_top - 2 * s - ring_r,
                         2 * ring_r, 2 * ring_r)
            p.drawArc(arc, int((self._phase * 320) % 360) * 16, 120 * 16)
            # 能量球
            er = 4.6 * s + 0.6 * s * math.sin(self._phase * 2.0)
            ecol = QColor(34 + int(120 * self.serotonin),
                          200 + int(40 * self.serotonin), 255)
            g2 = QRadialGradient(QPointF(ant_x + 4 * s, ant_top - 2 * s), er)
            g2.setColorAt(0.0, QColor(255, 255, 255, 235))
            g2.setColorAt(0.4, ecol)
            g2.setColorAt(1.0, ecol.darker(140))
            p.setBrush(g2)
            p.setPen(Qt.NoPen)
            p.drawEllipse(QPointF(ant_x + 4 * s, ant_top - 2 * s), er, er)

        # —— 头部主体：金属渐变圆角体（幼儿大头→青年修长）——
        head_w, head_h = G["head_w"] * s, G["head_h"] * s
        head_r = QRectF(cx - head_w / 2, cy - 28 * s, head_w, head_h)
        lg = QLinearGradient(head_r.topLeft(), head_r.bottomRight())
        if not gray_body:
            lg.setColorAt(0.0, P["body0"])
            lg.setColorAt(0.45, P["body1"])
            lg.setColorAt(1.0, P["body2"])
        else:
            lg.setColorAt(0.0, QColor(203, 213, 225, 255))
            lg.setColorAt(0.5, QColor(100, 116, 139, 255))
            lg.setColorAt(1.0, QColor(51, 65, 85, 255))
        body = QPainterPath()
        body.addRoundedRect(head_r, 20 * s, 20 * s)
        p.setBrush(lg)
        p.setPen(QPen(QColor(15, 23, 42, 120), 1.6 * s))
        p.drawPath(body)

        # 金属高光弧
        hi = QPainterPath()
        hi.addRoundedRect(QRectF(cx - head_w / 2 + 6 * s, cy - 24 * s,
                                 head_w - 12 * s, 14 * s), 10 * s, 10 * s)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 70))
        p.drawPath(hi)

        # —— 猫耳：科技耳（外层渐变+内发光；幼儿耳朵更大更萌）——
        #     v0.30.11 #12：可由「形象 DIY → 头饰（耳/角）」关掉
        if PT.acc_on("pet_acc_ears"):
            ear_k = G["ear"]
            for side, sgn in ((-1, -1), (1, 1)):
                ex0 = cx + sgn * 24 * s
                ear = QPainterPath()
                ear.moveTo(ex0 - 9 * s * ear_k, cy - 24 * s)
                ear.lineTo(ex0 + sgn * 1 * s, cy - (24 + 22 * ear_k) * s)
                ear.lineTo(ex0 + 9 * s * ear_k, cy - 24 * s)
                ear.closeSubpath()
                elg = QLinearGradient(ex0, cy - 46 * s, ex0, cy - 24 * s)
                elg.setColorAt(0.0, P["ear0"])
                elg.setColorAt(1.0, P["ear1"])
                p.setBrush(elg)
                p.setPen(QPen(QColor(15, 23, 42, 100), 1.2 * s))
                p.drawPath(ear)
                # 内辉
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(224, 242, 254, 130))
                ear2 = QPainterPath()
                ear2.moveTo(ex0 - 4 * s * ear_k, cy - 27 * s)
                ear2.lineTo(ex0 + sgn * 1 * s, cy - (24 + 13 * ear_k) * s)
                ear2.lineTo(ex0 + 4 * s * ear_k, cy - 27 * s)
                ear2.closeSubpath()
                p.drawPath(ear2)

        # —— 性别外观配件（P3：female 蝴蝶结 / male 护目饰带）——
        if self.gender == "female":
            bx, by = cx + 34 * s, cy - 32 * s
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(244, 114, 182))
            p.drawEllipse(QPointF(bx - 4.5 * s, by), 3.6 * s, 2.8 * s)
            p.drawEllipse(QPointF(bx + 4.5 * s, by), 3.6 * s, 2.8 * s)
            p.setBrush(QColor(251, 207, 232))
            p.drawEllipse(QPointF(bx, by), 2.4 * s, 2.4 * s)
        elif self.gender == "male":
            p.setPen(QPen(QColor(30, 41, 59, 200), 2.2 * s, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(cx - head_w / 2 + 10 * s, cy - 30 * s, cx + head_w / 2 - 10 * s,
                       cy - 30 * s)
            p.setPen(QPen(QColor(56, 189, 248, 220), 2.6 * s, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(cx - 3 * s, cy - 32 * s, cx + 3 * s, cy - 28 * s)

        # —— 面部深色视窗（科技核心屏）——
        face = QRectF(cx - 24 * s, cy - 16 * s, 48 * s, 34 * s)
        fp = QPainterPath()
        fp.addRoundedRect(face, 12 * s, 12 * s)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(15, 23, 42, 210))
        p.drawPath(fp)
        # 屏内扫描线
        p.setPen(QPen(P["acc"], 1 * s))
        scan_y = cy - 4 * s + math.sin(self._phase * 1.6) * 8 * s
        p.drawLine(cx - 20 * s, scan_y, cx + 20 * s, scan_y)

        # —— LED 眼睛 + 表情（P3 状态机：按情绪词画眼型/眉/泪/腮红） ——
        eye_y = cy - 6 * s
        ex_l = cx - 12 * s + self._look * 2.5 * s
        ex_r = cx + 12 * s + self._look * 2.5 * s
        if expr == "angry":
            eye_col = QColor(248, 113, 113)
        elif expr == "aggrieved":
            eye_col = QColor(129, 140, 248)
        elif expr in ("joy", "proud"):
            eye_col = QColor(253, 224, 71)
        elif expr in ("shy", "curious"):
            eye_col = QColor(244, 114, 182)
        else:
            eye_col = P["acc"] if not gray_body else QColor(148, 163, 184)
        eye_r = G["eye_r"] * s * (1.15 if self.skin == "cute" else 1.0)
        side_shift = (-16 * s) if act == "peek" else 0.0    # 探头偷看：眼睛齐齐望向一侧
        ey_l, ey_r = ex_l + side_shift, ex_r + side_shift
        pen_eye = lambda: QPen(eye_col, 2.2 * s, Qt.SolidLine, Qt.RoundCap)  # noqa: E731
        if sleeping:
            p.setPen(QPen(QColor(71, 85, 105), 2.2 * s, Qt.SolidLine, Qt.RoundCap))
            for ex in (ey_l, ey_r):
                p.drawArc(QRectF(ex - 6 * s, eye_y - 4 * s, 12 * s, 9 * s), 0, 180 * 16)
        elif expr == "sleepy" or self._blink > 0:            # 犯困 / 眨眼：半合眼睑
            p.setPen(pen_eye())
            for ex in (ey_l, ey_r):
                p.drawLine(ex - 6 * s, eye_y, ex + 6 * s, eye_y)
        else:                                                # 亮眼
            for ex in (ey_l, ey_r):
                eg = QRadialGradient(QPointF(ex, eye_y), 5.5 * s)
                eg.setColorAt(0.0, QColor(255, 255, 255, 255))
                eg.setColorAt(0.45, eye_col)
                eg.setColorAt(1.0, QColor(8, 47, 73, 255))
                p.setPen(Qt.NoPen)
                p.setBrush(eg)
                p.drawEllipse(QPointF(ex, eye_y), eye_r, eye_r)
        # 眉/泪/腮红 细节点缀（小机器人也要会"委屈""生气""害羞"）
        if expr == "angry":
            p.setPen(QPen(QColor(248, 113, 113, 230), 1.8 * s, Qt.SolidLine, Qt.RoundCap))
            for sgn in (-1, 1):                               # 眉尖向下压
                bx = ex_l if sgn < 0 else ex_r
                p.drawLine(bx - 7 * s * sgn, eye_y - 11 * s, bx + 2 * s, eye_y - 5 * s)
        elif expr == "aggrieved":
            p.setPen(QPen(QColor(129, 140, 248, 210), 1.8 * s, Qt.SolidLine, Qt.RoundCap))
            for sgn in (-1, 1):                               # 眉尾下垂
                bx = ex_l if sgn < 0 else ex_r
                p.drawLine(bx - 3 * s, eye_y - 5 * s, bx + 7 * s * sgn, eye_y - 11 * s)
            p.setPen(Qt.NoPen)                                # 泪珠
            p.setBrush(QColor(147, 197, 253, 235))
            dx = ex_r + 4 * s
            p.drawEllipse(QPointF(dx, eye_y + eye_r + 3 * s + self._poke * 2 * s),
                          1.8 * s, 3.2 * s)
        elif expr == "shy":
            p.setPen(Qt.NoPen)                                # 腮红
            for xx in (cx - 20 * s, cx + 20 * s):
                g_ = QRadialGradient(QPointF(xx, cy + 5 * s), 4.5 * s)
                g_.setColorAt(0.0, QColor(244, 114, 182, 150))
                g_.setColorAt(1.0, QColor(244, 114, 182, 0))
                p.setBrush(g_)
                p.drawEllipse(QPointF(xx, cy + 5 * s), 4.5 * s, 3.5 * s)

        # —— 嘴部：LED 表情条 ——
        mx, my = cx + self._look * 1.5 * s + side_shift * 0.6, cy + 10 * s
        p.setPen(QPen(P["acc"], 2.2 * s, Qt.SolidLine, Qt.RoundCap))
        if self.speaking:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(253, 224, 71))
            p.drawEllipse(QPointF(mx, my), 5 * s, 4.2 * s)
        elif sleeping:
            p.setPen(QPen(QColor(100, 116, 139), 2 * s))
            p.drawLine(mx - 3 * s, my, mx + 3 * s, my)
        elif expr == "joy" or expr == "proud" or (expr == "calm" and happy):
            arc_m = QPainterPath()
            arc_m.moveTo(mx - 7 * s, my - 1 * s)
            arc_m.quadTo(mx, my + 7 * s, mx + 7 * s, my - 1 * s)
            p.setPen(QPen(QColor(103, 232, 249, 235), 2.2 * s, Qt.SolidLine, Qt.RoundCap))
            p.drawPath(arc_m)
        elif expr == "aggrieved" or (expr == "calm" and low):
            arc_m = QPainterPath()                            # 撇嘴
            arc_m.moveTo(mx - 7 * s, my + 5 * s)
            arc_m.quadTo(mx, my - 2 * s, mx + 7 * s, my + 5 * s)
            p.setPen(QPen(QColor(165, 180, 252, 235), 2.2 * s, Qt.SolidLine, Qt.RoundCap))
            p.drawPath(arc_m)
        elif expr == "angry":
            p.setPen(QPen(QColor(248, 113, 113, 235), 2.4 * s, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(mx - 7 * s, my - 5 * s, mx, my + 1 * s)   # 生气的"︿"嘴
            p.drawLine(mx + 7 * s, my - 5 * s, mx, my + 1 * s)
        elif expr == "shy":
            p.setPen(QPen(QColor(244, 114, 182, 220), 2 * s, Qt.SolidLine, Qt.RoundCap))
            p.drawEllipse(QPointF(mx, my), 3 * s, 2.2 * s)       # 小声的 o
        elif expr == "curious":
            p.setPen(pen_eye())
            p.drawEllipse(QPointF(mx, my), 2.4 * s, 3 * s)
        else:
            p.drawLine(mx - 6 * s, my, mx + 6 * s, my)

        # —— 眼角高光/腮侧发光点（开心/得意）——
        if expr in ("joy", "proud") or (expr == "calm" and happy):
            p.setPen(Qt.NoPen)
            for xx in (cx - 20 * s, cx + 20 * s):
                sg = QRadialGradient(QPointF(xx, cy + 3 * s), 5 * s)
                sg.setColorAt(0.0, QColor(253, 224, 71, 120))
                sg.setColorAt(1.0, QColor(253, 224, 71, 0))
                p.setBrush(sg)
                p.drawEllipse(QPointF(xx, cy + 3 * s), 5 * s, 4 * s)

        # —— 颈部发光环 ——
        p.setPen(QPen(P["acc"], 2 * s))
        p.drawLine(cx - 12 * s, cy + 28 * s, cx + 12 * s, cy + 28 * s)
        p.setPen(QPen(QColor(99, 102, 241, 200), 1.4 * s))
        p.drawArc(QRectF(cx - 10 * s, cy + 30 * s, 20 * s, 7 * s), 0, 180 * 16)

        # —— v0.29 常态双臂（此前只有 wave 动作才画一只手，用户要求"除了头，还有身体和手脚"）——
        # 肩点取躯干两侧、颈环略下方；臂长按成长档位递增（G["arm"]），末端一颗发光小手。
        # 幼儿臂短圆润 → 青年臂长修长；走路时前后摆臂，静止时随呼吸微浮。
        _sh_y = cy + 22 * s
        _arm = G.get("arm", 13) * s
        _sw = math.sin(self._phase * 1.4) * 1.2 * s
        if self.mode == "walk":
            _sw = math.sin(self._phase * 4.0) * 4.8 * s
        elif act == "dance":
            _sw = math.sin(self._phase * 6.0) * 5.0 * s
        for _sgn in (-1, 1):
            _sh_x = cx + _sgn * (G["head_w"] / 2 - 6 * s)
            # —— v0.30.11：按当前动作改写手的目标位（默认：自然下垂微外张）——
            _dx = _sgn * 4 * s
            _dy = _arm + (_sw if _sgn > 0 else -_sw)
            if act == "stretch":
                _k = 0.55 + 0.45 * abs(math.sin(self._phase * 1.6))
                _dx = _sgn * _arm * 0.95
                _dy = -_arm * _k                       # 双臂上举过头
            elif act == "cheer":
                _k = 0.85 + 0.15 * abs(math.sin(self._phase * 6.0))
                _dx = _sgn * _arm * 0.75
                _dy = -_arm * 1.05 * _k                # 双手高举欢呼
            elif act == "arms":
                _dx = -_sgn * (G["head_w"] / 2 - 8 * s)
                _dy = _arm * 0.45                      # 双臂在胸前交叠
            elif act == "pat":
                _cl = abs(math.sin(self._phase * 7.5))
                _dx = _sgn * (6 * s - 3 * s * _cl)
                _dy = _arm * 0.30 - 2 * s * _cl        # 双手向中线合拢击掌
            elif act == "point" and _sgn > 0:
                _dx = _arm * 1.35
                _dy = -_arm * 0.35 + _sw               # 右臂斜伸指出
            elif act == "heart":
                _dx = _sgn * 7 * s
                _dy = _arm * 0.02                      # 双手胸前合拢（比心）
            elif act == "bounce":
                _dx = _sgn * 7 * s
                _dy = _arm * 0.5 - abs(math.sin(self._phase * 8.0)) * 4 * s
            _hx = _sh_x + _dx
            _hy = _sh_y + _dy
            p.setPen(QPen(QColor(125, 211, 252, 205), 3.2 * s,
                          Qt.SolidLine, Qt.RoundCap))
            p.drawLine(QPointF(_sh_x, _sh_y), QPointF(_hx, _hy))
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(224, 242, 254, 235))
            p.drawEllipse(QPointF(_hx, _hy), 3.2 * s, 3.2 * s)
            p.setBrush(QColor(34, 211, 238, 130))
            p.drawEllipse(QPointF(_hx, _hy), 1.5 * s, 1.5 * s)

        # —— 底部悬浮光环 / 腿光（幼儿期没有小脚，纯悬浮球）——
        fy = h - 5 * s
        hover_r = 14 * s
        p.setPen(QPen(P["acc"], 1.4 * s))
        p.drawEllipse(QPointF(cx, fy), hover_r, hover_r * 0.32)
        if G["leg"]:
            p.setPen(QPen(P["acc2"], 1.4 * s))
            swing = math.sin(self._phase * 4.0) * (7.5 * s if self.mode == "walk" else 0)
            for sgn in (-1, 1):
                lx = cx + sgn * (9 * s + (swing if sgn < 0 else -swing))
                p.drawEllipse(QPointF(lx, fy - 3 * s), 3.4 * s, 2.2 * s)

        # —— 阶段装饰：幼儿期头顶呆毛球；青年期肩甲（v0.29 修：原来写 >=4，永远取不到）——
        if self.growth == 0:
            pw = 4.2 * s + 0.8 * s * math.sin(self._phase * 2.6)
            pg = QRadialGradient(QPointF(cx, cy - G["head_h"] / 2 - 30 * s), pw)
            pg.setColorAt(0.0, QColor(255, 255, 255, 240))
            pg.setColorAt(1.0, QColor(253, 224, 71, 160))
            p.setBrush(pg)
            p.setPen(Qt.NoPen)
            p.drawEllipse(QPointF(cx, cy - G["head_h"] / 2 - 30 * s), pw, pw)
        elif self.growth >= 4:
            # 成年期：金色冠饰（三角冠 + 顶珠），与青年肩甲区分
            _pc = QColor(250, 204, 21)
            _ty = cy - G["head_h"] / 2 - 6 * s
            _cw = head_w / 2 - 2 * s
            p.setPen(QPen(_pc, 2.2 * s, Qt.SolidLine, Qt.RoundCap))
            p.setBrush(QColor(250, 204, 21, 200))
            _crown = [
                (cx - _cw, _ty), (cx - _cw * 0.4, _ty - 12 * s), (cx, _ty),
                (cx + _cw * 0.4, _ty - 12 * s), (cx + _cw, _ty),
            ]
            for _i in range(len(_crown) - 1):
                p.drawLine(QPointF(*_crown[_i]), QPointF(*_crown[_i + 1]))
            p.setBrush(QColor(255, 240, 150, 255))
            p.drawEllipse(QPointF(cx, _ty - 13 * s), 2.4 * s, 2.4 * s)
        elif self.growth >= 3:
            # 青年期：肩甲
            p.setPen(QPen(P["ear0"], 2 * s, Qt.SolidLine, Qt.RoundCap))
            for sgn in (-1, 1):
                p.drawLine(cx + sgn * (head_w / 2 - 4 * s), cy + 26 * s,
                           cx + sgn * (head_w / 2 + 4 * s), cy + 22 * s)

        # —— 睡眠：Zzz 全息字 ——
        if sleeping:
            self._zz = (self._zz + 0.03) % 3.0
            p.setFont(QFont("Arial", int(9 * s + 5), QFont.Bold))
            for k, al in ((0.0, 100), (1.0, 150), (2.0, 210)):
                zy = cy - 52 * s - k * 9 * s - self._zz * 8 * s
                p.setPen(QColor(129, 140, 248, al))
                p.drawText(QRectF(cx + 16 * s, zy, 22 * s, 14 * s),
                           Qt.AlignLeft, "Z" * (3 - int(k)))
        elif reading:
            # 全息小书（右侧）
            bx, by = cx + 14 * s, cy + 6 * s
            bk = QRectF(bx, by, 22 * s, 16 * s)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(255, 255, 255, 235))
            p.drawRoundedRect(bk, 3 * s, 3 * s)
            p.setPen(QPen(P["acc2"], 1.3 * s))
            p.drawLine(bx + 11 * s, by, bx + 11 * s, by + 16 * s)
            p.drawLine(bx + 3 * s, by + 6 * s, bx + 9 * s, by + 6 * s)
            p.drawLine(bx + 13 * s, by + 6 * s, bx + 19 * s, by + 6 * s)
            p.drawLine(bx + 3 * s, by + 11 * s, bx + 9 * s, by + 11 * s)
            p.drawLine(bx + 13 * s, by + 11 * s, bx + 19 * s, by + 11 * s)
            # 翻书粒子
            p.setBrush(QColor(103, 232, 249, 190))
            fx = bx + 2 * s + abs(math.sin(self._phase * 3)) * 18 * s
            p.drawEllipse(QPointF(fx, by - 3 * s), 1.6 * s, 1.6 * s)

        # —— 动作帧（P3）：跳舞音符 / 踢球玩耍 ——
        if act == "dance":
            p.setPen(Qt.NoPen)
            for k in range(3):
                ny = cy - (46 + k * 10) * s - abs(math.sin(self._phase * 3 + k)) * 4 * s
                nx = cx + (6 + k * 9) * s
                p.setBrush(QColor(253, 224, 71, 210 - k * 40))
                p.drawEllipse(QPointF(nx, ny), 3 * s, 3 * s)
            p.setPen(QPen(QColor(253, 224, 71, 170), 1.6 * s, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(cx + 9 * s, cy - 47 * s, cx + 13 * s, cy - 52 * s)
            p.drawLine(cx + 17 * s, cy - 47 * s, cx + 21 * s, cy - 52 * s)
        elif act == "ball":
            py = h - 8 * s - abs(math.sin(self._phase * 5.0)) * 20 * s
            px = cx + math.sin(self._phase * 2.2) * 18 * s
            p.setPen(Qt.NoPen)
            g_ = QRadialGradient(QPointF(px, py), 6 * s)
            g_.setColorAt(0.0, QColor(253, 224, 71, 255))
            g_.setColorAt(1.0, QColor(234, 88, 12, 255))
            p.setBrush(g_)
            p.drawEllipse(QPointF(px, py), 5 * s, 5 * s)
        elif act == "spin":
            # 转圈：环绕身体的运动残影 + 速度线
            p.setPen(Qt.NoPen)
            for k in range(3):
                ang = self._phase * 6.0 + k * (2 * math.pi / 3)
                rx = cx + math.cos(ang) * 30 * s
                ry = cy - 6 * s + math.sin(ang) * 22 * s
                p.setBrush(QColor(34, 211, 238, 120 - k * 30))
                p.drawEllipse(QPointF(rx, ry), 2.4 * s, 2.4 * s)
            p.setPen(QPen(QColor(34, 211, 238, 150), 1.4 * s,
                           Qt.SolidLine, Qt.RoundCap))
            for k in range(4):
                a = self._phase * 6.0 + k * (math.pi / 2)
                p.drawLine(cx + math.cos(a) * 24 * s, cy + math.sin(a) * 18 * s,
                           cx + math.cos(a) * 30 * s, cy + math.sin(a) * 22 * s)
        elif act == "think":
            # 思考：头顶冒出"思考泡泡"——一颗小云 + 三点
            bx0, by0 = cx + 14 * s, cy - 44 * s
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(224, 242, 254, 210))
            p.drawEllipse(QPointF(bx0 + 7 * s, by0 + 8 * s), 5 * s, 4 * s)
            p.drawEllipse(QPointF(bx0 + 1 * s, by0 + 3 * s), 3.4 * s, 3 * s)
            p.drawEllipse(QPointF(bx0 + 3 * s, by0 - 3 * s), 4.4 * s, 4 * s)
            p.setBrush(QColor(56, 189, 248, 230))
            for k, (dx, dy) in enumerate(((3, -3), (6, 0), (9, 3))):
                rr = (2.0 - k * 0.3) * s
                p.drawEllipse(QPointF(bx0 + dx * s, by0 + dy * s), rr, rr)
        elif act == "wave":
            # 挥手：右肩伸出一只小手，上下摆动
            sx = cx + G["head_w"] * 0.42 * s
            sy = cy - 4 * s
            wob = math.sin(self._phase * 7.0) * 6 * s
            hx = sx + 12 * s
            hy = sy - 10 * s + wob
            p.setPen(QPen(QColor(125, 211, 252, 230), 3.0 * s,
                           Qt.SolidLine, Qt.RoundCap))
            p.drawLine(QPointF(sx, sy), QPointF(hx, hy))
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(224, 242, 254, 240))
            p.drawEllipse(QPointF(hx, hy), 3.6 * s, 3.6 * s)
            p.setBrush(QColor(34, 211, 238, 150))
            p.drawEllipse(QPointF(hx, hy - 8 * s), 1.6 * s, 1.6 * s)
        # ================= v0.30.11 动作扩充：新动作的样式帧 =================
        elif act == "nod":
            # 点头致意：头两侧各画一段"点头"运动弧
            p.setPen(QPen(QColor(34, 211, 238, 170), 1.6 * s,
                          Qt.SolidLine, Qt.RoundCap))
            for _sx, _dir in ((cx - 26 * s, -1), (cx + 26 * s, 1)):
                p.drawArc(QRectF(_sx - 5 * s, cy - 30 * s, 10 * s, 12 * s),
                          int((90 + _dir * 40) * 16), int(120 * 16))
        elif act == "stretch":
            # 伸懒腰：头顶上方三颗"舒展开"的星点
            p.setPen(Qt.NoPen)
            for _k in range(3):
                _st = abs(math.sin(self._phase * 2.2 + _k))
                p.setBrush(QColor(103, 232, 249, 210 - _k * 45))
                p.drawEllipse(QPointF(cx + (_k - 1) * 12 * s,
                                      cy - (58 + _st * 8) * s), 2.2 * s, 2.2 * s)
        elif act == "arms":
            # 抱臂：胸前一道"沉稳"的冷光横线
            p.setPen(QPen(QColor(148, 163, 184, 150), 1.4 * s, Qt.SolidLine,
                          Qt.RoundCap))
            p.drawLine(QPointF(cx - 12 * s, cy + 24 * s),
                       QPointF(cx + 12 * s, cy + 24 * s))
        elif act == "bounce":
            # 弹跳撒欢：脚下速度线 + 小尘点
            p.setPen(QPen(QColor(34, 211, 238, 150), 1.4 * s, Qt.SolidLine,
                          Qt.RoundCap))
            for _sgn2 in (-1, 1):
                p.drawLine(QPointF(cx + _sgn2 * 20 * s, fy - 2 * s),
                           QPointF(cx + _sgn2 * (28 + abs(math.sin(self._phase * 8)) * 5) * s,
                                   fy + 3 * s))
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(224, 242, 254, 180))
            p.drawEllipse(QPointF(cx - 16 * s, fy + 1 * s), 1.6 * s, 1.6 * s)
            p.drawEllipse(QPointF(cx + 16 * s, fy + 1 * s), 1.6 * s, 1.6 * s)
        elif act == "cheer":
            # 欢呼：双手上方迸出星光（六向放射）
            _hyc = cy + 22 * s - _arm * 1.05
            for _sgn2 in (-1, 1):
                _cx0 = cx + _sgn2 * _arm * 0.75
                p.setPen(QPen(QColor(253, 224, 71, 190), 1.5 * s, Qt.SolidLine,
                              Qt.RoundCap))
                for _k2 in range(4):
                    _a2 = self._phase * 4.0 + _k2 * (math.pi / 2)
                    _r0, _r1 = 4 * s, 9 * s
                    p.drawLine(QPointF(_cx0 + math.cos(_a2) * _r0, _hyc + math.sin(_a2) * _r0),
                               QPointF(_cx0 + math.cos(_a2) * _r1, _hyc + math.sin(_a2) * _r1))
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(255, 240, 150, 220))
                p.drawEllipse(QPointF(_cx0, _hyc), 2.4 * s, 2.4 * s)
        elif act == "pat":
            # 鼓掌：双手相击处迸出光点
            _cl = abs(math.sin(self._phase * 7.5))
            _px2, _py2 = cx, cy + 22 * s + _arm * 0.30 - 2 * s * _cl
            p.setPen(Qt.NoPen)
            for _k2 in range(3):
                p.setBrush(QColor(253, 224, 71, 200 - _k2 * 50))
                _a2 = self._phase * 5.0 + _k2 * 2.1
                p.drawEllipse(QPointF(_px2 + math.cos(_a2) * (7 + _k2 * 3) * s,
                                      _py2 + math.sin(_a2) * (6 + _k2 * 2) * s),
                              1.8 * s, 1.8 * s)
        elif act == "point":
            # 指一指：指尖一颗提示光 + 一道指示射线
            _tipx = cx + (G["head_w"] / 2 - 6 * s) + _arm * 1.35
            _tipy = cy + 22 * s - _arm * 0.35
            p.setPen(QPen(QColor(56, 189, 248, 160), 1.6 * s, Qt.SolidLine,
                          Qt.RoundCap))
            p.drawLine(QPointF(_tipx + 4 * s, _tipy - 4 * s),
                       QPointF(_tipx + 12 * s, _tipy - 11 * s))
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(103, 232, 249, 230))
            p.drawEllipse(QPointF(_tipx, _tipy), 2.2 * s, 2.2 * s)
        elif act == "heart":
            # 比心：双手上方升起一串小爱心
            for _k2 in range(3):
                _pr = (self._phase * 0.9 + _k2 / 3.0) % 1.0
                _hx2 = cx + math.sin(self._phase * 1.6 + _k2) * 5 * s
                _hy2 = cy + 20 * s - _pr * 34 * s
                _al = int(220 * (1.0 - _pr))
                if _al <= 6:
                    continue
                _hr = (3.4 - _pr * 1.2) * s
                p.setBrush(QColor(244, 114, 182, _al))
                p.setPen(Qt.NoPen)
                p.drawEllipse(QPointF(_hx2 - _hr * 0.5, _hy2), _hr * 0.6, _hr * 0.6)
                p.drawEllipse(QPointF(_hx2 + _hr * 0.5, _hy2), _hr * 0.6, _hr * 0.6)
                _hp = QPainterPath()
                _hp.moveTo(_hx2 - _hr, _hy2 + _hr * 0.1)
                _hp.lineTo(_hx2, _hy2 + _hr * 1.5)
                _hp.lineTo(_hx2 + _hr, _hy2 + _hr * 0.1)
                _hp.closeSubpath()
                p.drawPath(_hp)
        # —— 头顶工作气泡（与 3D 路径共用同一实现）——
        self._paint_work_bubble(p, w, h, cx, s)
        self._paint_trail(p, w, h, s)          # ★ 人形分支也拖尾
        p.end()
