"""agent_tools —— 让它"会干活"的工具箱（受控权限）。

能力：
- 找文件：find_files(name_or_keyword, folder) → 路径清单（只读）
- 读文件：peek_file(path) → 文本（txt/md/json/py/csv/js/ts 等纯文本；docx 简读 zip xml）
- 看网页：fetch_text(url) → 纯文本（供联网自学）
- 写脚本并运行：ensure_scripts_dir() / write_script / run_script（运行前调用方需确认）

所有操作都有清晰边界：只读、不越权、脚本目录专用。
"""
from __future__ import annotations

import functools
import html
import json
import os
import re
import shutil
import subprocess

def _sp(*a, **kw):
    """subprocess.run 包装：Windows 下自动加 CREATE_NO_WINDOW，运行脚本不弹黑窗。"""
    if os.name == "nt":
        kw = dict(kw, creationflags=kw.get("creationflags", 0) | 0x08000000)
    return subprocess.run(*a, **kw)

import time
import urllib.parse
import urllib.request
import zipfile
from typing import List, Optional, Tuple

import platform_ops                  # 跨平台：打开文件 / 定位 / 脚本解释器（v0.31.1）

if os.name == "nt":
    DATA_DIR = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"),
                            "PASMStudio")
else:
    DATA_DIR = os.path.expanduser("~/.pasmstudio")
# v0.30.11：脚本 / 项目不再落 DATA_DIR，改落**统一工作根**下的分类目录
# （<工作根>/script、<工作根>/project，见 pasm/cognitive/workspace.py）。
# 两个模块级名字保留（外部有 5 处读 `AT.SCRIPTS_DIR`），但值一律由 `_sync_dirs()`
# 现算后写回 —— 免得像 agent_team 那样出现"导入时冻结的陈旧副本"。
SCRIPTS_DIR = ""
PROJECTS_DIR = ""
#: 用户在设置里选的工作根（缺省 = 交给 workspace 解析；隔离 env 仍然优先）
_WS_CFG: dict = {}


def _sync_dirs():
    """按当前工作根重算 SCRIPTS_DIR / PROJECTS_DIR 并建好目录。"""
    global SCRIPTS_DIR, PROJECTS_DIR
    try:
        import workspace as WS
        SCRIPTS_DIR = WS.cat_dir("script", _WS_CFG)
        PROJECTS_DIR = WS.cat_dir("project", _WS_CFG)
    except Exception:  # noqa: BLE001 工作根不可用时退回老位置，保证不炸
        SCRIPTS_DIR = os.path.join(DATA_DIR, "scripts")
        PROJECTS_DIR = os.path.join(DATA_DIR, "projects")
        os.makedirs(SCRIPTS_DIR, exist_ok=True)
        os.makedirs(PROJECTS_DIR, exist_ok=True)
    return SCRIPTS_DIR, PROJECTS_DIR


def scripts_dir() -> str:
    """脚本分类目录（真值来源）。**写文件请走这里**，别读模块级常量。"""
    return _sync_dirs()[0]


def projects_dir() -> str:
    """项目分类目录（真值来源）。"""
    return _sync_dirs()[1]


def set_workspace(root: Optional[str] = None):
    """切换工作空间根（用户在设置里选的目录）。

    传 None / 无效值 → 回到默认（桌面\\PASM工作；隔离时是 <PASM_STUDIO_DIR>/work）。
    v0.30.11：以前落到 `root/scripts`、`root/projects`，现在统一进
    `<工作根>/script`、`<工作根>/project`，与其它产物同一个根、按类型分文件夹。
    """
    _WS_CFG.pop("ws_dir", None)
    if root and os.path.isdir(root):
        _WS_CFG["ws_dir"] = root
    return _sync_dirs()


_sync_dirs()   # 导入即就绪，保持旧行为（import 完 SCRIPTS_DIR 就能用）

_TXT_EXT = {".txt", ".md", ".json", ".py", ".csv", ".js", ".ts", ".html", ".css",
            ".ini", ".log", ".xml", ".yaml", ".yml", ".toml", ".bat", ".sh", ".sql"}
_IGNORE_DIRS = {"$recycle.bin", "system volume information", "node_modules",
                ".git", "__pycache__", "appdata", "program files", "windows",
                "workbuddy", ".workbuddy"}
_MAX_RESULTS = 15

# ============ 本机路径 & 打开应用能力（v0.15 / v0.26.1 真验证） ============
# 内置别名：Windows 自带 + 常见应用。值 "" 表示"用默认浏览器打开首页"。
_APP_ALIASES = {
    "记事本": "notepad.exe", "计算器": "calc.exe", "画图": "mspaint.exe",
    "写字板": "write.exe", "命令提示符": "cmd.exe", "cmd": "cmd.exe",
    "powershell": "powershell.exe", "终端": "powershell.exe",
    "资源管理器": "explorer.exe", "文件管理器": "explorer.exe",
    "此电脑": "explorer.exe", "我的电脑": "explorer.exe", "电脑": "explorer.exe",
    "控制面板": "control.exe", "任务管理器": "taskmgr.exe",
    "注册表编辑器": "regedit.exe", "放大镜": "magnify.exe", "屏幕键盘": "osk.exe",
    "系统信息": "msinfo32.exe", "磁盘清理": "cleanmgr.exe",
    "浏览器": "", "网页浏览器": "",
    "微信": "WeChat.exe", "QQ": "QQ.exe",
}
# v0.26.1：常见品牌应用 → (进程名, 安装目录内的相对 exe 路径候补)。
# 很多应用（网易云/QQ音乐…）无开始菜单快捷方式且不在 PATH，老逻辑只能干瞪眼；
# 这里在 C/D/E 等盘的 Program Files* 与 LOCALAPPDATA 下按候补路径探测真实 exe。
_KNOWN_APPS = {
    "网易云音乐": ("cloudmusic.exe", ["Netease/CloudMusic/cloudmusic.exe",
                                      "CloudMusic/cloudmusic.exe"]),
    "网易云": ("cloudmusic.exe", ["Netease/CloudMusic/cloudmusic.exe",
                                  "CloudMusic/cloudmusic.exe"]),
    "qq音乐": ("QQMusic.exe", ["Tencent/QQMusic/QQMusic.exe",
                                "QQMusic/QQMusic.exe",
                                "Tencent/QzoneMusic/QzoneMusic.exe"]),
    "腾讯音乐": ("QQMusic.exe", ["Tencent/QQMusic/QQMusic.exe",
                                  "QQMusic/QQMusic.exe"]),
    "微信": ("WeChat.exe", ["Tencent/WeChat/WeChat.exe",
                            "WeChat/WeChat.exe"]),
    "企业微信": ("WXWork.exe", ["Tencent/WXWork/WXWork.exe",
                                 "WXWork/WXWork.exe"]),
    "钉钉": ("DingTalkLauncher.exe",
             ["DingDing/main/current/DingTalkLauncher.exe",
              "DingTalk/DingTalkLauncher.exe"]),
    "腾讯会议": ("wemeetapp.exe",
                 ["Tencent/WemeetApps/wemeetapp.exe",
                  "WemeetApps/wemeetapp.exe",
                  "Tencent/WeMeet/wemeetapp.exe"]),
    "哔哩哔哩": ("bilibili.exe", ["bilibili/bilibili.exe",
                                    "Bilibili/bilibili.exe"]),
    "腾讯视频": ("QQPCTVod.exe", ["Tencent/QQLive/QQPCTVod.exe",
                                    "QQLive/QQPCTVod.exe"]),
    "qq浏览器": ("QQBrowser.exe", ["Tencent/QQBrowser/QQBrowser.exe",
                                     "QQBrowser/QQBrowser.exe"]),
    "谷歌浏览器": ("chrome.exe", ["Google/Chrome/Application/chrome.exe"]),
    "chrome": ("chrome.exe", ["Google/Chrome/Application/chrome.exe"]),
    "edge": ("msedge.exe", ["Microsoft/Edge/Application/msedge.exe"]),
    "steam": ("steam.exe", ["Steam/steam.exe"]),
}


