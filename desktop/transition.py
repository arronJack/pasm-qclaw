# -*- coding: utf-8 -*-
"""页面转场动效 —— 拿的是方法论，不是别人的实现。

方法论（参考 scene-shell 那套「组件自带 enter/exit/morph + intent 三档」的思路）：

    intent     时长    观感                     用在哪
    ambient    340ms  呼吸式慢淡入，最柔        后台/低频切换
    inform     240ms  常规淡入 + 轻微下移       默认（导航栏点击）
    confront   170ms  快、利落、位移更明显      需要"打断感"的切换

为什么不引 QWebEngineView 去复刻原版效果：
  ① 包体 +150MB；② 等于把上万行的 Qt 界面**整体重写成网页** —— 那是重做产品，
  不是加功能。Qt 原生这条路（QPropertyAnimation + QGraphicsOpacityEffect）效果
  到"顺滑有质感"，但**零新依赖、零重写**。

两个必须守住的工程约束：
  · **连切不能错乱**：上一个动画必须先停掉并清理干净（否则闪一下或停在半路）；
  · **不阻塞**：动画全程异步，切换后的数据刷新与流式输出照常跑。
"""
from __future__ import annotations

import sys

# intent → (时长ms, 起始不透明度, 位移px, 缓动名)
# 位移只在页面有 layout 且 intent 需要时生效（见 _shift）。
PRESETS = {
    "ambient":  dict(ms=340, fade=0.0, dy=0,  ease="OutCubic"),
    "inform":   dict(ms=240, fade=0.0, dy=14, ease="OutCubic"),
    "confront": dict(ms=170, fade=0.0, dy=26, ease="OutBack"),
}
DEFAULT_INTENT = "inform"
INTENTS = tuple(PRESETS)


def preset(intent) -> dict:
    """取档位参数；未知档位一律退回默认（绝不抛异常）。"""
    try:
        return PRESETS.get(str(intent or "").strip().lower(), PRESETS[DEFAULT_INTENT])
    except Exception:
        return PRESETS[DEFAULT_INTENT]


class PageTransition(object):
    """可复用的页面转场播放器 —— **一个窗口只建一个，反复用**。

    为什么必须复用实例：每次新建 QPropertyAnimation 会在连切时留下未停止的
    动画对象，它们继续改 opacity，表现就是"切页闪一下 / 停在半路"。
    """

    def __init__(self, stack):
        self.stack = stack
        self._anim = None
        self._eff = None
        self._host = None
        self.playing = False
        self.last_intent = ""
        self.plays = 0

    # ---------- 核心 ----------
    def stop(self):
        """立即停下并**彻底清理**上一个动画（连切前必调）。

        清理必须做两件事：停动画 + 摘掉 graphics effect。
        只停不停 effect 的话，页面会被永久套着一层透明度效果 ——
        表现为"某些页面看起来总是发灰"。
        """
        a = self._anim
        self._anim = None
        if a is not None:
            try:
                a.stop()
            except Exception:
                pass
            try:
                a.deleteLater()
            except Exception:
                pass
        h = self._host
        self._host = None
        self._eff = None
        if h is not None:
            try:
                h.setGraphicsEffect(None)
            except Exception:
                pass
        self.playing = False

    def go(self, index, intent=DEFAULT_INTENT):
        """切到第 index 页并播放入场动画。返回是否真的发生了切换。"""
        st = self.stack
        if st is None or index is None:
            return False
        try:
            if int(index) == int(st.currentIndex()):
                return False
        except Exception:
            return False

        cfg = preset(intent)
        self.stop()                       # 先清上一个（连切保护）
        st.setCurrentIndex(int(index))    # 先切页：内容立即可用，不等动画
        self.last_intent = str(intent or DEFAULT_INTENT)
        self.plays += 1

        w = None
        try:
            w = st.currentWidget()
        except Exception:
            w = None
        if w is None:
            return True
        if not self._animate(w, cfg):
            return True
        return True

    # ---------- 内部 ----------
    def _animate(self, widget, cfg):
        from qt_compat import QEasingCurve, QGraphicsOpacityEffect, QPropertyAnimation
        try:
            eff = QGraphicsOpacityEffect(widget)
            eff.setOpacity(float(cfg["fade"]))
            widget.setGraphicsEffect(eff)
        except Exception:
            return False          # 拿不到 effect 就只做"硬切"，不影响功能

        try:
            anim = QPropertyAnimation(eff, b"opacity", widget)
            anim.setDuration(int(cfg["ms"]))
            anim.setStartValue(float(cfg["fade"]))
            anim.setEndValue(1.0)
            try:
                anim.setEasingCurve(getattr(QEasingCurve, cfg["ease"]))
            except Exception:
                anim.setEasingCurve(QEasingCurve.OutCubic)
            # 动画结束就摘掉 effect —— 不摘会让页面永久套一层透明度层，
            # 复杂页面上还白吃一次离屏合成。
            anim.finished.connect(lambda h=widget: self._finish(h))
            self._host = widget
            self._eff = eff
            self._anim = anim
            self.playing = True
            anim.start()
            self._shift(widget, cfg)
            return True
        except Exception:
            try:
                widget.setGraphicsEffect(None)
            except Exception:
                pass
            return False

    def _finish(self, host):
        """动画收尾：把 effect 摘干净（幂等 —— 连切时可能已被 stop 清过）。"""
        if host is not self._host:
            return
        try:
            host.setGraphicsEffect(None)
        except Exception:
            pass
        self._eff = None
        self._anim = None
        self._host = None
        self.playing = False

    def _shift(self, widget, cfg):
        """可选的"位移感"：动画 layout 的上边距。

        只在 intent 需要（dy>0）且页面**确实有 layout** 时才做。
        之所以不用 pos/geometry 动画：QStackedWidget 里页面的位置由布局掌管，
        直接动 pos 会被下一帧布局覆盖，表现就是"位移没效果"。
        """
        dy = int(cfg.get("dy") or 0)
        if dy <= 0:
            return
        lay = None
        try:
            lay = widget.layout()
        except Exception:
            lay = None
        if lay is None:
            return
        try:
            from qt_compat import QEasingCurve, QPropertyAnimation
            m0 = lay.contentsMargins()
            anim = QPropertyAnimation(lay, b"contentsMargins", widget)
            from qt_compat import QMargins
            anim.setDuration(int(cfg["ms"]))
            anim.setStartValue(QMargins(m0.left(), m0.top() + dy,
                                        m0.right(), max(0, m0.bottom() - dy)))
            anim.setEndValue(m0)
            try:
                anim.setEasingCurve(getattr(QEasingCurve, cfg["ease"]))
            except Exception:
                pass
            anim.start()
            self._margins_anim = anim
        except Exception:
            pass          # 位移是锦上添花，失败不影响淡入


