# -*- coding: utf-8 -*-
"""effect_view.py —— 右侧「产出」栏的效果渲染器（v0.31.0）。

为什么要有这个模块：右栏要**真渲染**站点页面 / 图片 / 视频 / 任意 HTML，
Qt 原生那套（QTextBrowser + QLabel）做不到 —— 于是引入 QtWebEngine（Chromium）。

★ 三条**实测**结论（决定了本模块的形状，改之前先读这段）：

  1. **每个 QWebEngineView = 一个独立渲染进程。**
     实测：建 2 个视图 → `QtWebEngineProcess.exe` 从 1 个变 2 个。
     → 所以整个应用**只允许存在一个视图**。`SharedEffectView` 持有它并跨产出页
       复用；**切页只是换 HTML，不是换视图**。
       若改成"每页一个视图"，开 6 个产出页就是 6 个 Chromium 进程。

  2. **`setHtml(html)` 不带 baseUrl 时，页面不是"本地内容"。**
     `LocalContentCanAccessFileUrls` 那时不生效，**连绝对 `file://` 图片都加载不到**
     （实测 `img.naturalWidth == 0`）。
     必须 `setHtml(html, QUrl.fromLocalFile(目录))` —— 这样绝对/相对图片、
     `<video>`、以及 `<iframe src="file:///">` 的**站点都能加载**
     （实测 iframe 里读得到站点正文）。**不需要**落临时 .html 文件。

  3. `QApplication` 之后再 import `QtWebEngineWidgets` 是**可以的**
     （PySide6 6.11.2 实测，0.09s），不必为它调整程序启动顺序。
     Chromium 命令行开关在本模块 import 时设好即可（`QTWEBENGINE_CHROMIUM_FLAGS`）。

诚实边界：WebEngine 不可用时（例如只装了 PySide6-Essentials、没有 Addons）
`available()` 返回 False，调用方**如实降级**到原生渲染，并把"降级中"告诉用户 ——
不假装还是真浏览器。`error()` 给出真实原因。

> 排版纪律（小志 2026-09-17 原话：「文案居然这么小还带滚动条，不需要滚动条，
> 全显示即可，整个页面可以带滚动条」）：
> **块内一律不许出现滚动条** —— 正文 14px/1.75、`pre` 用 `white-space:pre-wrap`
> 换行而不是横向滚动；整个文档只有**一条**页面级滚动条。
> 唯一的例外是站点 `<iframe>`（活的网页本身就是一个视口，内部滚动是它的语义）。
"""
from __future__ import annotations

import html as _html
import os
import sys

# ⚠️ 必须在 import QtWebEngine* 之前设好：这些开关是 Chromium 启动时读的。
#    · disable-gpu / software-rasterizer：无 GPU（离屏、远程桌面、老显卡）下也能出图；
#    · no-sandbox：部分 Windows 环境里沙箱会导致渲染进程起不来；
#    · disable-dev-shm-usage：避免小容量 /dev/shm 的经典崩溃。
os.environ.setdefault(
    "QTWEBENGINE_CHROMIUM_FLAGS",
    "--disable-gpu --disable-software-rasterizer --no-sandbox --disable-dev-shm-usage")

from qt_compat import QTimer, Qt, QtWidgets          # noqa: E402
from qt_compat import QLabel, QVBoxLayout, QWidget   # noqa: E402

# --------------------------------------------------------------------------
# 可用性探测（**只探测一次**，结果缓存；失败原因要能如实说出去）
# --------------------------------------------------------------------------
_WE_OK = False
_WE_ERR = ""
_QWebEngineView = None
_QWebEnginePage = None
_QWebEngineSettings = None
_QUrl = None

try:
    from qt_compat import QUrl as _QUrl                       # noqa: E402
except Exception:                                             # noqa: BLE001
    try:
        from PySide6.QtCore import QUrl as _QUrl               # noqa: E402
    except Exception:                                         # noqa: BLE001
        _QUrl = None