def _prog_roots():
    """品牌应用探测根：各盘 Program Files* + 当前用户 LOCALAPPDATA。"""
    roots = []
    seen = set()
    for d in ("C:", "D:", "E:", "F:"):
        if not os.path.isdir(d + os.sep):
            continue
        for sub in ("Program Files", "Program Files (x86)"):
            p = os.path.join(d + os.sep, sub)      # "D:\Program Files..."（d+"\\" 防 join 丢斜杠）
            if os.path.isdir(p):
                roots.append(p)
    la = os.environ.get("LOCALAPPDATA", "")
    if la and os.path.isdir(la):
        roots.append(la)
    for r in roots:
        rl = r.lower()
        if rl not in seen:
            seen.add(rl)
            yield r


def _find_known_app(name: str):
    """按品牌表在真实磁盘上找 exe。返回 (exe_path, proc_name) 或 None。"""
    info = _KNOWN_APPS.get(name.strip().lower())
    if not info:
        return None
    proc, rels = info
    for root in _prog_roots():
        for rel in rels:
            p = os.path.join(root, rel.replace("/", os.sep))
            if os.path.isfile(p):
                return p, proc
        # 兜底：该厂商子目录下第一层直接找 exe（安装目录变体）
        vendor = rels[0].split("/")[0].split("\\")[0]
        base = os.path.join(root, vendor)
        if os.path.isdir(base):
            try:
                for d in os.listdir(base):
                    exe = os.path.join(base, d, proc)
                    if os.path.isfile(exe):
                        return exe, proc
            except Exception:
                pass
    return None


def _proc_alive(proc: str, wait_s: float = 0.0) -> bool:
    """用 tasklist 确认进程名是否在跑（等待 wait_s 秒后再查）。"""
    if not proc:
        return True
    if wait_s:
        try:
            import time as _t
            _t.sleep(wait_s)
        except Exception:
            pass
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq " + proc],
                             capture_output=True, timeout=8).stdout or b""
        return proc.lower() in out.decode("gbk", "ignore").lower() or \
            proc.lower() in out.decode("utf-8", "ignore").lower()
    except Exception:
        return True          # 查不了就不拦（保守放行，别误报失败）


def _launch_and_check(exe: str, proc: str, label: str) -> str:
    """真实启动 + 真实验证：进程出现才算"已打开"；否则如实说明。
    返回给用户看的文本（以 ! 开头 = 失败需提示）。"""
    # 跨平台：Windows 用 os.startfile，macOS 用 open，Linux 用 xdg-open（见 platform_ops）。
    _ok, _msg = platform_ops.open_path(exe)
    if not _ok:
        return "!启动「%s」失败：%s" % (label, _msg)
    if proc and not _proc_alive(proc, 0.9) and not _proc_alive(proc, 1.1):
        # 两次仍未见进程 → 明确告知"只发了指令、没确认到进程"
        return "已发送启动指令，但几秒内没检测到「%s」进程——若窗口没弹出，请检查应用是否正常安装，或告诉我它的安装路径，我直接打开。" % label
    return "已打开「%s」。" % label

# 挂在路径后面的中文尾巴（用户说"D:\报告\材料下的文件"时，真正的路径是 D:\报告\材料）
_PATH_TAILS = ("路径下面的所有文件", "路径下的所有文件", "路径下面的文件", "路径下的文件",
               "目录下的所有文件", "文件夹里的所有文件", "文件夹下的所有文件",
               "里面的所有文件", "里的所有文件", "下的所有文件", "中的所有文件",
               "文件夹里面的文件", "文件夹里的文件", "文件夹下的文件",
               "目录里面的文件", "目录里的文件", "目录下的文件",
               "里面的文件", "里的文件", "中的文件", "下的文件",
               "里面所有文件", "里所有文件", "下所有文件",
               "里面的内容", "里的内容", "中的内容", "的内容",
               "里面", "里的", "中的", "下面的", "下的", "里", "中", "下",
               "文件夹", "目录", "路径", "资料", "文件", "内容", "所有", "的")
# 可读文本类扩展名（peek_file 支持的子集，供 read_folder 过滤）
_READABLE_EXT = _TXT_EXT | {".docx", ".xlsx", ".xlsm"}
_PATH_TAIL_RE = None  # 运行时初始化，避免重复编译


def _tail_re():
    global _PATH_TAIL_RE
    if _PATH_TAIL_RE is None:
        # 长词优先；按出现频率从长到短排序保证贪婪匹配
        words = sorted(set(_PATH_TAILS), key=len, reverse=True)
        _PATH_TAIL_RE = re.compile("(?:" + "|".join(map(re.escape, words)) + ")+$")
    return _PATH_TAIL_RE


def _path_candidates(text: str) -> list:
    r"""从聊天文本中抽出可能的路径候选（引号内路径 → 盘符绝对路径 → ~ 用户目录）。

    v0.31.4 追加**空格容错候选**：输入侧（输入法/剪贴板）会在英文与中文之间插空格
    （真机实录："D:\Code 副\business"，真实目录 D:\Code副）。旧正则字符类里有 \s，
    候选在空格处截断成 "D:\Code" → 父目录回退一路退到盘根，readfolder 把整个盘
    根目录当成分析对象。这里对带空格的区间补两条候选：带空格原样 + 去空格变体。
    两者都只在**真实存在**时才会被 resolve_path 采用，不会误返回。
    """
    out = []
    for m in re.finditer(r"[\"“'‘]([A-Za-z]:[\\/][^\"”'’]+)[\"”'’]", text):
        out.append(m.group(1).strip().rstrip("\\/"))
    for m in re.finditer(r"[A-Za-z]:[\\/][^，。；、！？\s\"“”'’<>|*?]*", text):
        out.append(m.group(0).strip().rstrip("\\/"))
    for m in re.finditer(r"~[\\/][^，。；、！？\s\"“”'’<>|*?]*", text):
        out.append(os.path.expanduser(m.group(0).strip().rstrip("\\/")))
    # 空格容错：允许空格的宽松区间（只被句读/引号等终止，不 \s 截断）；
    # 去空格变体放在最后 —— 优先级：引号路径 > 精确无空格 > 带空格原文 > 去空格。
    for m in re.finditer(r"[A-Za-z]:[\\/][^，。；、！？\"“”'’<>|*?]*", text):
        c = m.group(0).strip().rstrip("\\/")
        if c and re.search(r"[ \u00a0\u3000]", c):
            out.append(c)
            out.append(re.sub(r"[ \u00a0\u3000]+", "", c))
    return out


