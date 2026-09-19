"""qt_compat —— Qt 单后端兼容命名层（v0.16，去掉向下兼容）。

v0.16 起产品最低支持 Windows 10（PySide6 / Qt6 路线，Ollama 本地大脑同样要求
Win10+），不再兼容 Win7/Win8，也不再回落 PyQt5：
- 固定 PySide6 (Qt 6) 单后端，业务代码统一从这里取名字；
- 保留 ev_pos / ev_global_pos 两个适配函数（Qt6 鼠标事件坐标语义）；
- 跨线程 UI 派发统一走 Signal（ui_dispatch）。
"""
from __future__ import annotations

# ---- 唯一后端：PySide6 (Qt 6，Windows 10+) ----
from PySide6 import QtCore, QtGui, QtWidgets                  # noqa: F401
from PySide6.QtCore import (QEvent, QLockFile, QPoint, QPointF, QRect,  # noqa: F401
                            QRectF, QSize, Qt, QTimer, Signal,
                            # v0.29 页面转场动效需要（业务代码统一从这里取名字）
                            QEasingCurve, QMargins, QPropertyAnimation,
                            # v0.31.0：右栏效果区用 WebEngine 渲染本地内容，
                            # `QUrl.fromLocalFile()` 是它唯一能吃对的基准地址形式。
                            QUrl)
from PySide6.QtGui import (QAction, QBrush, QColor, QConicalGradient, QCursor,  # noqa: F401
                           QFont, QIcon, QLinearGradient, QPainter, QPainterPath,
                           QPen, QPixmap, QRadialGradient, QTextCursor)
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog,
                               QFileDialog, QFrame,
                               QGraphicsOpacityEffect, QHBoxLayout, QInputDialog,
                               QLabel, QLayout, QLineEdit,
                               QListWidget, QListWidgetItem, QMainWindow, QMenu,
                               QMessageBox, QPlainTextEdit, QPushButton,
                               QStackedWidget,
                               # v0.31.0：右栏标签改成"只做选择器"的 OutTabBar(QTabBar)
                               # —— 内容区必须共享（一个 WebEngine 视图 = 一个渲染进程）
                               QTabBar, QTabWidget,
                               QStyle, QStyledItemDelegate,
                               QSizePolicy, QSlider, QSplitter, QStyleOptionViewItem,
                               QSystemTrayIcon,
                               QTextBrowser, QTextEdit, QToolButton, QVBoxLayout,
                               QWidget, QGridLayout, QScrollArea,
                               QGraphicsDropShadowEffect)

QT_BACKEND = "PySide6"


def ev_pos(ev):
    return ev.position()


def ev_global_pos(ev):
    return ev.globalPosition().toPoint()


class _Dispatcher(QtCore.QObject):
    """跨线程 UI 派发器：worker 线程 emit → 主线程执行（队列连接）。"""
    _fire = Signal(object)

    def __init__(self):
        super().__init__()
        self._fire.connect(lambda fn: fn())


_DISPATCHER = _Dispatcher()


def _dispatcher_alive() -> bool:
    """派发器（C++ 侧）是否还活着。进程收尾时 QApplication 与对象会被销毁。"""
    try:
        import shiboken6
        return bool(shiboken6.Shiboken.isValid(_DISPATCHER))
    except Exception:                     # noqa: BLE001
        return False                      # 取不到就按「已销毁」处理


def ui_dispatch(fn) -> None:
    """从任意线程安全地把 fn() 派发到 UI 线程执行。

    v0.30.11：进程收尾期（窗口已销毁）后台线程仍可能 emit —— 此时
    C++ 侧派发器已被删除，直接 emit 会抛
    RuntimeError("Signal source has been deleted")，只在日志里留
    一条无意义 traceback。故：**只有派发器本身没了**才丢弃这次
    UI 更新（都要退出了，更新没有意义）；fn() 自身抛的
    RuntimeError 必须继续往上冒，不能被这里吞掉。
    """
    try:
        _DISPATCHER._fire.emit(fn)
    except RuntimeError:
        if not _dispatcher_alive():
            return
        raise

def screen_available_geometry(pos=None):
    """取某点所在屏幕的**可用**区域（QRect）；取不到返回 None。

    用途：浮层/弹窗的贴边定位 —— 把窗口拉回可见范围，
    否则在屏幕边缘点击时，面板会弹出到屏幕外面去（用户只看到一半或看不到）。

    pos 给 None 时用主屏。
    """
    try:
        from PySide6.QtGui import QGuiApplication
        scr = QGuiApplication.screenAt(pos) if pos is not None else None
        if scr is None:
            scr = QGuiApplication.primaryScreen()
        return scr.availableGeometry() if scr is not None else None
    except Exception:
        return None

