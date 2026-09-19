# -*- coding: utf-8 -*-
"""「文档与演示」格式选择浮层（栏目整合的 UI 载体）。

为什么单独抽一个模块
--------------------
栏目整合后，文档类 4 个工种（文案 / 表格 / PPT / Word）收进了一个入口。
子格式的选择最初做成"按钮下方再占一行子标签"，代价是**永久吃掉一行高度**，
而且那行在非文档类时又要隐藏、切换时还会闪。

改成"点了才弹出的浮层"：
  · 平时不占布局高度（专业工具都这么做）；
  · 一次能把 4 个选项摊开成卡片，比细条菜单更好扫视；
  · 点面板外部自动关闭（`Qt.Popup` 原生行为，不必自己装全局事件过滤器）。

设计约束
--------
· **不依赖主窗口**：只对外发 `picked(key)`，怎么切栏目由调用方决定
  （这样它可以被独立自检，也方便以后复用到别的入口）。
· **清单由调用方传入**（`labels`），避免主文件与这里各写一份而漂移。
· Qt 类统一走 `qt_compat`（项目约定：业务代码只用它导出的名字）。
"""
import qt_compat as qt
from qt_compat import QFrame, QLabel, QPushButton, QVBoxLayout, QHBoxLayout, Qt, Signal

# 面板整体配色（与主界面同族）
_C_BG = "#ffffff"
_C_BORDER = "#cbd5e1"
_C_TITLE = "#0f172a"
_C_HINT = "#94a3b8"
_C_CARD_BG = "#f8fafc"
_C_CARD_HOVER = "#eff6ff"
_C_CARD_SEL = "#e0f2fe"
_C_CARD_SEL_BORDER = "#38bdf8"
_C_CARD_BORDER = "#e2e8f0"