def resolve_path(text: str) -> str:
    """从聊天文本解析出**真实存在**的路径（文件或文件夹）；没有则返回空串。
    策略（v0.31.4 两阶段）：
      阶段一：候选里**哪个真实存在**就用哪个（引号 > 精确 > 带空格原文 > 去空格）——
              存在性是唯一裁判，绝不会返回不存在的路径；
      阶段二：全都存在不了，才做"剥中文尾巴 / 逐级找最深已存在父目录"的回退，
              且**绝不回退到盘根**（真机实录：回退到 "D:/"，readfolder 把整个
              D 盘根目录当成分析对象，模型只能拿根目录清单编故事）。"""
    cands = [c.strip() for c in _path_candidates(text) if c.strip()]
    # ── 阶段一：存在即胜 ──
    for cand in cands:
        if os.path.exists(cand):
            return cand
    # ── 阶段二：回退（仅当没有任何候选真实存在）──
    for cand in cands:
        if re.search(r"\.[A-Za-z0-9]{1,6}$", cand):   # 自带扩展名但不存在 → 不猜目录
            continue
        probe = cand.rstrip("\\/ ")
        # 逐字剥掉末尾中文（"下的文件/里的内容/有没有问题"等任意说法），存在即停
        while probe and "\u4e00" <= probe[-1] <= "\u9fff":
            probe = probe[:-1]
            if os.path.exists(probe):
                return probe
        # 中文剥完仍不存在：逐级找最深已存在父目录（应对 ASCII 尾巴等），
        # 剥到盘根（"D:\"）为止 —— **盘根不许拿来充数**。
        _drv = len(os.path.splitdrive(probe)[0]) + 1
        while len(probe) > _drv:
            probe = os.path.dirname(probe)
            if len(probe) <= _drv:
                break          # 已经退到盘根 → 弃这个候选，试下一个
            if os.path.exists(probe):
                return probe
    return ""


def _with_ledger(action: str):
    """装饰器：把执行结果记进台账（供防幻觉守卫核对"真做了没有"）。

    为什么不逐个 return 插桩：这些函数分支多（按后缀分派、多入口），
    **漏一个 return 就会让"真执行"变成"查无账"，护栏就会误拦正确回复**。
    装饰器包在函数外层，不可能漏。

    ok 判定**宁松勿严**：只有明确的失败措辞才算 False。
    """
    _BAD = ("失败", "没找到", "未找到", "不存在", "读不了", "取消",
            "装好环境", "需要对应", "不支持", "超时")

    def deco(fn):
        @functools.wraps(fn)
        def wrap(*a, **kw):
            out = fn(*a, **kw)
            try:
                import sysops as _SYS
                txt = str(out or "")
                _SYS.note_action(action, (str(a[0]) if a else "")[:120],
                                 ok=not any(k in txt for k in _BAD),
                                 reason=txt[:80])
            except Exception:
                pass          # 记账失败绝不影响真正的执行
            return out
        return wrap
    return deco


@_with_ledger("open_app")
def open_app(name: str) -> str:
    """打开电脑上的应用/文件/文件夹。返回给用户看的消息；以 "!" 开头表示失败原因。"""
    name = (name or "").strip().strip('"“”‘’。，,.。')
    if not name:
        return "!想打开什么呢？告诉我应用名（如：记事本 / 微信 / 计算器）或路径。"
    if name in ("文件夹", "目录", "路径", "一个文件夹"):
        return "!要打开哪个文件夹？把路径发我就行，例如：打开 D:\\下载"
    # 1) 内置系统应用 / 常见应用别名（系统自带 exe 在 System32，直接启动并验证）
    exe = _APP_ALIASES.get(name)
    if exe is not None:
        if exe == "":                                  # 浏览器：用默认浏览器打开首页
            try:
                import webbrowser
                webbrowser.open("https://www.bing.com")
                return f"已打开浏览器。"
            except Exception as ex:
                return "!打开浏览器失败：" + str(ex)
        sys32 = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                             "System32", exe)
        if os.path.isfile(sys32):
            return _launch_and_check(sys32, exe, name)
        # System32 没有 → 该应用可能不在本机（如微信）→ 落到品牌探测/快捷方式搜索
    # 1b) 品牌应用真实路径探测（网易云/QQ音乐/微信/钉钉/腾讯会议…，装了就能开）
    hit = _find_known_app(name)
    if hit:
        exe_path, _proc = hit
        return _launch_and_check(exe_path, os.path.basename(exe_path), name)
    # 2) 输入本身是路径（文件/文件夹 → 系统默认方式打开）
    p = resolve_path(name)
    if p:
        try:
            platform_ops.startfile(p)
            return f"已打开：`{p}`"
        except Exception as ex:
            return "!打开失败：" + str(ex)
    # 3) 开始菜单 / 开始屏幕快捷方式搜索（*.lnk 可直接 startfile）
    for base in (os.environ.get("APPDATA", ""), os.environ.get("ProgramData", "")):
        if not base:
            continue
        sm = os.path.join(base, "Microsoft", "Windows", "Start Menu", "Programs")
        if not os.path.isdir(sm):
            continue
        for root, _dirs, files in os.walk(sm):
            for fn in files:
                if not fn.lower().endswith(".lnk"):
                    continue
                if name.lower() in fn[:-4].lower():
                    try:
                        platform_ops.startfile(os.path.join(root, fn))
                        return f"已打开「{fn[:-4]}」。"
                    except Exception:
                        continue
    # 4) PATH 中的命令（白名单校验，避免把整句话当命令执行）
    cmd = re.sub(r"[^A-Za-z0-9_.\-]", "", name)
    if cmd and len(cmd) >= 2:
        found = which(cmd)
        if found:
            return _launch_and_check(found, os.path.basename(found), name)
    return (f"!没找到叫「{name}」的应用（本机没探测到它的安装）。可以：\n"
            f"1) 说应用全名，如 记事本 / 计算器 / 微信 / 此电脑\n"
            f"2) 或直接给我要打开的可执行/快捷方式路径，如 打开 D:\\下载")


@_with_ledger("open_path")
def open_path(path: str) -> str:
    """用系统默认方式打开文件/文件夹。成功返回规范化路径；失败返回以 "!" 开头的错误。"""
    p = resolve_path(path)
    if not p:
        return "!" + (path or "空路径")
    try:
        platform_ops.startfile(p)
    except Exception as ex:
        return "!" + str(ex)
    return p


def read_folder(path: str, max_files: int = 6, per_file: int = 2500,
                max_total: int = 12000) -> str:
    """列出文件夹内容并抽取其中可读文本类文件的内容片段（供总结/分析用）。
    超大或非文本文件只列名不读内容。返回以「📁 路径」开头的文本；失败返回空串。"""
    if not os.path.isdir(path):
        return ""
    try:
        entries = sorted(os.listdir(path))
    except Exception:
        return ""
    lines = [f"📁 {path}"]
    dirs = [e for e in entries if os.path.isdir(os.path.join(path, e))]
    files = [e for e in entries if not os.path.isdir(os.path.join(path, e))]
    if dirs:
        lines.append("📂 子文件夹：" + "、".join(dirs[:20]) +
                     (" …" if len(dirs) > 20 else ""))
    if not files:
        lines.append("（该文件夹下没有可列出的文件）")
        return "\n".join(lines)
    total = sum(len(l) for l in lines)
    usable = skipped = 0
    for fn in files:
        if usable >= max_files or total > max_total:
            break
        full = os.path.join(path, fn)
        ext = os.path.splitext(fn)[1].lower()
        try:
            size = os.path.getsize(full)
        except Exception:
            continue
        if size > 8 * 1024 * 1024 or ext not in _READABLE_EXT:
            skipped += 1
            continue
        txt = peek_file(full, per_file)
        if not txt:
            skipped += 1
            continue
        usable += 1
        seg = f"\n\n────────── 《{fn}》({_fmt_size(size)}) ──────────\n{txt}"
        total += len(seg)
        if total > max_total:
            break
        lines.append(seg)
    if skipped:
        lines.append(f"\n（另有 {skipped} 个文件未展示：非文本/超 8MB/不支持格式）")
    return "\n".join(lines)