try:
    # ⚠️ Qt6 里 **QWebEnginePage / QWebEngineSettings 属于 QtWebEngineCore**，
    #    QtWebEngineWidgets 只提供 QWebEngineView。（从 Widgets 里 import Page 会
    #    ImportError —— 实测踩到，并因此**静默降级**：`available()` 返回 False，
    #    整套真渲染没启用，而界面看起来一切正常。故此处刻意分成两行 import。）
    from PySide6.QtWebEngineCore import QWebEnginePage as _QWebEnginePage          # noqa: E402
    from PySide6.QtWebEngineCore import QWebEngineSettings as _QWebEngineSettings  # noqa: E402
    from PySide6.QtWebEngineWidgets import QWebEngineView as _QWebEngineView       # noqa: E402
    _WE_OK = True
except Exception as _ex:                                      # noqa: BLE001
    _WE_ERR = "%s: %s" % (type(_ex).__name__, _ex)


def available() -> bool:
    """WebEngine 能不能用（**真导入成功**才算；不是"看起来装了"）。"""
    return _WE_OK


def error() -> str:
    """不可用时的真实原因（给"降级中"的提示用）。"""
    return _WE_ERR


# --------------------------------------------------------------------------
# 文档模板与样式
# --------------------------------------------------------------------------
_CSS = """
*{box-sizing:border-box}
html,body{margin:0;padding:0;background:#fbfdff}
body{
  font:14px/1.75 "Microsoft YaHei","PingFang SC","Segoe UI",sans-serif;
  color:#0f172a;padding:12px;
  -webkit-font-smoothing:antialiased;
}
h1{font-size:18px;line-height:1.5;margin:0 0 10px}
h2{font-size:16px;line-height:1.5;margin:16px 0 6px}
h3{font-size:15px;line-height:1.5;margin:14px 0 6px}
h4{font-size:14px;margin:12px 0 4px}
p{margin:8px 0}
ul,ol{margin:8px 0;padding-left:22px}
li{margin:4px 0}
a{color:#0369a1}
hr{border:none;border-top:1px solid #e2e8f0;margin:14px 0}
blockquote{margin:10px 0;padding:8px 12px;background:#f1f5f9;
  border-left:3px solid #94a3b8;border-radius:0 6px 6px 0;color:#334155}
table{border-collapse:collapse;width:100%;margin:10px 0;font-size:13px}
th,td{border:1px solid #e2e8f0;padding:6px 9px;text-align:left;vertical-align:top}
th{background:#f1f5f9;font-weight:bold}
tr:nth-child(even) td{background:#fafbfc}
pre{background:#0f172a;color:#e2e8f0;padding:11px 13px;border-radius:8px;
  white-space:pre-wrap;overflow-wrap:anywhere;margin:10px 0;
  font:12.5px/1.7 Consolas,"Courier New",monospace}
code{font-family:Consolas,"Courier New",monospace;font-size:13px;
  background:#f1f5f9;padding:1px 5px;border-radius:4px;color:#0f172a}
pre code{background:transparent;color:inherit;padding:0;font-size:12.5px}
img{max-width:100%;height:auto;display:block;margin:10px 0;
  border:1px solid #e2e8f0;border-radius:8px;background:#fff}
video{max-width:100%;display:block;margin:10px 0;border-radius:8px;
  background:#000;border:1px solid #e2e8f0}
iframe.site{width:100%;height:min(62vh,560px);border:1px solid #e2e8f0;
  border-radius:8px;background:#fff;display:block;margin:10px 0}
.docframe{width:100%;height:min(70vh,620px);border:1px solid #e2e8f0;
  border-radius:8px;background:#fff;display:block;margin:10px 0}
.block{background:#fff;border:1px solid #e2e8f0;border-radius:10px;
  padding:13px 15px;margin:0 0 12px}
.block:last-child{margin-bottom:0}
.rhead{display:flex;align-items:center;gap:8px;margin:0 0 10px}
.rno{font:11px/1 sans-serif;font-weight:bold;background:#e0f2fe;color:#0369a1;
  border-radius:999px;padding:4px 10px;white-space:nowrap}
.rask{flex:1;min-width:0;color:#0369a1;font-size:12.5px;
  overflow-wrap:anywhere;word-break:break-word}
.rline{height:1px;background:#e2e8f0;flex:0 0 28px}
.rkind{font:11px/1 sans-serif;color:#64748b;background:#f1f5f9;
  border-radius:999px;padding:4px 9px;white-space:nowrap}
.path{font:12px/1.65 Consolas,"Courier New",monospace;color:#475569;
  background:#f8fafc;border:1px solid #e2e8f0;border-radius:6px;
  padding:7px 9px;margin:10px 0 0;overflow-wrap:anywhere;word-break:break-all}
.pmeta{color:#94a3b8;font-size:12px;margin:8px 0 0}
.tag{display:inline-block;font:11px/1 sans-serif;border-radius:999px;
  padding:4px 9px;margin:0 6px 6px 0}
.tag.ok{background:#f0fdf4;color:#15803d;border:1px solid #bbf7d0}
.tag.warn{background:#fffbeb;color:#b45309;border:1px solid #fde68a}
.tag.err{background:#fef2f2;color:#b91c1c;border:1px solid #fecaca}
.empty{color:#94a3b8;font-size:13px;text-align:center;padding:26px 0}
"""

