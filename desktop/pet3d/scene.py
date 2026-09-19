# -*- coding: utf-8 -*-
"""场景装配 —— 把「骨骼 + 动作 + 配色」合成一个可反复出图的单元。

这一层是 PetAvatar 唯一需要认识的接口：喂给它状态（成长/皮肤/表情/动作），
它吐出一张带 alpha 的 QImage。
"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage

from . import anim as ANIM
from . import renderer as REND
from .mat import Palette
from .rig import Rig


class Scene(object):
    """一个 3D 小人实例（持有全部可复现状态）。"""

    def __init__(self, growth=1, skin="royal"):
        self.growth = int(growth)
        self.skin = skin
        self._hide = frozenset()        # v0.30.11 #12：形象 DIY 头饰开关
        self.rig = Rig(self.growth, self._hide)
        self.anim = ANIM.Animator(self.rig)
        self.pal = Palette(skin, self.growth)
        self._painted_once = False

    # ---------- 状态 ----------
    def set_growth(self, g):
        g = int(g)
        if g == self.growth:
            return False
        self.growth = g
        self.rig = Rig(g, self._hide)
        self.anim = ANIM.Animator(self.rig)
        self._painted_once = False
        return True

    def set_skin(self, skin):
        if skin == self.skin:
            return False
        self.skin = skin
        return True

    def set_look(self, hide):
        """v0.30.11 #12 形象 DIY：头饰开关变化 → 重建 rig。

        3D 的"隐藏部件"只能靠**装配时不加**，所以集合一变就得重建
        （代价 = 重传 VBO，约 30ms 级；开关是低频操作，可接受）。
        集合没变时**不做任何事**（本方法每帧都会被调用）。
        返回 True 表示刚重建过（调用方应改用 freeze 起手，别从小人零姿态"长"出来）。
        """
        hide = frozenset(hide or ())
        if hide == self._hide:
            return False
        self._hide = hide
        self.rig = Rig(self.growth, hide)
        self.anim = ANIM.Animator(self.rig)
        self._painted_once = False
        return True

    # ---------- 逐帧 ----------
    def advance(self, dt, act, mode, expr, look=0.0, speaking=False,
                valence=0.0, facing=0.0):
        self.anim.update(dt, act, mode, expr, look, speaking, valence, facing)
        self.pal = Palette(self.skin, self.growth, expr, valence)

    def freeze(self, act, mode, expr, look=0.0, speaking=False, valence=0.0,
               facing=0.0):
        """不做插值、直接到位（首帧用，避免小人从零姿态"长"出来）。"""
        self.anim.jump(act, mode, expr, look, speaking, valence, facing)
        self.pal = Palette(self.skin, self.growth, expr, valence)

    # ---------- 出图 ----------
    def image(self, size_px, supersample=None):
        r = REND.renderer()
        if supersample is not None:
            r.ss = max(1, int(supersample))
        img = r.render(self.rig, self.pal, int(size_px))
        if img is not None:
            self._painted_once = True
        return img

    # ---------- 自省 ----------
    def describe(self):
        return {
            "growth": self.growth,
            "skin": self.skin,
            "parts": len(self.rig.parts),
            "bones": len(self.rig.bones),
            "height": round(self.rig.bones["head"].world[13] +
                            self.rig.profile["head"] * 0.95, 4),
        }


def available():
    """本机能否跑 GPU 3D。"""
    return REND.probe()


def shutdown():
    REND.shutdown()


def composite(base, img, dx=0, dy=0):
    """把渲染结果叠到目标图上（保持 alpha）。"""
    if img is None:
        return base
    from PySide6.QtGui import QPainter
    p = QPainter(base)
    p.setRenderHint(QPainter.Antialiasing)
    p.drawImage(dx, dy, img)
    p.end()
    return base