def _clean_path(p: str) -> str:
    p = p.strip().strip('"\'，。')
    if os.name == "nt" and not os.path.isabs(p):
        p = os.path.expanduser("~")
    return p


def find_files(keyword: str, folder: str = "") -> List[str]:
    """按文件名/关键词搜索（递归，只读）。folder 空→当前用户目录。"""
    folder = _clean_path(folder) if folder else os.path.expanduser("~")
    if not os.path.isdir(folder):
        return []
    kw = keyword.lower()
    hits: List[str] = []
    for root, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if d.lower() not in _IGNORE_DIRS
                   and not d.startswith(".")]
        if len(hits) >= _MAX_RESULTS * 2:
            break
        for fn in files:
            low = fn.lower()
            if kw in low or kw in root.lower():
                hits.append(os.path.join(root, fn))
            if len(hits) >= _MAX_RESULTS * 2:
                break
    return hits[:_MAX_RESULTS]


# ---- v0.30.11 #5：含糊文件指代解析（"打开E盘里的txt" 这类没有具体文件名的指令）----
# 文件类型关键词 → 扩展名模式（中文口语常见说法）
_TYPE_HINTS = {
    "txt": [".txt"], "文本": [".txt"], "记事本": [".txt"], "笔记": [".txt"],
    "word": [".docx", ".doc"], "文档": [".docx", ".doc", ".txt", ".pdf"],
    "excel": [".xlsx", ".xls"], "表格": [".xlsx", ".xls", ".csv"],
    "csv": [".csv"], "pdf": [".pdf"], "ppt": [".pptx", ".ppt"],
    "图片": [".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"],
    "照片": [".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"],
    "视频": [".mp4", ".mkv", ".avi", ".mov", ".wmv"],
    "音频": [".mp3", ".wav", ".flac", ".m4a"], "音乐": [".mp3", ".wav", ".flac", ".m4a"],
}


def _walk_for(root, exts, kw, max_hits=40, max_depth=4):
    """在单根下做**有界**递归：深度受限 + 命中即停 + 跳过系统/缓存目录。"""
    if not os.path.isdir(root):
        return []
    res = []
    try:
        for cur, dirs, files in os.walk(root):
            depth = cur[len(root):].count(os.sep)
            if depth >= max_depth:
                dirs[:] = []
            dirs[:] = [d for d in dirs if d.lower() not in _IGNORE_DIRS
                       and not d.startswith(".")]
            for fn in files:
                low = fn.lower()
                ok = (any(low.endswith(e) for e in exts) if exts
                      else (kw and kw in low))
                if ok:
                    res.append(os.path.join(cur, fn))
                    if len(res) >= max_hits:
                        return res
    except Exception:
        pass
    return res


def resolve_vague_file(text):
    """含糊文件指代解析：'打开E盘里的txt' / '读D盘那个word' 之类没有具体文件名的指令。

    返回**最匹配的一个真实文件路径**，找不到返回 ''。
    搜索策略（刻意避免全盘扫）：
      · 指明盘符 → 只扫该盘的常用目录（根/Desktop/Documents/Downloads/用户目录）；
      · 否则 → 扫当前用户的 Desktop/Documents/Downloads，仍未果再浅扫各盘根。
    命中多个时优先「用户目录」且按修改时间最新排序（最符合"最近要找的那份"）。
    """
    if not text:
        return ""
    low = text.lower()
    # 1) 盘符（"E盘" / "e盘" / "E:"）
    drive = ""
    m = re.search(r"([a-z])\s*盘", low)
    if m:
        drive = m.group(1).upper() + ":"
    # 2) 类型 / 扩展名 / 关键词
    exts, kw = [], ""
    for k, ext_list in _TYPE_HINTS.items():
        if k in low:
            exts = ext_list
            break
    if not exts:
        em = re.search(r"\.([a-z0-9]{1,6})", low)
        if em:
            exts = ["." + em.group(1)]
    if not exts:
        fm = re.search(r"[「'\"《]([^」'\"》]{1,40})[」'\"》]", text)
        if fm:
            kw = fm.group(1).strip().lower()
    if not drive and not exts and not kw:
        return ""                      # 既没盘符也没类型也没关键词 → 没法猜，放弃
    # 3) 选搜索根
    user = os.path.expanduser("~")
    if drive:
        d = drive
        cand = [f"{d}\\", f"{d}\\Users\\{os.path.basename(user)}\\Desktop",
                f"{d}\\Desktop", f"{d}\\Documents", f"{d}\\Downloads",
                f"{d}\\Users"]
        roots = [c for c in cand if os.path.isdir(c)]
        if not roots and os.path.exists(f"{d}\\"):
            roots = [f"{d}\\"]
    else:
        roots = [os.path.join(user, "Desktop"), os.path.join(user, "Documents"),
                 os.path.join(user, "Downloads")]
    # 4) 搜索
    hits = []
    for r in roots:
        hits += _walk_for(r, exts, kw, max_hits=40, max_depth=4)
    if not hits and not drive:
        for d in ("C:", "D:", "E:", "F:"):
            rp = f"{d}\\"
            if os.path.exists(rp):
                hits += _walk_for(rp, exts, kw, max_hits=20, max_depth=2)
    if not hits:
        return ""
    # 5) 排序：用户目录优先 + 修改时间最新
    user_roots = (os.path.join(user, "Desktop"), os.path.join(user, "Documents"),
                  os.path.join(user, "Downloads"))

    def _score(p):
        in_user = any(p.startswith(ur) for ur in user_roots)
        try:
            mt = os.path.getmtime(p)
        except OSError:
            mt = 0
        return (1 if in_user else 0, mt)
    hits.sort(key=_score, reverse=True)
    return hits[0]


def peek_file(path: str, max_chars: int = 6000) -> str:
    path = _clean_path(path)
    if not os.path.exists(path):
        return ""
    if os.path.isdir(path):
        return list_dir(path)
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext in _TXT_EXT:
            raw = open(path, "r", encoding="utf-8", errors="ignore").read()
        elif ext == ".docx":
            raw = _docx_text(path)
        elif ext in (".xlsx", ".xlsm"):
            raw = _xlsx_text(path)
        else:
            return ""
        return raw[:max_chars]
    except Exception:
        return ""


def list_dir(path: str, max_items: int = 60) -> str:
    """列出一个目录的内容（文件+子目录），返回人类可读清单。"""
    path = _clean_path(path)
    if not os.path.isdir(path):
        return ""
    try:
        entries = sorted(os.listdir(path))
    except Exception:
        return ""
    dirs, files = [], []
    for e in entries:
        try:
            full = os.path.join(path, e)
            if os.path.isdir(full):
                dirs.append(e)
            else:
                size = os.path.getsize(full)
                files.append(f"{e}  ({_fmt_size(size)})")
        except Exception:
            continue
    head = f"📁 {path}\n"
    parts = ["[文件夹]"] + dirs[:20] if dirs else []
    body = []
    if dirs:
        body.append("📂 " + "、".join(dirs[:20]) + (" …" if len(dirs) > 20 else ""))
    if files:
        shown = files[:max_items]
        body.append("📄 " + "\n📄 ".join(shown))
        if len(files) > len(shown):
            body.append(f"… 还有 {len(files) - len(shown)} 个文件")
    if not body:
        return head + "（空目录）"
    return (head + "\n".join(body))[:4000]


