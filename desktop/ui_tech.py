# -*- coding: utf-8 -*-
"""ui_tech —— 界面「科技感」零件（v0.30.6）。

为什么单独成模块
----------------
小志的要求是「增加科幻/科技感，比如动态线状，说话时会变化」。这类东西如果
直接写进 `pasm_companion.py`（已 12000+ 行），会有两个后果：
  ① 波形是有**纯函数**核心的（相位 → 采样点），塞进巨型 UI 文件就没法单独测；
  ② 定时重绘是**性能敏感**的（每 40ms 一次全量 repaint），必须有明确的
     「不可见就不跑」规则，写在业务文件里会被后来的人改坏。

所以这里只放：**纯函数（可测）+ 一个自绘小部件（可降级）**，不含任何业务判断。

设计约定
--------
- 只依赖 `qt_compat`，不 import 任何业务模块 → 可以被任意页面复用、也不会引入环。
- **不可见必须停表**：`showEvent/hideEvent` 控制 QTimer，parent 隐藏时立刻停。
  宁可少动，也不要为了好看让整个界面在后台空转重绘。
- `set_active()` 幂等；重复调用同一个值不重绘、不重启定时器。
"""
from __future__ import annotations

import math

from qt_compat import (QColor, QLinearGradient, QPainter, QPainterPath, QPen,
                       QWidget, QTimer, Qt)

# —— 配色（与产品主色一致：青蓝科技风）——
C_WAVE_A = "#22d3ee"      # 青
C_WAVE_B = "#0ea5e9"      # 蓝
C_IDLE = "#cbd5e1"        # 静止时的浅灰蓝


def wave_points(width: int, height: int, phase: float, active: bool = False,
                level: float = 0.0, samples: int = 0):
    """把「相位」映射成一串采样点 —— **纯函数，可单测**。

    返回 [(x, y), ...]（float 像素坐标）。

    波形 = 主波（低频大形） + 二次谐波（细节）：
    - 静止（active=False）：振幅压到 22%，节奏慢 —— 像"待机呼吸"，不抢注意力；
    - 说话（active=True）：振幅拉满，且谐波比例提高 —— 像声波在跳。
    `level`（0~1）再叠一层整体增益，用于"情绪越强、波形越明显"。

    采样点数默认按宽度取（约每 3px 一个点），过密无意义、过疏会看到折线。
    """
    w = max(1, int(width))
    h = max(1, int(height))
    if samples <= 0:
        samples = max(8, int(w / 3))
    # 振幅预算（v0.30.6 两轮修正，两次都是自检抓出来的）：
    #   第一版 amp_live=0.50h + 谐波 → 撞上 0.62h 钳制，level 增益被钳没；
    #   第二版 0.26h → 增益可见了，但**谐波叠加后越出 h/2**，波形被上下边界裁平。
    # 现在按「峰值」而不是「基波」做预算：
    #   峰值偏移 = amp × (1 + 谐波比) ≤ h/2 的一半留白  →  amp ≤ 0.34h / 1.34
    # 取 amp_live=0.20h、满增益 1.6× → 峰值 0.858×h/2，永不触边。
    harm = 0.34 if active else 0.16
    amp_idle = h * 0.11
    amp_live = h * 0.20
    amp = (amp_live if active else amp_idle) * (1.0 + 0.6 * max(0.0, min(1.0, level)))
    amp = min(amp, h * 0.34 / (1.0 + harm))       # 保险：按峰值反推的上限
    k = 3.1 if active else 2.2                    # 主波频率（说话时更密）
    cy = h / 2.0
    pts = []
    for i in range(samples + 1):
        t = i / float(samples)
        x = t * w
        y = cy + math.sin(t * math.tau * k + phase) * amp
        y += math.sin(t * math.tau * (k * 2.7) + phase * 1.7) * amp * harm
        pts.append((x, max(0.0, min(float(h), y))))
    return pts