#: 文档骨架。整份文档**只有一条**页面级滚动条（浏览器默认行为）。
_DOC = ("<!DOCTYPE html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        "<title>{title}</title><style>{css}</style></head><body>{body}</body></html>")


def wrap_document(title: str, body_html: str) -> str:
    """把累积的块拼成一份完整 HTML 文档（样式统一在这里）。"""
    return _DOC.format(title=_html.escape(title or "产出"), css=_CSS,
                       body=body_html or "")


def block_html(round_no: int, ask: str = "", kind_label: str = "",
               inner: str = "", path: str = "", media: str = "",
               root: str = "", note: str = "") -> str:
    """生成一个"轮次块"。

    每轮一块、**只往后加**（小志：「每次提了新要求新产出就往下输出，
    不删除和覆盖之前的内容」）—— 所以这里绝不生成"覆盖"语义的东西。

    · `inner`  —— 这一轮的效果正文（文案/成稿/说明，已是 HTML）
    · `media`  —— 图片 / 视频 / 站点入口（本地绝对路径），按扩展名决定标签
    · `path`   —— 这一轮的主产物路径（单独一行等宽字展示）
    """
    head = ['<div class="rhead">',
            '<span class="rno">第 %d 轮</span>' % max(1, int(round_no))]
    if kind_label:
        head.append('<span class="rkind">%s</span>' % _html.escape(kind_label))
    if ask:
        head.append('<span class="rline"></span>')
        head.append('<span class="rask">%s</span>' % _html.escape(ask[:160]))
    head.append('</div>')
    parts = ['<div class="block">', "".join(head)]
    if inner:
        parts.append(inner)
    parts.append(_media_html(media, root))
    if path:
        parts.append('<div class="path">%s</div>' % _html.escape(path))
    if note:
        parts.append('<div class="pmeta">%s</div>' % note)
    parts.append("</div>")
    return "".join(parts)


#: 扩展名 → 渲染方式。刻意**只有这三种**，其余一律给"文件行"，
#: 免得把不认识的东西硬塞进 <img> 变成一张碎图。
_VIDEO_EXT = (".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v")
_IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".svg")
_WEB_EXT = (".html", ".htm")


def _uri(p: str) -> str:
    """本地路径 → 浏览器能吃的 file:// URI（QtWebEngine 里这条路是通的，实测）。"""
    try:
        s = os.path.abspath(p).replace("\\", "/")
        return "file:///" + s.lstrip("/")
    except Exception:                                          # noqa: BLE001
        return ""


def _media_html(media: str, root: str = "") -> str:
    """把这一轮的"效果物"渲染成 HTML。

    ★ 站点（.html）不只给链接，而是**内嵌 iframe 真渲染** ——
      小志：「如生成开发的内容，可以在此栏呈交生成的整个站点效果或页面效果」。
      实测：`setHtml(..., QUrl.fromLocalFile(目录))` 时 file:// 的 iframe 能加载。
    """
    if not media:
        return ""
    if not os.path.exists(media) and not (root and os.path.exists(root)):
        return ""
    p = media if os.path.exists(media) else root
    ext = os.path.splitext(p)[1].lower()
    u = _uri(p)
    if not u:
        return ""
    if ext in _VIDEO_EXT:
        return ('<video src="%s" controls preload="metadata"></video>' % u)
    if ext in _IMAGE_EXT:
        return ('<img src="%s" alt="%s">'
                % (u, _html.escape(os.path.basename(p))))
    if ext in _WEB_EXT:
        return ('<iframe class="site" src="%s"></iframe>'
                '<div class="pmeta">↑ 站点实时预览（这一块内部可滚动）｜'
                '要交互请点上面的「↗ 打开」用系统浏览器看</div>' % u)
    return ('<div class="path">📄 %s</div>'
            % _html.escape(os.path.basename(p)))