def _fmt_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}TB"


def _xlsx_text(path: str) -> str:
    """读 xlsx 为制表文本（共享字符串 + 前几个 sheet），尽力而为。"""
    try:
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
            shared = []
            if "xl/sharedStrings.xml" in names:
                xml = z.read("xl/sharedStrings.xml").decode("utf-8", "ignore")
                for si in re.findall(r"<si>(.*?)</si>", xml, flags=re.S):
                    txt = "".join(re.findall(r"<t[^>]*>(.*?)</t>", si, flags=re.S))
                    shared.append(txt)
            sheet_names = sorted([n for n in names
                                  if re.match(r"xl/worksheets/sheet\d+\.xml$", n)])[:3]
            out = []
            for sh in sheet_names:
                xml = z.read(sh).decode("utf-8", "ignore")
                sheet_lines = []
                # 分步处理每行
                for row in re.findall(r"<row[^>]*>(.*?)</row>", xml, flags=re.S):
                    line = []
                    for cm in re.finditer(r"<c\b[^>]*?(?:/>|>(.*?)</c>)", row, flags=re.S):
                        ctag = cm.group(0)
                        ttype = re.search(r't="(\w+)"', ctag)
                        tt = ttype.group(1) if ttype else ""
                        inner = cm.group(1) or ""
                        if tt == "inlineStr":
                            m = re.search(r"<t[^>]*>(.*?)</t>", inner, flags=re.S)
                            line.append(m.group(1) if m else "")
                        elif tt == "s":
                            m = re.search(r"<v>(.*?)</v>", inner, flags=re.S)
                            idx = int(m.group(1)) if m and m.group(1).strip().lstrip("-").isdigit() else -1
                            line.append(shared[idx] if 0 <= idx < len(shared) else "")
                        elif tt == "str" or not tt:
                            m = re.search(r"<v>(.*?)</v>", inner, flags=re.S)
                            line.append(m.group(1) if m else "")
                    sheet_lines.append("\t".join(line).rstrip("\t"))
                if sheet_lines:
                    out.append("\n".join(sheet_lines))
            return "\n\n".join(out)[:8000]
    except Exception:
        return ""


def read_project(folder: str, max_chars: int = 9000) -> str:
    """读一个项目/文件夹：列出结构 + 读最可能的关键文件（README/入口/配置）。"""
    folder = _clean_path(folder)
    if not os.path.isdir(folder):
        return ""
    listing = list_dir(folder)
    # 找高价值文本文件（README / 入口 / 配置）
    cand = []
    for root, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if d.lower() not in _IGNORE_DIRS
                   and not d.startswith(".") and d not in (".git",)]
        for fn in files:
            low = fn.lower()
            if low.startswith(("readme", "read me")) or low in (
                    "main.py", "app.py", "index.py", "__init__.py", "index.html",
                    "package.json", "pyproject.toml", "requirements.txt",
                    "dockerfile", "installer.iss", "setup.py"):
                cand.append(os.path.join(root, fn))
        if len(cand) >= 6:
            break
    parts = [listing]
    for p in cand[:4]:
        txt = peek_file(p, max_chars=3000)
        if txt:
            parts.append("\n===== " + os.path.basename(p) + " =====\n" + txt)
    return "\n".join(parts)[:max_chars]


def _docx_text(path: str) -> str:
    try:
        with zipfile.ZipFile(path) as z:
            xml = z.read("word/document.xml").decode("utf-8", "ignore")
        paras = re.findall(r"<w:p[ >].*?</w:p>", xml, flags=re.S)
        out = []
        for p in paras[:200]:
            texts = re.findall(r"<w:t[^>]*>(.*?)</w:t>", p, flags=re.S)
            if texts:
                out.append("".join(texts))
        return "\n".join(out)
    except Exception:
        return ""


def fetch_text(url: str, max_chars: int = 4000) -> str:
    """抓网页正文（尽力而为），失败返回空。"""
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) PASMCompanion/0.9"})
        with urllib.request.urlopen(req, timeout=15) as r:
            raw = r.read().decode("utf-8", "ignore")
    except Exception:
        return ""
    raw = re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>", " ", raw)
    text = re.sub(r"<[^>]+>", " ", raw)
    text = html.unescape(re.sub(r"\s+", " ", text))
    return text.strip()[_skip_banner(text):][:max_chars] if text else ""


def _skip_banner(text: str) -> int:
    idx = 0
    for marker in ("正文", "摘要", "简介"):
        j = text.find(marker)
        if j > 0 and (idx == 0 or j < idx):
            idx = j
    return min(idx, 500)


# ---------------- 自主搜学：多来源回退（v0.14） ----------------
def _plain(raw: str) -> str:
    raw = re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>|<noscript[\s\S]*?</noscript>",
                 " ", raw)
    t = re.sub(r"<[^>]+>", " ", raw)
    t = html.unescape(re.sub(r"\s+", " ", t))
    return t.strip()


def _search_snippets(raw: str) -> str:
    """解析搜索引擎结果页（必应 b_algo / 搜狗 result），返回标题+摘要拼接。"""
    parts = []
    blocks = re.findall(r'<li[^>]*class=["\']?[^"\']*b_algo[^"\']*["\']?[^>]*>([\s\S]*?)</li>', raw)
    if not blocks:
        blocks = re.findall(r'<div[^>]*class=["\'][^"\']*(?:result|vrwrap)[^"\']*["\'][^>]*>([\s\S]*?)</div>', raw)
    for b in blocks[:7]:
        tt = re.search(r"<h[23][^>]*>([\s\S]*?)</h[23]>", b)
        sn = re.search(r"<p[^>]*>([\s\S]*?)</p>", b)
        seg = (tt.group(1) if tt else "") + " " + (sn.group(1) if sn else "")
        seg = html.unescape(re.sub(r"<[^>]+>", " ", seg))
        seg = re.sub(r"\s+", " ", seg).strip()
        if len(seg) > 30:
            parts.append(seg)
    return "\n".join(parts)


def fetch_topic_text(topic: str, max_chars: int = 24000,
                     return_srcs: bool = False):
    """自主搜学：百科 → 维基 → 必应 → 搜狗逐源尝试，命中即汇入，返回学习文本。

    v0.27.8：默认上限由 6k 提到 24k（资料更全）；return_srcs=True 时额外返回
    真实抓到的来源 URL 列表，便于把"出处"记进知识库（资料不全→补来源）。"""
    q = urllib.parse.quote(topic)
    srcs = [
        ("baike", "https://baike.baidu.com/item/" + q),
        ("wiki", "https://zh.wikipedia.org/wiki/" + q),
        ("bing", "https://cn.bing.com/search?q=" + q),
        ("sogou", "https://www.sogou.com/web?query=" + q),
    ]
    ua = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    pieces = []
    got_urls = []
    for label, url in srcs:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": ua})
            with urllib.request.urlopen(req, timeout=12) as r:
                raw = r.read().decode("utf-8", "ignore")
        except Exception:
            continue
        if label in ("bing", "sogou"):
            text = _search_snippets(raw)
        else:
            text = _plain(raw)
            if text:
                for marker in ("摘要", "简介", "正文"):
                    j = text.find(marker)
                    if 0 < j < 3000:
                        text = text[j:]
                        break
                cut = text.find(" 阅读 编辑 查看历史 ")
                if cut > 0:
                    text = text[:cut]
        if text and len(text) > 150:
            pieces.append(text.strip())
            got_urls.append(url)
        if sum(len(p) for p in pieces) >= max_chars:
            break
    text = "\n".join(pieces)[:max_chars]
    if return_srcs:
        return text, got_urls
    return text