def card_qss(radius: int = 12, accent: str = C_WAVE_B) -> str:
    """小人卡片的外观（浅色渐变 + 细描边）。

    刻意用**浅色**：产品整体是浅色主题，深色卡片会突兀；科技感靠
    渐变、描边色和那条动态波形线来给，而不是靠把底压黑。
    """
    return (
        "QFrame#techcard{"
        "border:1px solid %s;"
        "border-radius:%dpx;"
        "background:qlineargradient(x1:0,y1:0,x2:1,y2:0,"
        "stop:0 #f0f9ff, stop:0.55 #f8fbff, stop:1 #eef7ff);}"
        % (accent, radius)
    )


class VoiceWave(QWidget):
    """一条会动的波形线：它说话时振幅变大、节奏加快。

    它只画线，不做任何业务判断 —— 调用方用 `set_active()` 告诉它"现在在不在说话"。
    """

    def __init__(self, parent=None, height: int = 22, level: float = 0.0):
        super().__init__(parent)
        self.setFixedHeight(int(height))
        self.setMinimumWidth(60)
        self._phase = 0.0
        self._active = False
        self._level = float(level)
        self._timer = QTimer(self)
        self._timer.setInterval(40)          # 25fps：够顺，且不至于让界面忙
        self._timer.timeout.connect(self._tick)
        # 静止时更慢：待机呼吸不需要 25fps
        self._idle_interval = 90

    # —— 对外接口 ——
    def set_active(self, on: bool):
        """在不在"说话"。幂等：值没变就不动定时器、不重绘。"""
        on = bool(on)
        if on == self._active:
            return
        self._active = on
        self._sync_timer()
        self.update()

    def is_active(self) -> bool:
        return self._active

    def set_level(self, level: float):
        """0~1 的整体增益（情绪越强波形越明显）。"""
        try:
            lv = max(0.0, min(1.0, float(level)))
        except Exception:
            return
        if abs(lv - self._level) < 1e-3:
            return
        self._level = lv
        self.update()

    # —— 生命周期：不可见就停表（省 CPU，也避免后台空转）——
    def showEvent(self, ev):
        super().showEvent(ev)
        self._sync_timer()

    def hideEvent(self, ev):
        self._timer.stop()
        super().hideEvent(ev)

    def _sync_timer(self):
        if not self.isVisible():
            self._timer.stop()
            return
        want = 40 if self._active else self._idle_interval
        if self._timer.interval() != want:
            self._timer.setInterval(want)
        if not self._timer.isActive():
            self._timer.start()

    def _tick(self):
        if not self.isVisible():
            self._timer.stop()               # 兜底：parent 隐藏时 pyhide 不一定触发
            return
        self._phase += 0.42 if self._active else 0.16
        self.update()

    def paintEvent(self, _ev):
        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.Antialiasing, True)
            w, h = self.width(), self.height()
            pts = wave_points(w, h, self._phase, self._active, self._level)
            if len(pts) < 2:
                return
            path = QPainterPath()
            path.moveTo(pts[0][0], pts[0][1])
            for x, y in pts[1:]:
                path.lineTo(x, y)
            grad = QLinearGradient(0, 0, w, 0)
            if self._active:
                grad.setColorAt(0.0, QColor(C_WAVE_A))
                grad.setColorAt(1.0, QColor(C_WAVE_B))
            else:
                grad.setColorAt(0.0, QColor(C_IDLE))
                grad.setColorAt(1.0, QColor(C_IDLE))
            pen = QPen(grad, 2.0)
            pen.setCapStyle(Qt.RoundCap)
            pen.setJoinStyle(Qt.RoundJoin)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawPath(path)
        finally:
            p.end()


class TechCard(QWidget):
    """带发光描边的卡片容器（把内容 setLayout 进来即可）。

    用 QWidget + QSS 而不是 QFrame：QSS 在 QWidget 上要 `setAttribute`，
    这里统一处理好，业务侧只管往里塞控件。
    """

    def __init__(self, parent=None, radius: int = 12, accent: str = C_WAVE_B):
        super().__init__(parent)
        self.setObjectName("techcard")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(card_qss(radius, accent))


