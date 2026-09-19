"""PASM Studio 产品入口（v0.16）。

真正的产品 = 桌面小人(PetShell) + 对话窗(CompanionWindow)。
注意：desktop/pasm_desktop.py 是早期"出生/画布"游戏脚手架，禁止再作为打包入口。

打包与运行均以本文件为入口；任何未捕获异常都会写入 %APPDATA%\\PASMStudio\\crash.log
并弹窗提示（windowed 应用无控制台，靠这个兜底不至于"点了没反应"）。
v0.16：不再兼容 Win7/8（最低 Windows 10）。启动失败时优先给出可读的中文说明；
Qt 都无法初始化时退回系统 MessageBox，杜绝"双击后只闪一个英文错/无任何反应"。
"""
import logging
import os
import sys
import traceback

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)
for _p in (_HERE, _REPO):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_CRASH_DIR = None


def _crash_dir() -> str:
    global _CRASH_DIR
    if _CRASH_DIR is None:
        if getattr(sys, "frozen", False):
            base = os.environ.get("APPDATA") or os.path.expanduser("~")
            _CRASH_DIR = os.path.join(base, "PASMStudio")
        else:
            _CRASH_DIR = os.path.join(_REPO, ".pasmstudio_dev")
    return _CRASH_DIR


def _log_crash(exc: BaseException):
    try:
        d = _crash_dir()
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "crash.log"), "a", encoding="utf-8") as f:
            f.write("=" * 60 + "\n")
            f.write(sys.version + "\n")
            traceback.print_exc(file=f)
    except Exception:
        pass


def _msgbox(title: str, text: str):
    """先尝试 Qt 弹窗；Qt 起不来（如平台插件/系统过旧）退回系统 MessageBoxW。"""
    try:
        from qt_compat import QApplication, QMessageBox
        app = QApplication.instance() or QApplication(sys.argv)
        QMessageBox.critical(None, title, text)
    except Exception:
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, text, title, 0x10)  # MB_ICONERROR
        except Exception:
            pass


def _os_too_old() -> bool:
    """PASM Studio v0.16+ 最低 Windows 10（PySide6/Qt6 路线）。"""
    try:
        if os.name != "nt":
            return False
        v = sys.getwindowsversion()
        return (v.major, v.minor) < (10, 0)
    except Exception:
        return False


_LIGHT_QSS = """
/* v0.23.1 全局亮色兜底：应用级 QSS 优先级高于 palette，系统深色模式
   再怎么变也不会把聊天/列表染黑。各控件自身的局部样式优先级更高，
   需要定制的颜色（按钮选中态等）不受影响。 */
QWidget { color: #0f172a; }
QDialog, QWizard { background: #f7f8fb; }
QToolTip { background: #ffffff; color: #0f172a; border: 1px solid #cbd5e1; }
QMenu { background: #ffffff; color: #0f172a; border: 1px solid #e2e8f0; }
QMenu::item:selected { background: #e0f2fe; color: #0c4a6e; }
QMenuBar { background: #f7f8fb; color: #0f172a; }
QLineEdit, QTextEdit, QPlainTextEdit, QTextBrowser {
    background: #ffffff; color: #0f172a;
    selection-background-color: #bae6fd; selection-color: #0c4a6e; }
QComboBox { background: #ffffff; color: #0f172a; }
QComboBox QAbstractItemView {
    background: #ffffff; color: #0f172a;
    selection-background-color: #bae6fd; selection-color: #0c4a6e; }
QListWidget, QTreeWidget, QTableWidget, QListView, QTreeView, QTableView {
    background: #ffffff; color: #0f172a; }
QListWidget::item:selected, QListView::item:selected {
    background: #bae6fd; color: #0c4a6e; }
QMessageBox { background: #f7f8fb; }
QScrollBar:vertical, QScrollBar:horizontal { background: #f1f5f9; }
QScrollBar::handle:vertical, QScrollBar::handle:horizontal { background: #cbd5e1; }
"""