# ---------------- 多语言开发支持（v0.13） ----------------
# 语言 → (扩展名, 运行器探测命令, 人话说明)
LANGS = {
    "python": (".py", "python", "Python"),
    "javascript": (".js", "node", "JavaScript/Node"),
    "node": (".js", "node", "JavaScript/Node"),
    "html": (".html", "", "HTML 网页"),
    "css": (".css", "", "CSS"),
    "vue": (".html", "", "Vue 网页"),
    "java": (".java", "java", "Java"),
    "go": (".go", "go", "Go"),
    "golang": (".go", "go", "Go"),
    "c": (".c", "", "C"),
    "cpp": (".cpp", "", "C++"),
    "c++": (".cpp", "", "C++"),
    "sql": (".sql", "", "SQL"),
    "bat": (".bat", "", "Windows 批处理"),
    "shell": (".sh", "", "Shell"),
    "typescript": (".ts", "", "TypeScript"),
}


def detect_lang(code: str = "", req: str = "") -> str:
    """从需求/代码推断语言键名（LANGS 的 key）。默认 python。"""
    text = (req + " " + code[:600]).lower()
    table = [("vue", ("vue",)), ("html", ("html", "网页", "页面", "前端", "h5", "<!doctype")),
             ("sql", ("sql", "建表", "数据库", "sqlite", "mysql", "查询语句")),
             ("javascript", ("javascript", " js ", "js版", "node", "脚本.js")),
             ("typescript", ("typescript", " ts ")),
             ("go", (" golang", "go语言", "go 语言", "golang")),
             ("java", ("java语言", "java ", "spring")),
             ("cpp", ("c++", "cpp")),
             ("c", ("c语言", " c 语言")),
             ("bat", ("bat", "批处理")),
             ("shell", ("shell", "bash", "shell脚本"))]
    for key, kws in table:
        if any(k in text for k in kws):
            return key
    if code:
        low = code.lower()
        if low.lstrip().startswith(("<!doctype", "<html")) or "<html" in low[:200]:
            return "html"
        if "public static void main" in low:
            return "java"
        if "package main" in low and "func main" in low:
            return "go"
        if re.search(r"\b(console\.log|function\s+\w+|require\(|const\s+\w+\s*=)", low) \
                and "def " not in low:
            return "javascript"
        if re.search(r"\b(def\s+\w+|import\s+\w+|print\()", low):
            return "python"
    return "python"


def which(tool: str) -> str:
    """探测运行器是否可用，返回路径或空。"""
    try:
        return shutil.which(tool) or ""
    except Exception:
        return ""


def runtime_hint(ext: str) -> str:
    """没有对应运行器时给用户的安装提示。"""
    return {
        ".js": "本机没装 Node.js（去 https://nodejs.cn 下载安装后我就能跑 JS 了）",
        ".java": "本机没装 JDK（安装 JDK 17+ 后我就能编译运行 Java）",
        ".go": "本机没装 Go（安装 Go 1.21+ 后我就能跑 Go 程序）",
        ".cpp": "本机没有 C++ 编译器（装 MinGW-w64 或 VS Build Tools 后可运行）",
        ".ts": "本机没有 ts-node/Deno 运行环境",
    }.get(ext, "")


def write_script(name: str, code: str, lang: str = "") -> str:
    """写脚本到专用目录；lang 空→自动识别；返回文件路径。"""
    if not lang:
        lang = detect_lang(code=code)
    ext = LANGS.get(lang, (".py",))[0]
    safe = re.sub(r"[^\w\-. ]", "_", name)[:40] or "script"
    path = os.path.join(scripts_dir(), safe + ext)
    with open(path, "w", encoding="utf-8") as f:
        f.write(code)
    return path


@_with_ledger("run_script")
def run_script(path: str, timeout: int = 60) -> str:
    """按扩展名分派运行。Python/JS/Go/Bat 直接跑；HTML 用浏览器打开；
    Java 编译后运行；其余给出运行方式说明。"""
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext == ".py":
            py = os.environ.get("PASM_PY", "python")
            r = _sp([py, path], capture_output=True, text=True,
                               timeout=timeout, encoding="utf-8", errors="replace")
        elif ext == ".js":
            node = which("node")
            if not node:
                return "代码已写好：`" + path + "`\n" + runtime_hint(".js")
            r = _sp([node, path], capture_output=True, text=True,
                               timeout=timeout, encoding="utf-8", errors="replace")
        elif ext == ".go":
            go = which("go")
            if not go:
                return "代码已写好：`" + path + "`\n" + runtime_hint(".go")
            r = _sp([go, "run", path], capture_output=True, text=True,
                               timeout=timeout, encoding="utf-8", errors="replace")
        elif ext == ".java":
            javac = which("javac")
            if not javac:
                return "代码已写好：`" + path + "`\n" + runtime_hint(".java")
            d = os.path.dirname(path)
            _sp([javac, path], capture_output=True, text=True,
                           timeout=timeout, cwd=d)
            cls = os.path.splitext(os.path.basename(path))[0]
            java = which("java") or "java"
            r = _sp([java, "-cp", d, cls], capture_output=True,
                               text=True, timeout=timeout, encoding="utf-8",
                               errors="replace")
        elif ext == ".bat":
            # .bat 是 Windows 批处理；非 Windows 上不能假装能跑，如实说清。
            _argv = platform_ops.script_argv(path)
            if _argv is None:
                return ("这是 Windows 批处理脚本（.bat），%s 上无法直接运行。"
                        % platform_ops.platform_name())
            r = _sp(_argv, capture_output=True,
                               text=True, timeout=timeout, encoding="utf-8",
                               errors="replace")
        elif ext == ".html":
            _ok, _msg = platform_ops.open_path(path)   # 用默认浏览器打开看效果
            if _ok:
                try:
                    import sysops as _SYS
                    _SYS.note_action("open_browser", path, ok=True)
                except Exception:
                    pass
                return ("网页已写好并用浏览器打开：`" + path + "`\n"
                        "（没弹出来的话手动双击该文件即可）")
            return "网页已写好：`" + path + "`（双击即可在浏览器打开；%s）" % _msg
        elif ext == ".sh":
            sh = which("bash")
            if not sh:
                return "代码已写好：`" + path + "`（需要 bash 环境运行）"
            r = _sp([sh, path], capture_output=True, text=True,
                               timeout=timeout, encoding="utf-8", errors="replace")
        else:
            return (f"代码已写好：`{path}`\n"
                    f"（{ext} 需要对应编译器/环境，我已保存好，装好环境后让我\"运行 刚才的脚本\"）")
        out = (r.stdout or "") + ("\n[错误]\n" + r.stderr if r.stderr else "")
        return out[:3000] or "（脚本无输出）"
    except subprocess.TimeoutExpired:
        return "（脚本运行超时，可能需要更长时间或存在交互输入）"
    except Exception as ex:
        return f"运行失败：{ex}"