class DocSuitePanel(QFrame):
    """文档格式选择浮层：图标 + 名称 + 一句说明的卡片横排。"""

    picked = Signal(str)

    def __init__(self, labels, parent=None):
        """labels: [(key, label, hint)] 或 [(key, label)] —— 后者自动补空说明。"""
        super().__init__(parent)
        self._items = []
        for it in labels:
            key, label = it[0], it[1]
            hint = it[2] if len(it) > 2 else ""
            self._items.append((key, label, hint))

        # Qt.Popup：点击面板外部自动关闭；不加这条就得自己写全局鼠标钩子
        self.setWindowFlags(Qt.Popup | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, False)
        self.setStyleSheet(
            "DocSuitePanel{background:%s;border:1px solid %s;border-radius:12px;}"
            % (_C_BG, _C_BORDER))

        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 12)
        lay.setSpacing(8)

        title = QLabel("选择文档格式")
        title.setStyleSheet("color:%s;font-size:12px;font-weight:bold;" % _C_TITLE)
        lay.addWidget(title)

        row = QHBoxLayout()
        row.setSpacing(8)
        self._btns = {}
        for key, label, hint in self._items:
            card = QPushButton()
            card.setCursor(Qt.PointingHandCursor)
            card.setFixedSize(96, 78)
            card.setToolTip(hint or label)
            card.setCheckable(True)
            # 卡片内容：图标大字 + 名称 + 说明小字（用 \n 分三行）
            icon, name = (label.split(" ", 1) + [""])[:2] if " " in label else ("", label)
            card.setText("%s\n%s\n%s" % (icon or "•", name, hint or " "))
            card.setStyleSheet(
                "QPushButton{border:1px solid %s;border-radius:10px;background:%s;"
                "color:%s;font-size:11px;padding:4px;text-align:center;}"
                "QPushButton:hover{background:%s;border-color:#93c5fd;}"
                "QPushButton:checked{background:%s;border:1px solid %s;color:#0369a1;"
                "font-weight:bold;}"
                % (_C_CARD_BORDER, _C_CARD_BG, _C_TITLE, _C_CARD_HOVER,
                   _C_CARD_SEL, _C_CARD_SEL_BORDER))
            card.clicked.connect(lambda _=False, k=key: self._pick(k))
            row.addWidget(card)
            self._btns[key] = card
        lay.addLayout(row)

        tip = QLabel("选一种即可直接提要求；也可以直接说「给我个 Word」")
        tip.setStyleSheet("color:%s;font-size:10px;" % _C_HINT)
        lay.addWidget(tip)

    # ---------- 对外 ----------
    def keys(self):
        return [k for k, _, _ in self._items]

    def set_current(self, key):
        """高亮当前格式（面板打开时应当把当前项点亮）。"""
        for k, b in self._btns.items():
            b.setChecked(k == key)

    def popup_at(self, global_pos, current=None, anchor_width=0):
        """在指定全局坐标的**下方**弹出；自动避免超出屏幕右/下边缘。

        anchor_width > 0 时，把面板水平居中到锚点（触发按钮）上 ——
        否则面板从按钮左边缘展开，看起来像是"贴偏了"。
        """
        if current:
            self.set_current(current)
        self.adjustSize()
        x, y = int(global_pos.x()), int(global_pos.y())
        if anchor_width:
            x -= max(0, (self.width() - int(anchor_width)) // 2)
        scr = qt.screen_available_geometry(global_pos)
        if scr is not None:
            gw, gh = self.width(), self.height()
            x = min(x, scr.right() - gw - 4)
            y = min(y, scr.bottom() - gh - 4)
            x = max(scr.left() + 4, x)
            y = max(scr.top() + 4, y)
        self.move(x, y)
        self.show()

    def _pick(self, key):
        self.hide()
        self.picked.emit(key)


def _selftest():
    """离屏自检：构造 → 卡片数 → 点击发信号 → 高亮 → 定位不出屏。"""
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import QPoint

    app = QApplication.instance() or QApplication([])
    checks = []

    def check(name, ok, extra=""):
        checks.append((name, bool(ok)))
        print("  %s %s%s" % ("[OK]  " if ok else "[FAIL]", name,
                             ("  -- " + str(extra)) if extra else ""))

    LABELS = [("copy", "✍️ 文案", "出成稿"),
              ("xls", "📊 表格", "Excel"),
              ("ppt", "📽 PPT", "演示稿"),
              ("doc", "📄 Word", "文档")]

    p = DocSuitePanel(LABELS)
    check("构造成功（4 张卡片）", len(p.keys()) == 4, p.keys())
    check("弹层标志含 Popup（点外部自动关）",
          bool(p.windowFlags() & Qt.Popup))

    p.set_current("ppt")
    check("set_current 只点亮目标项",
          [k for k, b in p._btns.items() if b.isChecked()] == ["ppt"],
          [k for k, b in p._btns.items() if b.isChecked()])

    got = []
    p.picked.connect(lambda k: got.append(k))
    p._btns["xls"].click()
    check("点卡片发出 picked 信号", got == ["xls"], got)
    check("选完自动隐藏", p.isHidden())

    # 定位：给一个屏幕右下的坐标，应被拉回可见范围
    p.popup_at(QPoint(99999, 99999), current="doc")
    scr = qt.screen_available_geometry(QPoint(99999, 99999))
    inside = True
    if scr is not None:
        inside = (p.x() + p.width() <= scr.right() + 1
                  and p.y() + p.height() <= scr.bottom() + 1)
    check("贴边弹出时被拉回屏幕内", inside,
          "pos=(%d,%d) size=(%d,%d)" % (p.x(), p.y(), p.width(), p.height()))
    p.hide()

    # 两元组形式（不带说明）也要能用
    p2 = DocSuitePanel([("a", "AA"), ("b", "BB")])
    check("兼容两元组 labels（无说明）", p2.keys() == ["a", "b"], p2.keys())

    bad = [n for n, ok in checks if not ok]
    print()
    print("%d 项，%s" % (len(checks), "全部通过" if not bad else "失败 %d 项" % len(bad)))
    return 1 if bad else 0


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        sys.exit(_selftest())
    print(__doc__)