def _apply_light_theme(app) -> None:
    """v0.23.0/0.23.1 主题修复：统一固定为"亮色 Fusion + 显式浅色 Palette +
    应用级全局 QSS 兜底"。

    修复：用户系统是 Windows 深色模式时，Qt 自动套用深色 Palette——
    界面（聊天框/会话列表）跟着变黑，但聊天文字仍按浅色设计写死深色，
    结果"字看不见"。v0.23.0 只改了 palette；但不少控件样式里只写死了
    浅色背景、文字色依赖 palette 继承链，个别场景仍会颠倒。v0.23.1 在
    palette 之上叠加应用级 QSS，把文字/输入框/列表/下拉等底色字色全部
    钉死为亮色（应用级 QSS 优先级高于 palette，控件局部样式优先级又
    高于应用级 QSS——分层兜底，互不破坏）。
    """
    try:
        from PySide6.QtGui import QColor, QPalette  # type: ignore
        app.setStyle("Fusion")
        p = QPalette()
        light = {
            QPalette.Window: QColor("#f7f8fb"),
            QPalette.WindowText: QColor("#0f172a"),
            QPalette.Base: QColor("#ffffff"),
            QPalette.AlternateBase: QColor("#f1f5f9"),
            QPalette.Text: QColor("#0f172a"),
            QPalette.Button: QColor("#ffffff"),
            QPalette.ButtonText: QColor("#0f172a"),
            QPalette.Highlight: QColor("#5b8def"),
            QPalette.HighlightedText: QColor("#ffffff"),
            QPalette.ToolTipBase: QColor("#ffffff"),
            QPalette.ToolTipText: QColor("#0f172a"),
            QPalette.Link: QColor("#0ea5e9"),
            QPalette.PlaceholderText: QColor("#94a3b8"),
        }
        for role, col in light.items():
            p.setColor(QPalette.Active, role, col)
            p.setColor(QPalette.Inactive, role, col)
            p.setColor(QPalette.Disabled, role, col)
        app.setPalette(p)
        app.setStyleSheet(_LIGHT_QSS)     # v0.23.1：QSS 硬兜底，双保险
    except Exception:
        pass  # 主题兜底失败不阻断启动（仍有各控件自带浅色样式）