# ---------------- 全栈项目支持（v0.13） ----------------
FILE_MARK = re.compile(r"^===FILE:\s*(.+?)\s*===$", re.M)


def parse_bundle(text: str) -> dict:
    """解析 LLM 输出的多文件包：
    ===FILE: 路径===
    内容
    ===END===
    返回 {相对路径: 内容}。也兼容 ```lang 围栏内的单文件。"""
    files = {}
    marks = list(FILE_MARK.finditer(text))
    if marks:
        for i, m in enumerate(marks):
            start = m.end()
            end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
            body = text[start:end]
            body = re.sub(r"^===END===\s*$", "", body.strip(), flags=re.M).strip()
            if body:
                files[m.group(1).strip().replace("\\", "/")] = body + "\n"
    return files


def save_project(name: str, files: dict) -> Tuple[str, str]:
    """把 {相对路径: 内容} 写入 projects/<名字>/，返回 (项目目录, 文件树文本)。"""
    safe = re.sub(r"[^\w\- ]", "_", name)[:24].strip() or "project"
    pdir = os.path.join(projects_dir(), safe)
    os.makedirs(pdir, exist_ok=True)
    for rel, content in files.items():
        rel = rel.lstrip("/ ")
        if ".." in rel:
            continue
        full = os.path.join(pdir, *rel.split("/"))
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
            f.write(content)
    tree = "📁 " + pdir + "\n" + _tree(pdir)
    _register_project(safe, pdir, list(files))
    return pdir, tree


def _tree(pdir: str, max_n: int = 20) -> str:
    rows = []
    for root, dirs, fs in os.walk(pdir):
        dirs[:] = [d for d in dirs if d not in ("node_modules", ".git", "__pycache__")]
        rel = os.path.relpath(root, pdir)
        prefix = "" if rel == "." else rel + "/"
        for f in fs[:max_n]:
            rows.append("  " + prefix + f)
    return "\n".join(rows[:max_n])


# ---------------- 文件生成（v0.13）：Word / PPT / Excel / 任意路径保存 ----------------
def desktop_dir() -> str:
    """用户的桌面（兼容 OneDrive 重定向）。"""
    home = os.path.expanduser("~")
    for cand in (os.path.join(home, "Desktop"),
                 os.path.join(home, "OneDrive", "Desktop"),
                 os.path.join(home, "桌面")):
        if os.path.isdir(cand):
            return cand
    return home


def _safe_dest(path: str) -> str:
    """目标目录守卫：拒绝系统目录。"""
    low = path.lower()
    for bad in ("c:\\windows", "c:/windows", "program files", "system32"):
        if low.startswith(bad):
            raise ValueError("不能写入系统目录，请选一个普通文件夹（如桌面）")
    return path


def save_file(path: str, content: str) -> str:
    """把文本内容写到用户指定的任意路径（自动建目录）。返回最终路径。"""
    path = _safe_dest(path.strip().strip('"'))
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


def _md_sections(md: str):
    """把 LLM 的 Markdown 拆成 (标题, [(节标题, [行])])。"""
    lines = md.strip().splitlines()
    title = ""
    secs: List[tuple] = []
    cur_h, cur = "", []
    for ln in lines:
        s = ln.rstrip()
        if not title and s.strip() and not s.startswith("#") and not s.startswith("|"):
            title = s.strip().lstrip("# ").strip()
            continue
        if s.startswith("## ") or s.startswith("# "):
            if cur_h or cur:
                secs.append((cur_h, cur))
            cur_h, cur = s.lstrip("# ").strip(), []
        else:
            if s.strip() or cur:
                cur.append(s.strip())
    if cur_h or cur:
        secs.append((cur_h, cur))
    return (title or "未命名"), secs


def _doc_dir() -> str:
    """办公文档默认落点：工作根/doc（v0.30.11 前是桌面根目录）。

    以前生成的 .docx/.pptx 直接堆在桌面上，和图片、项目、脚本各在一处；
    现在统一进工作根的 doc/ 分类。
    """
    try:
        import workspace as WS
        return WS.cat_dir("doc")
    except Exception:  # noqa: BLE001
        return desktop_dir()


def _auto_path(path: str, title: str, ext: str) -> str:
    if path and (path.lower().endswith(ext) or os.path.splitext(path)[1]):
        if path.lower().endswith(ext):
            return path
    name = re.sub(r"[^\w\- ]", "", title)[:24] or "文档"
    stamp = time.strftime("%m%d_%H%M")
    return os.path.join(path or _doc_dir(), f"{name}_{stamp}{ext}")


def make_docx(md: str, path: str = "", imgs: dict = None) -> str:
    """Markdown(标题+##节+段落/-列表) → Word 文档。优先 python-docx，兜底手写 OOXML。

    imgs={节标题: 图片路径} 时，在每节正文后插入配图（仅 python-docx 路径支持；
    兜底路径为纯手写 OOXML，不含图片）。
    """
    title, secs = _md_sections(md)
    path = _auto_path(path, title, ".docx")
    try:
        from docx import Document
        from docx.shared import Inches
        d = Document()
        d.add_heading(title, 0)
        for h, rows in secs:
            if h:
                d.add_heading(h, 1)
            for r in rows:
                if r.startswith("- "):
                    d.add_paragraph(r[2:], style="List Bullet")
                elif r:
                    d.add_paragraph(r)
            img = (imgs or {}).get(h)
            if img and os.path.isfile(img):
                try:
                    d.add_picture(img, width=Inches(5.6))
                except Exception:
                    pass
        d.save(path)
        return path
    except ImportError:
        pass
    # 兜底：手工构造最小 docx（Word/WPS 均可打开）
    esc = lambda s: (s.replace("&", "&amp;").replace("<", "&lt;")
                     .replace(">", "&gt;"))
    body = [f'<w:p><w:pPr><w:jc w:val="center"/></w:pPr>'
            f'<w:r><w:rPr><w:b/><w:sz w:val="44"/></w:rPr>'
            f'<w:t xml:space="preserve">{esc(title)}</w:t></w:r></w:p>']
    for h, rows in secs:
        if h:
            body.append(f'<w:p><w:r><w:rPr><w:b/><w:sz w:val="30"/></w:rPr>'
                        f'<w:t xml:space="preserve">{esc(h)}</w:t></w:r></w:p>')
        for r in rows:
            if not r:
                continue
            txt = r[2:] if r.startswith("- ") else r
            bullet = "· " if r.startswith("- ") else ""
            body.append(f'<w:p><w:r><w:t xml:space="preserve">{esc(bullet + txt)}</w:t>'
                        f'</w:r></w:p>')
    doc_xml = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
               '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
               '<w:body>' + "".join(body) + '</w:body></w:document>')
    ct = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
          '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
          '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
          '<Default Extension="xml" ContentType="application/xml"/>'
          '<Override PartName="/word/document.xml" ContentType='
          '"application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
          '</Types>')
    rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/'
            '2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
    _safe_dest(path)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", ct)
        z.writestr("_rels/.rels", rels)
        z.writestr("word/document.xml", doc_xml)
    return path