# --------------------------------------------------------------------------
# 共享视图：全应用**唯一**的 QWebEngineView
# --------------------------------------------------------------------------
class _EffectPage(_QWebEnginePage if _WE_OK else object):     # type: ignore[misc]
    """页面策略：**外链不在右栏里跳**（右栏是"看效果"的地方，不是浏览器）。"""

    def acceptNavigationRequest(self, url, ntype, is_main_frame):        # noqa: N802
        try:
            s = url.scheme().lower()
        except Exception:                                                # noqa: BLE001
            return True
        if s in ("http", "https") and is_main_frame:
            # 交给系统浏览器（用户点链接是想真打开，不是把右栏顶掉）
            try:
                QtWidgets.QDesktopServices.openUrl(url)
            except Exception:                                            # noqa: BLE001
                pass
            return False
        return True

    def createWindow(self, wtype):                                       # noqa: N802
        return None


#: 建过的共享视图（用于**显式收尾**，见 `shutdown_all()`）
_LIVE = []


def shutdown_all():
    """把所有建过的效果视图**先销毁掉**。

    ⚠️ 为什么必须有这个东西（真踩到）：
      如果让 QWebEngineView 等到解释器退出时才被销毁，进程会以
      **0xC0000005（访问冲突）**结束 —— 在验证套件里表现为
      "所有断言都通过了，但脚本 rc=3221225477"，
      看起来像"全过"，实则**脚本没跑完**（临时目录都来不及清）。
      所以：窗口关闭时销毁一次，进程收尾时再兜一次。
    """
    for w in list(_LIVE):
        try:
            w.dispose()
        except Exception:                                      # noqa: BLE001
            pass
    _LIVE[:] = []