def _selftest() -> int:
    """自检：纯函数部分逐条验，控件部分用 offscreen 构造。"""
    fails = []

    def check(name, cond, extra=""):
        print("  %s %s%s" % ("[OK]  " if cond else "[FAIL]", name,
                             ("  " + str(extra)) if (extra and not cond) else ""))
        if not cond:
            fails.append(name)

    print("=== ui_tech 自检 ===")

    # ① 采样点数与形状
    pts = wave_points(120, 20, 0.0, False)
    check("返回成对坐标", all(len(t) == 2 for t in pts), pts[:2])
    check("点数 ≥ 8", len(pts) >= 8, len(pts))
    check("x 单调不减", all(pts[i][0] <= pts[i + 1][0] for i in range(len(pts) - 1)))
    check("x 落在宽度内", pts[0][0] == 0 and abs(pts[-1][0] - 120) < 1e-6,
          (pts[0][0], pts[-1][0]))
    check("y 不越界", all(0.0 <= y <= 20.0 for _, y in pts),
          [y for _, y in pts if not (0 <= y <= 20)])

    # ② 说话时振幅必须更大（这是「说话时会变化」的核心承诺，必须可证伪）
    def span(active):
        ps = wave_points(200, 40, 1.7, active)
        ys = [y for _, y in ps]
        return max(ys) - min(ys)
    s_idle, s_live = span(False), span(True)
    check("说话时波形明显更大", s_live > s_idle * 1.6, (round(s_idle, 2), round(s_live, 2)))

    # ③ level 增益
    s0 = span(True)
    ps = wave_points(200, 40, 1.7, True, level=1.0)
    s1 = max(y for _, y in ps) - min(y for _, y in ps)
    check("level 提高会拉大波形", s1 > s0, (round(s0, 2), round(s1, 2)))
    check("振幅有上限（不画出格子）",
          s1 <= 40.0 * 0.95, round(s1, 2))
    # 「不触边」是硬要求：一旦触边就被裁平，看起来像条直线，反而更丑
    for _lv in (0.0, 0.5, 1.0):
        for _act in (False, True):
            _ys = [y for _, y in wave_points(200, 22, 3.3, _act, _lv)]
            _m = min(_ys); _M = max(_ys)
            check("不触边(lv=%.1f,active=%s)" % (_lv, _act),
                  _m > 0.5 and _M < 21.5, (round(_m, 2), round(_M, 2)))

    # ④ 边界：宽/高为 0 或负数不该崩
    for w, h in ((0, 0), (-5, -5), (1, 1)):
        try:
            p2 = wave_points(w, h, 0.0, True)
            ok = isinstance(p2, list) and len(p2) >= 8
        except Exception as ex:
            ok = False
            print("      ↑ %sx%s 抛了 %r" % (w, h, ex))
        check("退化尺寸 %sx%s 不崩" % (w, h), ok)

    # ⑤ 卡片 QSS 含渐变（科技感靠它，不能退化成纯色）
    q = card_qss()
    check("卡片样式带渐变", "qlineargradient" in q, q[:40])
    check("卡片样式带描边色", C_WAVE_B in q)

    # ⑥ 控件：offscreen 构造 + 幂等 + 不可见不跑表
    try:
        from qt_compat import QApplication
        app = QApplication.instance() or QApplication([])
        wv = VoiceWave()
        check("初始为静止", wv.is_active() is False)
        wv.set_active(True)
        check("set_active(True) 生效", wv.is_active() is True)
        wv.set_active(True)                       # 幂等
        check("重复 set_active 不报错", wv.is_active() is True)
        wv.set_level(0.5)
        wv.set_level("坏值")                       # 不该崩
        check("set_level 容错", True)
        check("不可见时不跑定时器", not wv._timer.isActive(), wv._timer.interval())
        tc = TechCard()
        check("TechCard 可构造", tc.objectName() == "techcard")
        del app
    except Exception as ex:
        check("Qt 控件构造", False, repr(ex))

    print("-" * 46)
    if fails:
        print("自检失败 %d 项：%s" % (len(fails), "；".join(fails)))
        return 1
    print("自检通过（0 失败）")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_selftest())