# ——————————————————————————————————————————————————————
# 自检（离屏 Qt；验证的是"真的切了页、真的播了动画、连切不出错"）
# ——————————————————————————————————————————————————————

def selftest() -> int:
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

    passed = failed = 0

    def check(name, cond, extra=""):
        nonlocal passed, failed
        if cond:
            passed += 1
            print("PASS %s %s" % (name, extra))
        else:
            failed += 1
            print("FAIL %s %s" % (name, extra))

    # 1) 纯逻辑（不需要 Qt）
    check("三档 intent 齐全", set(INTENTS) == {"ambient", "inform", "confront"})
    check("ambient 比 inform 慢", preset("ambient")["ms"] > preset("inform")["ms"])
    check("confront 比 inform 快", preset("confront")["ms"] < preset("inform")["ms"])
    check("未知 intent 退回默认",
          preset("花里胡哨") == preset(DEFAULT_INTENT) and preset(None) == preset(DEFAULT_INTENT))
    check("所有档位时长在合理区间",
          all(80 <= p["ms"] <= 600 for p in PRESETS.values()))

    try:
        from qt_compat import (QApplication, QLabel, QStackedWidget, QVBoxLayout,
                               QWidget)
    except Exception as e:
        print("SKIP Qt 部分（%s）" % e)
        print("\n%d 项，%s" % (passed + failed, "全部通过" if not failed else "%d 项失败" % failed))
        return 1 if failed else 0

    app = QApplication.instance() or QApplication([])
    stack = QStackedWidget()
    stack.resize(400, 300)
    for i in range(4):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.addWidget(QLabel("页 %d" % i))
        stack.addWidget(page)
    stack.show()

    tr = PageTransition(stack)

    # 2) 正常切页
    ok = tr.go(2, "inform")
    check("切页返回 True", ok is True)
    check("currentIndex 真的变了", stack.currentIndex() == 2, "现在是 %d" % stack.currentIndex())
    check("动画在播", tr.playing is True and tr._anim is not None)
    check("页面套上了透明度效果", stack.currentWidget().graphicsEffect() is not None)

    # 3) 切到当前页 = 不动作
    check("切到当前页返回 False", tr.go(2, "inform") is False)

    # 4) 连切不出错乱（连续 6 次快速切）
    seq_err = 0
    for i in (0, 3, 1, 2, 0, 3):
        try:
            tr.go(i, "confront")
            if stack.currentIndex() != i:
                seq_err += 1
        except Exception as e:
            seq_err += 1
            print("   连切异常:", e)
    check("连续快速切换不错乱", seq_err == 0, "错误 %d 次" % seq_err)
    check("连切后仍在播动画", tr.playing is True)

    # 5) stop 必须清干净（否则页面永久发灰）
    tr.stop()
    check("stop 后动画停", tr.playing is False and tr._anim is None)
    check("stop 后 effect 被摘掉",
          stack.currentWidget().graphicsEffect() is None)
    tr.stop()
    check("stop 幂等（可重复调）", tr.playing is False)

    # 6) 脏输入不崩
    try:
        r1 = tr.go(None, "inform")
        r2 = tr.go(999, "inform")     # 越界交给 QStackedWidget 自己兜（不崩即可）
        r3 = tr.go("2", "不存在的档位")
        ok_dirty = (r1 is False) and isinstance(r2, bool) and isinstance(r3, bool)
    except Exception as e:
        ok_dirty = False
        print("   脏输入异常:", e)
    check("脏输入不崩且返回布尔", ok_dirty)
    tr.stop()

    # 7) 三档都能真的跑一遍
    allok = True
    for it in INTENTS:
        try:
            tr.stop()
            tr.go(1, it)
            app.processEvents()
            if stack.currentIndex() != 1:
                allok = False
        except Exception as e:
            allok = False
            print("   %s 异常: %s" % (it, e))
    check("三档 intent 都能跑通", allok)
    tr.stop()

    print("\n%d 项，%s" % (passed + failed, "全部通过" if not failed else "%d 项失败" % failed))
    print("（离屏 Qt，不显示窗口、不碰你的任何文件）")
    return 1 if failed else 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        raise SystemExit(selftest())
    print(__doc__)