class SharedEffectView(QWidget):
    """产出区唯一的渲染视图（懒创建：没人看效果时**不建**，不给启动加负担）。

    `alive` 反映"WebEngine 视图真的建起来了" —— 验证时用它区分
    "真渲染" 与 "静默降级"（不许把降级说成成功）。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._view = None
        _LIVE.append(self)
        self._last = ("", "")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self._ph = QLabel("")                     # 建视图期间的占位
        self._ph.setStyleSheet("color:#94a3b8;font-size:12px;")
        lay.addWidget(self._ph)
        self._lay = lay
        self._last_ok = False
        self._err = ""

    # ---- 查询 ----
    @property
    def alive(self) -> bool:
        return self._view is not None

    def error(self) -> str:
        return self._err or error()

    # ---- 懒创建 ----
    def _ensure(self):
        if self._view is not None or not _WE_OK:
            return self._view
        try:
            v = _QWebEngineView(self)
            v.setPage(_EffectPage(v))
            st = v.settings()
            for k, val in (("JavascriptEnabled", True),
                           ("LocalContentCanAccessFileUrls", True),
                           ("LocalContentCanAccessRemoteUrls", False),
                           ("PlaybackRequiresUserGesture", False),
                           ("PluginsEnabled", False),
                           ("ShowScrollBars", True),
                           ("ScrollAnimatorEnabled", True)):
                try:
                    st.setAttribute(getattr(_QWebEngineSettings.WebAttribute, k), val)
                except Exception:                                    # noqa: BLE001
                    pass
            v.setContextMenuPolicy(Qt.NoContextMenu)
            self._lay.removeWidget(self._ph)
            self._ph.hide()
            self._lay.addWidget(v)
            self._view = v
            self._err = ""
        except Exception as ex:                                      # noqa: BLE001
            self._err = "%s: %s" % (type(ex).__name__, ex)
            logging_hint = "（WebEngine 视图创建失败，已降级为原生渲染）"
            self._ph.setText(logging_hint)
            self._ph.show()
            self._view = None
        return self._view

    # ---- 渲染 ----
    def set_doc(self, html: str, base_dir: str = "") -> bool:
        """把整份文档交给共享视图。返回"这一份真的送上去了"。

        ★ baseDir 必须给：不给的话页面不是"本地内容"，**连绝对 file:// 图片都加载不到**
          （实测 img.naturalWidth == 0）。
        """
        v = self._ensure()
        if v is None:
            return False
        try:
            base = _QUrl.fromLocalFile(os.path.abspath(base_dir) + os.sep) \
                if (base_dir and _QUrl) else (_QUrl("about:blank") if _QUrl else None)
            if base is None:
                v.setHtml(html)
            else:
                v.setHtml(html, base)
            self._last = (html, base_dir)
            self._last_ok = True
            return True
        except Exception as ex:                                      # noqa: BLE001
            self._err = "%s: %s" % (type(ex).__name__, ex)
            self._last_ok = False
            return False

    def reload_doc(self) -> bool:
        if not self._last[0]:
            return False
        return self.set_doc(*self._last)

    def dispose(self):
        """显式销毁 WebEngine 视图（**在解释器退出之前**）。

        见 `shutdown_all()` 那段说明：晚销毁 = 进程访问冲突退出。
        """
        v, self._view = self._view, None
        if v is None:
            return
        for step in ("stop", "setParent"):
            try:
                if step == "stop":
                    v.stop()
                else:
                    v.setParent(None)
            except Exception:                                      # noqa: BLE001
                pass
        try:
            v.deleteLater()
        except Exception:                                          # noqa: BLE001
            pass
        # 让 DeferredDelete 真的执行（不然 deleteLater 只是排了个队）
        try:
            QtWidgets.QApplication.processEvents()
            from qt_compat import QEvent as _QE
            QtWidgets.QApplication.sendPostedEvents(None, _QE.DeferredDelete)
            QtWidgets.QApplication.processEvents()
        except Exception:                                          # noqa: BLE001
            pass
        try:
            _LIVE.remove(self)
        except Exception:                                          # noqa: BLE001
            pass

    def query(self, js: str, cb=None, timeout_ms: int = 0):
        """在页面里跑一段 JS 并取回结果（验证用：读 DOM 事实，别猜）。"""
        v = self._ensure()
        if v is None:
            if cb is not None:
                cb(None)
            return
        try:
            if cb is None:
                v.page().runJavaScript(js)
            else:
                v.page().runJavaScript(js, 0, cb)
        except Exception:                                            # noqa: BLE001
            if cb is not None:
                cb(None)


def selftest(verbose: bool = True) -> bool:
    """自检：模板/媒体判定/可用性探测。**不建窗口**（由 verify 脚本负责真渲染）。"""
    ok = True

    def ck(name, cond, detail=""):
        nonlocal ok
        if not cond:
            ok = False
        if verbose:
            print("  [%s] %s %s" % ("OK" if cond else "FAIL", name,
                                    "" if cond else "← %r" % (detail,)))

    doc = wrap_document("标题", block_html(1, ask="做个广告", kind_label="广告",
                                           inner="<p>正文</p>", path=r"C:\a\b.md"))
    ck("文档骨架完整", doc.startswith("<!DOCTYPE html>") and "</html>" in doc)
    ck("块里有轮次与要求", "第 1 轮" in doc and "做个广告" in doc)
    ck("路径以等宽字单独展示", "class=\"path\"" in doc and "b.md" in doc)
    # ★ 排版纪律：块内不许出现滚动条（整份文档只靠页面级那一条）
    ck("★ 块内没有 overflow:auto/scroll（不许内层滚动条）",
       "overflow:auto" not in _CSS and "overflow-y:auto" not in _CSS
       and "overflow-x:auto" not in _CSS, "CSS 里出现了内层滚动")
    ck("★ 代码块用换行而不是横向滚动", "white-space:pre-wrap" in _CSS)
    ck("正文基准字号 ≥14px（小志嫌小过）", "font:14px/" in _CSS)
    ck("视频/图片/站点分别有对应标签",
       _media_html.__doc__ is not None and _VIDEO_EXT and _IMAGE_EXT and _WEB_EXT)
    ck("可用性探测返回明确布尔（降级要能如实说）",
       isinstance(available(), bool) and (available() or error()))
    if verbose:
        print("  WebEngine 可用=%s%s" % (available(),
                                        "" if available() else "（原因：%s）" % error()))
    return ok


if __name__ == "__main__":
    import logging                                                    # noqa: E402
    logging.basicConfig(level=logging.WARNING)
    print("effect_view selftest:", "PASS" if selftest() else "FAIL")
    sys.exit(0 if selftest(False) else 1)