def _main() -> int:
    # ★ 日志必须先于**任何** logging 调用装好（v0.30.2 修）。
    # logging.basicConfig 在 root 已有 handler 时会**静默失效**，而
    # logging.info() 会自动装一个 stderr handler —— 顺序错了日志就永远是空的，
    # 且不报错。v0.30.0 的迁移日志正是踩了这个（pasm.log 一直为空）。
    try:
        import logsetup
        logsetup.setup()
    except Exception as _lse:
        # 不许静默：日志装不上这件事必须留痕，否则"日志永远为空"只能靠猜
        try:
            import os as _os2
            _os2.makedirs(_os2.environ.get("TEMP") or ".", exist_ok=True)
            with open(_os2.path.join(_os2.environ.get("TEMP") or ".",
                                     "pasm_logsetup_error.txt"),
                      "a", encoding="utf-8") as _f:
                _f.write("pasm_main: import/setup logsetup 失败：%r\n" % (_lse,))
        except Exception:
            pass

    # v0.29 一次性数据迁移（角色默认名等）。
    # 放在最前：纯文件操作、不依赖 Qt，且失败不阻断启动 ——
    # 启动路径上任何非致命问题都不该让用户打不开应用。
    try:
        import migrate
        for _mk, _mv in migrate.run_once():
            logging.info("migrate %s -> %s", _mk, _mv)
    except Exception as _mex:
        logging.warning("migrate 跳过: %s", _mex)

    from qt_compat import QApplication, QFont
    from qt_compat import QMessageBox, QSystemTrayIcon  # noqa: F401 (触发托盘支持)

    # ── v0.31.0：冻结版「右栏真浏览器」自检开关 ────────────────────────────
    # 为什么要它：右栏的效果区靠 QtWebEngine（内嵌 Chromium），而它在打包后
    # **最容易静默降级** —— 少了 QtWebEngineProcess.exe / resources/*.pak /
    # qtwebengine_locales，代码里探测不到就退回原生渲染，界面看起来一切正常，
    # 只有"站点/视频没渲染"这一个症状（而且很难联想到是打包问题）。
    # 所以给一个能被外部脚本调用的开关：**真建视图 → 真加载页面 → 读回 DOM 事实**
    # → 打印结论并以退出码表达，让"冻结版能不能用真浏览器"这件事可被自动验证。
    if "--we-check" in sys.argv:
        # ⚠️ 冻结版是 console=False 的：`sys.stdout` 是 None，裸 `print` 会
        #    AttributeError。所以结果**落盘**为准，打印只是顺带（且必须在 try 里）。
        _LINES = []
        _OUTF = os.path.join(os.environ.get("TEMP") or ".", "pasm_we_check.txt")

        def _say(msg):
            _LINES.append(str(msg))
            try:
                print(msg)
            except Exception:
                pass
            try:
                with open(_OUTF, "w", encoding="utf-8") as _f:
                    _f.write("\n".join(_LINES) + "\n")
            except Exception:
                pass
        try:
            import effect_view as _EV
            _say("WE_AVAILABLE=%s" % _EV.available())
            if not _EV.available():
                _say("WE_ERROR=%s" % _EV.error())
                _say("WE_CHECK=FAIL")
                return 3
            from PySide6.QtWidgets import QApplication as _QA
            from PySide6.QtWebEngineWidgets import QWebEngineView as _WEV
            _app = _QA.instance() or _QA([])
            import tempfile as _tf
            import os as _os
            _d = _tf.mkdtemp(prefix="pasm_we_check_")
            _png = _os.path.join(_d, "t.png")
            try:
                from PySide6.QtGui import QPixmap as _QP, QColor as _QC
                _p = _QP(40, 30)
                _p.fill(_QC("#0ea5e9"))
                _p.save(_png)
            except Exception:
                pass
            _v = _WEV()
            _html = ("<html><body><h1 id='h'>FROZEN_OK</h1>"
                     "<img id='i' src='file:///%s'></body></html>"
                     % _png.replace("\\", "/"))
            _got = {"v": None}

            def _cb(val):
                _got["v"] = val
            _v.loadFinished.connect(lambda ok: _v.page().runJavaScript(
                "(document.getElementById('h').innerText) + '|' + "
                "(document.getElementById('i').naturalWidth)", 0, _cb))
            from PySide6.QtCore import QUrl as _QU
            _v.setHtml(_html, _QU.fromLocalFile(_d + _os.sep))
            import time as _t
            _t0 = _t.time()
            while _got["v"] is None and _t.time() - _t0 < 25:
                _app.processEvents()
                _t.sleep(0.05)
            _say("WE_DOM=%s" % _got["v"])
            _v.stop()
            _v.setParent(None)
            _v.deleteLater()
            _app.processEvents()
            _ok = isinstance(_got["v"], str) and _got["v"].startswith("FROZEN_OK|")
            _ok = _ok and not _got["v"].endswith("|0")
            _say("WE_CHECK=%s" % ("PASS" if _ok else "FAIL"))
            return 0 if _ok else 4
        except Exception as _wex:
            _say("WE_EXC=%r" % (_wex,))
            _say("WE_CHECK=FAIL")
            return 5

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setFont(QFont("Microsoft YaHei", 10))
    _apply_light_theme(app)              # v0.23.0：无论系统深浅色都固定亮色 UI

    from pasm_pet import PetShell, _ensure_single_instance
    if not _ensure_single_instance():
        # 已有实例在跑：提示后退出（保留桌面小人那一份）
        return 0

    pet = PetShell()
    pet.show()
    # 启动即打开对话窗（产品主界面），桌面小人同时驻留
    pet.open_chat()
    return app.exec()


if __name__ == "__main__":
    # 注意：sys.exit() 必须放在 try 外面——否则正常退出时它抛出的 SystemExit(0)
    # 会被 BaseException 兜住，误弹"很抱歉…0"的错误框（退出报错的根因）。
    try:
        if _os_too_old():
            _msgbox(
                "无法启动 PASM Studio",
                "你的 Windows 版本过旧。\n\nPASM Studio v0.16 起需要 Windows 10 "
                "或更高版本（不再支持 Windows 7/8）。\n\n"
                "请升级系统后再安装使用。")
            sys.exit(1)
        code = _main()
    except BaseException as exc:  # noqa: BLE001  windowed 应用无控制台，必须落盘
        _log_crash(exc)
        try:
            _msgbox(
                "PASM Studio 启动失败",
                "很抱歉，程序启动遇到问题：\n\n" + str(exc) +
                "\n\n这通常是系统环境（如缺少运行库、显卡驱动过旧）导致的。\n"
                "提示：PASM Studio v0.16 起需要 Windows 10 或更高版本。\n\n"
                "详细信息已写入 " + os.path.join(_crash_dir(), "crash.log") +
                "，可发给我们修复。")
        except Exception:
            pass
        sys.exit(1)
    sys.exit(code)