def _ppt_text(slide, text, left, top, width, size=18, bold=False, color=None):
    """在幻灯片上加一个文本框（自动换行）。"""
    from pptx.util import Pt, Inches
    from pptx.dml.color import RGBColor
    tb = slide.shapes.add_textbox(left, top, width, Inches(1.2))
    tf = tb.text_frame
    tf.word_wrap = True
    p = tf.paragraphs[0]
    p.text = text
    p.font.size = Pt(size)
    p.font.bold = bold
    if color is not None:
        p.font.color.rgb = RGBColor(*color)
    return tb


def _ppt_bullets(slide, bullets, left, top, width, size=18):
    """在幻灯片左侧/整幅加要点列表。"""
    from pptx.util import Pt, Inches
    box = slide.shapes.add_textbox(left, top, width, Inches(5.2))
    tf = box.text_frame
    tf.word_wrap = True
    first = True
    for b in bullets:
        p = tf.paragraphs[0] if first else tf.add_paragraph()
        first = False
        p.text = "• " + b
        p.font.size = Pt(size)
        p.space_after = Pt(8)
    return box


def make_pptx(md: str, path: str = "", imgs: dict = None) -> str:
    """Markdown(标题 + ##每页 + -要点) → PPT。

    - 16:9 宽屏，封面 + 每节一页；
    - 每页可选插入配图（imgs={节标题: 图片路径}，由调用方生成）；
    - 有图时左文右图，无图时整幅要点；带页脚与统一配色。
    """
    from pptx import Presentation
    from pptx.util import Inches
    title, secs = _md_sections(md)
    path = _auto_path(path, title, ".pptx")
    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)
    BLUE = (0x2B, 0x5C, 0x9E)
    GREY = (0x59, 0x59, 0x59)
    # 封面
    cover = prs.slides.add_slide(prs.slide_layouts[6])
    _ppt_text(cover, title, Inches(1), Inches(2.6), Inches(11.3), size=40, bold=True, color=BLUE)
    if secs and secs[0][1]:
        _ppt_text(cover, secs[0][1][0][:120], Inches(1), Inches(3.6),
                  Inches(11.3), size=18, color=GREY)
    # 内容页
    for h, rows in secs:
        s = prs.slides.add_slide(prs.slide_layouts[6])
        _ppt_text(s, h or "要点", Inches(0.6), Inches(0.35), Inches(12.1),
                  size=28, bold=True, color=BLUE)
        bullets = [r[2:] if r.startswith("- ") else r for r in rows if r]
        img = (imgs or {}).get(h)
        if img and os.path.isfile(img):
            try:
                s.shapes.add_picture(img, Inches(7.0), Inches(1.3), height=Inches(4.6))
                _ppt_bullets(s, bullets[:8], Inches(0.6), Inches(1.3), Inches(6.0))
            except Exception:
                _ppt_bullets(s, bullets[:12], Inches(0.8), Inches(1.3), Inches(11.7))
        else:
            _ppt_bullets(s, bullets[:12], Inches(0.8), Inches(1.3), Inches(11.7))
        _ppt_text(s, title, Inches(0.6), Inches(7.0), Inches(12), size=10, color=GREY)
    _safe_dest(path)
    prs.save(path)
    return path


def make_xlsx(md: str, path: str = "") -> str:
    """Markdown 表格(| a | b |) → Excel；没有表格就把各节转成两列（项目/内容）。"""
    title, secs = _md_sections(md)
    path = _auto_path(path, title, ".xlsx")
    rows: List[list] = []
    for ln in md.splitlines():
        s = ln.strip()
        if s.startswith("|") and s.endswith("|") and "---" not in s:
            cells = [c.strip() for c in s.strip("|").split("|")]
            if any(cells):
                rows.append(cells)
    if not rows:
        rows = [["项目", "内容"]]
        for h, rs in secs:
            rows.append([h or "概述", "；".join(x.lstrip("- ") for x in rs if x)[:200]])
    try:
        from openpyxl import Workbook
        wb = Workbook()
        ws = wb.active
        ws.title = (title or "Sheet1")[:28]
        for r in rows[:500]:
            ws.append(r)
        # 简单美化：表头加粗（尽力而为）
        try:
            from openpyxl.styles import Font
            for c in ws[1]:
                c.font = Font(bold=True)
        except Exception:
            pass
        _safe_dest(path)
        wb.save(path)
        return path
    except ImportError:
        csvp = os.path.splitext(path)[0] + ".csv"
        import csv as _csv
        with open(csvp, "w", newline="", encoding="utf-8-sig") as f:
            _csv.writer(f).writerows(rows[:500])
        return csvp


def _register_project(name: str, pdir: str, files: list):
    try:
        pf = os.path.join(DATA_DIR, "projects.json")
        data = []
        if os.path.exists(pf):
            data = json.load(open(pf, "r", encoding="utf-8"))
        data = [d for d in data if d.get("name") != name]
        data.insert(0, {"name": name, "dir": pdir, "files": files[:12],
                        "t": time.strftime("%m-%d %H:%M")})
        json.dump(data[:20], open(pf, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
    except Exception:
        pass


@_with_ledger("run_project")
def run_project(pdir: str, timeout: int = 45) -> str:
    """尝试运行项目入口：python/node 入口直接跑；index.html 浏览器打开。"""
    entries = []
    for root, dirs, fs in os.walk(pdir):
        dirs[:] = [d for d in dirs if d not in ("node_modules", ".git", "__pycache__")]
        for f in fs:
            low = f.lower()
            if low in ("main.py", "app.py", "server.py", "run.py", "index.js",
                       "server.js", "app.js"):
                entries.append(os.path.join(root, f))
    if not entries:
        htmls = [f for f in os.listdir(pdir) if f.lower().endswith(".html")] \
            if os.path.isdir(pdir) else []
        if htmls:
            page = os.path.join(pdir, htmls[0])
            try:
                platform_ops.startfile(page)
                return "🌐 前端页面已用浏览器打开：" + page
            except Exception:
                return "项目已生成，入口页面：" + page
        return "项目文件已生成（没找到可执行入口，告诉我怎么跑或让我补启动脚本）。"
    e = entries[0]
    ext = os.path.splitext(e)[1].lower()
    cwd = os.path.dirname(e)
    try:
        if ext == ".py":
            py = os.environ.get("PASM_PY", "python")
            # 常见 Web 框架项目：只提示如何启动，不在后台挂死
            head = open(e, "r", encoding="utf-8", errors="ignore").read(2000)
            if any(k in head for k in ("uvicorn", "Flask(__name__)", "FastAPI(",
                                       "app.run(", "streamlit")):
                return ("这是 Web 服务项目，启动命令：\n```\ncd " + pdir +
                        "\n" + py + " " + e + "\n```\n（需要依赖时先 `pip install -r requirements.txt`）")
            r = _sp([py, e], capture_output=True, text=True,
                               timeout=timeout, cwd=cwd,
                               encoding="utf-8", errors="replace")
        elif ext == ".js":
            node = which("node")
            if not node:
                return runtime_hint(".js")
            r = _sp([node, e], capture_output=True, text=True,
                               timeout=timeout, cwd=cwd,
                               encoding="utf-8", errors="replace")
        else:
            return "项目已生成，入口：" + e
        out = (r.stdout or "") + ("\n[错误]\n" + r.stderr if r.stderr else "")
        return ("运行入口 " + os.path.basename(e) + "：\n") + (out[:2500] or "（无输出）")
    except subprocess.TimeoutExpired:
        return "入口已运行但长时间无输出（服务型项目正常现象），项目目录：" + pdir
    except Exception as ex:
        return "运行失败：" + str(ex)
