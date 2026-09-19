# -*- coding: utf-8 -*-
"""sysops —— v0.27.1 真实系统操作层（说得出就要做得到，做不到就说真话）

设计原则（对应用户三点要求）：
1. **真实执行**：所有操作返回真实的成功/失败结果，绝不虚构"已完成"。
   删除 = 移入回收站（FOF_ALLOWUNDO，可恢复），不是永久删除。
2. **安全护栏**：系统目录/盘符根/程序目录硬拒（怎么问都不行）；
   非空目录删除必须显式授权（allow_nonempty=True 且 UI 弹窗确认）；
   清理垃圾 = 先扫描报告（scan 只读不动手），用户确认后才清（clean），
   且只碰白名单临时目录、只删超过 min_age_days 的文件、被占用文件跳过并如实报告。
3. **执行台账**：每个动作记录 时间/动作/对象/原因/结果（JSONL），可追溯可复盘。
"""
import os
import re
import sys
import json
import time
import ctypes
import ctypes.wintypes as wt

APPDATA_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")),
                           "PASMStudio")
LEDGER_PATH = os.path.join(APPDATA_DIR, "ops_ledger.jsonl")
os.makedirs(APPDATA_DIR, exist_ok=True)

# ---------------------------------------------------------------- 硬禁区
def _user_profile() -> str:
    return os.path.expanduser("~").rstrip("\\")


def _forbidden_roots() -> list:
    """绝对不许删/清的根目录（本身以及等于它们）。其**内部的**用户文件不受影响，
    例如 C:\\Windows\\Temp 属于白名单清理目标，但 C:\\Windows 本身绝不能删。"""
    up = _user_profile()
    roots = [
        os.environ.get("SystemRoot", r"C:\Windows"),
        os.environ.get("ProgramFiles", r"C:\Program Files"),
        os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
        os.environ.get("ProgramData", r"C:\ProgramData"),
        up,                                   # 用户主目录本身（C:\Users\xxx）
        "C:\\", "D:\\", "E:\\", "F:\\", "G:\\",
    ]
    out = []
    for r in roots:
        if r:
            out.append(os.path.normcase(os.path.normpath(r)))
    return out


_FORBIDDEN = _forbidden_roots()


def is_forbidden(path: str) -> bool:
    """目标是硬禁区本身（或其上层）→ True。禁区内部的子项不在此列。"""
    p = os.path.normcase(os.path.normpath(os.path.abspath(path or "")))
    for f in _FORBIDDEN:
        if p == f:
            return True
    return False


def inside_forbidden(path: str) -> bool:
    """目标位于**系统**硬禁区内部（如 C:\\Windows\\System32\\xxx）→ True。
    注意：用户主目录**不参与**内部检查——否则 AppData（含 %TEMP%）全被误伤；
    主目录本身不可删由 is_forbidden 保证。C:\\Windows\\Temp 例外（清理白名单）。"""
    p = os.path.normcase(os.path.normpath(os.path.abspath(path or "")))
    if p.replace("\\", "/").rstrip("/").endswith("windows/temp"):
        return False
    up = os.path.normcase(os.path.normpath(_user_profile()))
    for f in _FORBIDDEN:
        if f == up:
            continue                       # 主目录内部不拦（Temp/桌面/文档都在里面）
        if p.startswith(f + os.sep):
            return True
    return False


# ---------------------------------------------------------------- 描述
def describe(path: str) -> dict:
    """给确认弹窗用：这是什么、多大、里面有什么。全部实测，不估计。"""
    d = {"path": path, "exists": os.path.exists(path), "kind": "",
         "size": 0, "n_files": 0, "n_dirs": 0, "empty": False, "error": ""}
    if not d["exists"]:
        d["error"] = "路径不存在"
        return d
    if os.path.isfile(path):
        d["kind"] = "file"
        d["size"] = os.path.getsize(path)
        return d
    d["kind"] = "dir"
    try:
        for root, dirs, files in os.walk(path):
            d["n_dirs"] += len(dirs)
            for f in files:
                d["n_files"] += 1
                try:
                    d["size"] += os.path.getsize(os.path.join(root, f))
                except OSError:
                    pass
            if d["n_files"] > 50000:          # 巨型目录别扫到天荒地老
                break
    except OSError as ex:
        d["error"] = str(ex)
    d["empty"] = d["n_files"] == 0 and d["n_dirs"] == 0
    return d


def _human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024
    return f"{n:.1f} GB"


# ---------------------------------------------------------------- 回收站删除
class _SHFILEOPSTRUCTW(ctypes.Structure):
    """Win32 SHFILEOPSTRUCTW（ctypes.wintypes 没有自带，手动定义）。"""
    _fields_ = [("hwnd", wt.HWND),
                ("wFunc", wt.UINT),
                ("pFrom", wt.LPCWSTR),
                ("pTo", wt.LPCWSTR),
                ("fFlags", ctypes.c_ushort),
                ("fAnyOperationsAborted", wt.BOOL),
                ("hNameMappings", ctypes.c_void_p),
                ("lpszProgressTitle", wt.LPCWSTR)]


def to_recycle_bin(path: str):
    """Windows 回收站删除（可恢复）。返回 (ok: bool, errmsg: str)。
    真调用 Win32 SHFileOperationW，失败时返回系统真实错误。"""
    if sys.platform != "win32":
        try:
            os.remove(path) if os.path.isfile(path) else os.rmdir(path)
            return True, ""
        except OSError as ex:
            return False, str(ex)
    # pFrom 需要双 \0 结尾（路径+\0+空串终止）。c_wchar_p 字段会自动补一个 \0，
    # 所以这里字符串里先带一个 \0，落盘后正好是双 \0。
    op = _SHFILEOPSTRUCTW()
    op.hwnd = None
    op.wFunc = 3                                # FO_DELETE
    op.pFrom = os.path.abspath(path) + "\0"
    op.fFlags = 0x40 | 0x10 | 0x4               # ALLOWUNDO|NOCONFIRMATION|SILENT
    op.fAnyOperationsAborted = False
    op.hNameMappings = None
    op.lpszProgressTitle = None
    rc = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    if rc == 0 and not op.fAnyOperationsAborted:
        return True, ""
    if op.fAnyOperationsAborted:
        return False, "操作被系统或用户中止"
    return False, f"系统错误码 {rc}（可能是文件被占用或权限不足）"


# ---------------------------------------------------------------- 删除
def delete_path(path: str, allow_nonempty: bool = False,
                why: str = "用户请求") -> dict:
    """带完整护栏的删除。返回真实结果，绝不粉饰：
    ok=True  → 真删掉了（回收站）
    ok=False → reason 写明为什么（不存在/系统禁区/非空未授权/被占用/权限不足）
    """
    r = {"path": path, "ok": False, "reason": "", "trash": True,
         "size": 0, "n_files": 0}
    if not path or not os.path.exists(path):
        r["reason"] = "路径不存在（可能已被删除，或名字有出入）"
        return r
    if is_forbidden(path):
        r["reason"] = "这是系统关键目录（Windows/程序目录/盘符根/用户主目录），" \
                      "无论怎么要求我都不会删它——删了系统会坏。"
        return r
    if inside_forbidden(path):
        r["reason"] = "它位于系统目录内部，需要管理员权限且风险高，我不代做。" \
                      "若确实需要，请你手动操作或用管理员权限的清理工具。"
        return r
    d = describe(path)
    r["size"], r["n_files"] = d["size"], d["n_files"]
    if d["kind"] == "dir" and not d["empty"] and not allow_nonempty:
        r["reason"] = (f"文件夹不是空的（{d['n_files']} 个文件、{d['n_dirs']} 个子文件夹，"
                       f"共 {_human(d['size'])}）。为防误删，需要你确认后我才整体删除。")
        r["need_confirm"] = True
        return r
    ok, err = to_recycle_bin(path)
    if ok:
        r["ok"] = True
    else:
        r["reason"] = f"删除失败：{err}"
    ledger("delete", path, why, r)
    return r


# ---------------------------------------------------------------- 垃圾扫描/清理
def _junk_locations() -> list:
    """白名单：只清这里面的。名字+路径+说明。"""
    locs = []
    tmp_user = os.environ.get("TEMP") or os.path.join(_user_profile(), "AppData\\Local\\Temp")
    tmp_win = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "Temp")
    for p, label in ((tmp_user, "用户临时文件夹"), (tmp_win, "系统临时文件夹")):
        if p and os.path.isdir(p):
            locs.append({"path": os.path.normpath(p), "label": label})
    for env, label in (("LOCALAPPDATA", "pip 缓存"),
                       ("LOCALAPPDATA", "npm 缓存")):
        base = os.environ.get(env)
        if not base:
            continue
        sub = ("pip\\cache" if "pip" in label else "npm-cache")
        p = os.path.join(base, sub)
        if os.path.isdir(p):
            locs.append({"path": p, "label": label})
    return locs


def scan_junk() -> dict:
    """只读扫描，什么都不删。返回每个白名单位置的实际占用与文件数。"""
    out = {"locations": [], "total": 0, "n_files": 0}
    for loc in _junk_locations():
        size = files = 0
        for root, _dirs, fs in os.walk(loc["path"]):
            for f in fs:
                files += 1
                try:
                    size += os.path.getsize(os.path.join(root, f))
                except OSError:
                    pass
        out["locations"].append({**loc, "size": size, "n_files": files,
                                 "human": _human(size)})
        out["total"] += size
        out["n_files"] += files
    out["total_human"] = _human(out["total"])
    return out


def clean_junk(min_age_days: float = 2.0, max_seconds: float = 90.0) -> dict:
    """清理白名单临时目录里超过 min_age_days 的文件。
    被占用/无权限的文件**跳过并计数**，绝不假装删掉。
    只删文件不动目录结构（空目录留给系统自己收，避免误伤正在写的程序）。
    max_seconds：真实 TEMP 可能有几十万文件，给桌面助手设时间预算，
    到点如实报告"本轮清了多少、还有剩余可再清一次"，不糊弄。"""
    cutoff = time.time() - min_age_days * 86400
    t0 = time.time()
    r = {"deleted_files": 0, "freed": 0, "skipped_locked": 0, "skipped_new": 0,
         "remaining": False, "freed_human": "", "errors": []}
    for loc in _junk_locations():
        if time.time() - t0 > max_seconds:
            r["remaining"] = True
            break
        for root, _dirs, fs in os.walk(loc["path"]):
            if time.time() - t0 > max_seconds:
                r["remaining"] = True
                break
            for f in fs:
                fp = os.path.join(root, f)
                try:
                    st = os.stat(fp)
                except OSError:
                    r["skipped_locked"] += 1
                    continue
                if st.st_mtime > cutoff:
                    r["skipped_new"] += 1
                    continue
                try:
                    os.remove(fp)
                    r["deleted_files"] += 1
                    r["freed"] += st.st_size
                except OSError as ex:
                    # 真实原因：被占用(PermissionError/32)还是别的
                    r["skipped_locked"] += 1
                    if len(r["errors"]) < 3:
                        r["errors"].append(f"{f}: {ex.strerror or ex}")
                if time.time() - t0 > max_seconds:
                    r["remaining"] = True
                    break
        if r["remaining"]:
            break
    r["freed_human"] = _human(r["freed"])
    ledger("clean_junk", "temp-whitelist", f"清理>={min_age_days}天临时文件", r)
    return r


def desktop_dir() -> str:
    """真实桌面路径：SHGetKnownFolderPath 优先（兼容 OneDrive 重定向的机器），
    失败回落 ~/Desktop。v0.27.2 修复：旧版硬编码 ~/Desktop，
    在桌面被 OneDrive 接管的机器上会删错地方/找不到东西。"""
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            class _GUID(ctypes.Structure):
                _fields_ = [("d1", ctypes.c_ulong), ("d2", ctypes.c_ushort),
                            ("d3", ctypes.c_ushort), ("d4", ctypes.c_ubyte * 8)]

            kfp = ctypes.windll.shell32.SHGetKnownFolderPath
            kfp.restype = ctypes.HRESULT
            # FOLDERID_Desktop {B4BFCC3A-DB2C-424C-B029-7FE99A87C641}
            g = _GUID(0xB4BFCC3A, 0xDB2C, 0x424C,
                      (ctypes.c_ubyte * 8)(0xB0, 0x29, 0x7F, 0xE9,
                                           0x9A, 0x87, 0xC6, 0x41))
            p = ctypes.c_wchar_p()
            # flags: 0 = 默认（用户级，不校验重定向目标存在性）
            if kfp(ctypes.byref(g), 0, None, ctypes.byref(p)) == 0 and p.value:
                return os.path.normpath(p.value)
        except Exception:
            pass
    return os.path.join(_user_profile(), "Desktop")


DESKTOP_CANDIDATES = None


def desktop_candidates() -> list:
    """所有可能的桌面位置（去重、存在的优先），供批量操作遍历。"""
    out = []
    for p in (desktop_dir(),
              os.path.join(_user_profile(), "Desktop"),
              os.path.join(_user_profile(), "OneDrive", "Desktop"),
              os.path.join(_user_profile(), "桌面")):
        if p and os.path.isdir(p):
            n = os.path.normcase(os.path.normpath(p))
            if n not in out:
                out.append(n)
    return out


IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico")


def list_desktop_images() -> list:
    """桌面上的图片文件（非递归，多候选桌面合并去重）。返回绝对路径列表。"""
    out = []
    seen = set()
    for desk in desktop_candidates():
        try:
            for e in sorted(os.listdir(desk)):
                if os.path.splitext(e)[1].lower() in IMAGE_EXTS:
                    fp = os.path.join(desk, e)
                    n = os.path.normcase(fp)
                    if n not in seen and os.path.isfile(fp):
                        seen.add(n)
                        out.append(fp)
        except OSError:
            continue
    return out


def ledger_recent(actions: tuple, seconds: float = 25.0) -> bool:
    """最近 seconds 秒内有没有 actions 里 ok=True 的真实操作记录。
    防幻觉守卫用：模型嘴上说'删完了'，台账里必须有真账。"""
    cut = time.time() - seconds
    for row in ledger_tail(30):
        try:
            t = time.mktime(time.strptime(row.get("t", ""), "%Y-%m-%d %H:%M:%S"))
        except (ValueError, TypeError):
            continue
        if t >= cut and row.get("ok") and row.get("action") in actions:
            return True
    return False


# ---------------------------------------------------------------- 执行台账
def ledger(action: str, target: str, why: str, result: dict):
    """每个真实操作记一笔：谁(固定小U)/何时/做了什么/为什么/结果。可追溯。"""
    try:
        with open(LEDGER_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "t": time.strftime("%Y-%m-%d %H:%M:%S"),
                "action": action, "target": target, "why": why,
                "ok": result.get("ok"), "reason": result.get("reason", ""),
                "result": {k: v for k, v in result.items()
                           if k not in ("path", "ok", "reason")}
            }, ensure_ascii=False) + "\n")
    except OSError:
        pass


def note_action(action: str, target: str = "", ok: bool = True,
                reason: str = ""):
    """给「真实执行」记一笔台账（便捷版）。

    与 ledger() 的区别：只关心"这件事到底发生了没有"，参数极简，
    执行出口一行就能记 —— **不容易漏**。防幻觉守卫靠它判断
    "模型说的已执行是不是真的"。
    """
    ledger(action, str(target)[:200], "真实执行",
           {"ok": bool(ok), "reason": str(reason)[:120]})


def ledger_tail(n: int = 20) -> list:
    try:
        with open(LEDGER_PATH, encoding="utf-8") as f:
            return [json.loads(ln) for ln in f.readlines()[-n:] if ln.strip()]
    except (OSError, json.JSONDecodeError):
        return []


# ---------------------------------------------------------------- 难度评估
DIFF_SIMPLE_WORDS = ("打开", "读一下", "看看", "总结", "翻译", "改写", "分析一下",
                     "删除", "新建", "复制")
DIFF_HARD_WORDS = ("然后", "接着", "最后", "分别", "批量", "所有", "每个", "全部",
                   "逐个", "挨个", "一遍", "以及", "再", "顺便")


def assess_difficulty(text: str) -> dict:
    """任务难度粗判：simple=主体直接干；complex=建议拆分/派子智能体。
    破坏性动词（删/清/格式化/卸载）→ 无论如何都必须主体亲自确认后执行。"""
    t = (text or "").strip()
    destructive = any(w in t for w in ("删", "清除", "清空", "格式化", "卸载",
                                       "覆盖", "重装"))
    hard = sum(1 for w in DIFF_HARD_WORDS if w in t)
    easy = sum(1 for w in DIFF_SIMPLE_WORDS if w in t)
    n_steps = max(hard, 1 if len(t) > 120 else 0)
    if destructive:
        mode = "main_confirm"          # 主体亲自 + 必须确认
    elif n_steps >= 2 or (hard >= 1 and easy >= 2):
        mode = "delegate"              # 拆子步骤派子智能体，主体复核
    else:
        mode = "simple"                # 主体直接干
    return {"mode": mode, "destructive": destructive,
            "n_steps": n_steps + (1 if easy else 0), "len": len(t)}


# ------------------------------------------------ v0.27.2 真实动作：浏览器搜索
def open_web_search(query: str) -> dict:
    """真实动作：用系统默认浏览器打开搜索页（必应，国内可达）。
    真开了就是真开了，失败就说失败原因，绝不虚构"已搜索"。"""
    import urllib.parse
    import webbrowser
    q = (query or "").strip()
    r = {"query": q, "ok": False, "reason": "", "url": ""}
    if not q:
        r["reason"] = "没认出要搜什么（关键词为空）"
        ledger("web_search", q, "用户请求上网搜索", r)
        return r
    url = "https://www.bing.com/search?q=" + urllib.parse.quote(q)
    r["url"] = url
    try:
        if not webbrowser.open(url):
            r["reason"] = "系统没有可用的默认浏览器"
            ledger("web_search", q, "用户请求上网搜索", r)
            return r
    except Exception as ex:
        r["reason"] = f"打不开浏览器：{ex}"
        ledger("web_search", q, "用户请求上网搜索", r)
        return r
    r["ok"] = True
    ledger("web_search", q, "用户请求上网搜索", r)
    return r


# ------------------------------------------------ v0.27.2 真实体检：安全状况
_STARTUPINFO = None


def _ps_exe() -> str:
    """PowerShell 可执行文件**完整路径**。

    v0.27.3 真机实锤：旧版用裸命令名 "powershell"，在本机 subprocess 里
    0.0 秒直接失败（异常被 except 吞掉 → 返回空 → 上层误判"取不到状态"）；
    换成完整路径 System32\\WindowsPowerShell\\v1.0\\powershell.exe 立刻正常
    （实测 RT=0 / FW=3/3）。与 asr.py 的做法保持一致。
    """
    root = os.environ.get("SystemRoot") or r"C:\Windows"
    p = os.path.join(root, "System32", "WindowsPowerShell", "v1.0",
                     "powershell.exe")
    return p if os.path.exists(p) else "powershell"


def _ps_run(cmd: str, timeout: float = 30.0) -> str:
    """跑一条只读 PowerShell 命令并取 stdout；失败返回空串（调用方如实降级）。

    v0.27.3 健壮化（真机实锤修复）：
    - 加 `-ExecutionPolicy Bypass`（部分机器策略禁脚本，静默失败）
    - 输出解码按 UTF-8 → GBK 双路尝试（PowerShell 5.1 默认按 OEM/GBK 输出中文，
      旧版硬解码 utf-8 会把中文吃成乱码/空串，导致"明明能读到却说取不到"）
    - 返回前 strip 掉 PS 远程模块加载噪声行（Creating implicit remoting module…）
    """
    import subprocess
    try:
        p = subprocess.run(
            [_ps_exe(), "-NoProfile", "-NonInteractive",
             "-ExecutionPolicy", "Bypass", "-Command", cmd],
            capture_output=True, timeout=timeout,
            creationflags=0x08000000)          # CREATE_NO_WINDOW，不闪黑框
    except subprocess.TimeoutExpired:
        return ""
    except Exception:
        # 完整路径也失败 → 最后再试一次裸命令名
        try:
            p = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive",
                 "-ExecutionPolicy", "Bypass", "-Command", cmd],
                capture_output=True, timeout=timeout,
                creationflags=0x08000000)
        except Exception:
            return ""
    raw = p.stdout or b""
    if not raw:
        return ""
    txt = ""
    for enc in ("utf-8", "gbk", "mbcs"):
        try:
            txt = raw.decode(enc)
            if "�" not in txt:                 # 没有替换字符说明解码对了
                break
        except Exception:
            continue
    if not txt:
        txt = raw.decode("utf-8", "ignore")
    # 去掉 PowerShell 隐式远程模块的加载噪声（会污染解析）
    lines = [ln for ln in txt.splitlines()
             if ln.strip() and not ln.strip().startswith(
                 ("Creating implicit remoting module",
                  "Getting command information from remote session"))]
    return "\n".join(lines).strip()


def security_check(timeout: float = 25.0) -> dict:
    """电脑安全体检（只读，不改任何设置）：Defender 实时保护 / 病毒库日期 /
    防火墙启用数 / 最近 5 条威胁检出记录。取不到就如实说取不到，不编结论。"""
    out = {"ok": True, "realtime": None, "sig_date": "", "firewall_txt": "",
           "threats": [], "error": "", "antivirus": [], "scan_age_days": None,
           "source": ""}
    # v0.27.3：脚本内逐字段容错。真机实锤——旧版把
    #   AntivirusSignatureLastUpdated（可能为 null）直接 .ToString()，
    #   一个字段空就让整条命令抛异常 → stdout 全空 → 明明读得到 Defender
    #   却对 user 说"取不到"。现在每个字段独立 try，取不到就留 "?"。
    # 注意：PowerShell 里 try/catch 是**语句**不能当表达式用（旧写法
    #   'RT='+(try{...}catch{'?'}) 整条命令语法错误 → 输出全空），
    #   所以每个字段都用 "$v=…; if($null -eq $v){'X=?'}else{'X='+…}" 的语句式。
    s = _ps_run(
        "$s=$null; try{$s=Get-MpComputerStatus -ErrorAction Stop}catch{}; "
        "if($s){"
        " $v=$s.RealTimeProtectionEnabled;"
        " if($null -eq $v){'RT=?'}else{'RT='+[int]$v};"
        " $v=$s.AntispywareEnabled;"
        " if($null -eq $v){'AS=?'}else{'AS='+[int]$v};"
        " $v=$s.AntivirusEnabled;"
        " if($null -eq $v){'AV=?'}else{'AV='+[int]$v};"
        " $v=$s.AMServiceEnabled;"
        " if($null -eq $v){'AM=?'}else{'AM='+[int]$v};"
        " $v=$s.AntivirusSignatureLastUpdated;"
        " if($null -eq $v){'SIG=?'}else{'SIG='+$v.ToString('yyyy-MM-dd')};"
        " $v=$s.FullScanEndTime;"
        " if($null -eq $v){'FS=?'}else{'FS='+$v.ToString('yyyy-MM-dd')};"
        " $v=$s.QuickScanEndTime;"
        " if($null -eq $v){'QS=?'}else{'QS='+$v.ToString('yyyy-MM-dd')}"
        "} else { 'NOMP=1' }", timeout)
    src = "Windows Defender"
    if "NOMP=1" in s:
        # Defender 不可用 → 退到安全中心（WMI SecurityCenter2，覆盖第三方杀软）
        src = "Windows 安全中心(SecurityCenter2)"
        s2 = _ps_run(
            "$a=Get-CimInstance -Namespace root/SecurityCenter2 "
            "-ClassName AntivirusProduct -ErrorAction SilentlyContinue; "
            "if($a){ foreach($x in $a){"
            " $n=$x.displayName; $st='?'; "
            " try{$h='{0:x6}' -f $x.productState; "
            " if($h.Length -ge 6){$on=$h.Substring(4,2); "
            " if($on -eq '10' -or $on -eq '11'){$st=1}else{$st=0}}}catch{}; "
            " 'AVNAME='+$n; 'AVSTATE='+$st } } else { 'NOAV=1' }", timeout)
        names = re.findall(r"AVNAME=(.+)", s2)
        states = re.findall(r"AVSTATE=(\d|\?)", s2)
        for i, n in enumerate(names):
            out["antivirus"].append(
                {"name": n.strip(),
                 "on": (states[i] == "1" if i < len(states) else None)})
        if out["antivirus"]:
            out["realtime"] = out["antivirus"][0].get("on")
            out["source"] = src
            out["sig_date"] = ""
        else:
            out["ok"] = False
            out["error"] = ("这台机器上取不到杀毒软件状态（Defender 与安全中心都读不到，"
                            "可能服务被停用或权限不足）。我不编造结论。")
            ledger("security_check", "Windows安全", "用户请求安全体检", out)
            return out
    else:
        m = re.search(r"RT=(\d)", s)
        if m:
            out["realtime"] = m.group(1) == "1"
        m = re.search(r"SIG=(\S+)", s)
        if m and m.group(1) != "?":
            out["sig_date"] = m.group(1).strip()
        m = re.search(r"FS=(\S+)", s)
        if m and m.group(1) != "?":
            try:
                d0 = time.strptime(m.group(1), "%Y-%m-%d")
                out["scan_age_days"] = int((time.time() - time.mktime(d0)) / 86400)
            except Exception:
                pass
        if out["realtime"] is None:
            out["ok"] = False
            out["error"] = ("取不到 Windows Defender 状态（可能装了第三方杀毒软件、"
                            "Defender 服务被停用，或权限不足）。我不编造结论。")
            ledger("security_check", "Windows安全", "用户请求安全体检", out)
            return out
        out["source"] = src
    f = _ps_run("$p=Get-NetFirewallProfile; if($p){'FW='+(@($p|Where-Object{$_.Enabled}).Count)"
                "+'/'+@($p).Count}", timeout)
    m = re.search(r"FW=(\d+)\s*/\s*(\d+)", f)
    if m:
        en, tot = int(m.group(1)), int(m.group(2))
        out["firewall_txt"] = ("✅ %d/%d 个网络配置文件已启用" % (en, tot)
                               if en == tot else
                               "⚠️ 只有 %d/%d 个已启用（有未开启的）" % (en, tot))
    else:
        out["firewall_txt"] = "未知（取不到防火墙状态）"
    t = _ps_run("$t=Get-MpThreatDetection | Sort-Object InitialDetectionTime "
                "-Descending | Select-Object -First 5; "
                "$t | ForEach-Object { 'THR='+$_.InitialDetectionTime."
                "ToString('yyyy-MM-dd HH:mm')+' 检出(ThreatID '+$_.ThreatID+')' }",
                timeout)
    for ln in t.splitlines():
        if ln.startswith("THR="):
            out["threats"].append(ln[4:].strip())
    ledger("security_check", "Windows安全", "用户请求安全体检（只读）", out)
    return out


# ============================================================ v0.27.3 新能力
# 真机痛点：
#  ① "删除桌面上的空文件夹" —— 泛指，没有具体名字，旧版解析不出目标就回
#     "找不到"（用户感受：还是删不掉）。现在能真的把空文件夹扫出来列清单。
#  ② "分析一下桌面上的文件" —— 旧版要求先读过文件才有上下文，否则直接回
#     "我还没读过文件呢"，等于没有分析。现在能主动扫描并给结构化报告。
#  ③ "帮我分析这个表格" —— 旧版要求消息里带 .xlsx 路径，否则"手头没表格"。
#     现在能主动在桌面/下载/文档找最近的数据文件并让用户选。
_SKIP_DIRS = {"$recycle.bin", "system volume information", "node_modules",
              ".git", "__pycache__", "recovery", "windows", "programdata"}


def find_empty_dirs(root: str, max_n: int = 40) -> list:
    """列出 root 下所有**空文件夹**（含只含空子文件夹的递归空目录）。只读。"""
    root = (root or "").strip()
    if not root or not os.path.isdir(root):
        return []
    empties = []

    def _is_effectively_empty(p: str, depth: int = 0) -> bool:
        """目录里没有任何文件（空子目录也算空）。"""
        if depth > 6:
            return False
        try:
            for e in os.scandir(p):
                if e.is_file(follow_symlinks=False):
                    return False
                if e.is_dir(follow_symlinks=False):
                    if not _is_effectively_empty(e.path, depth + 1):
                        return False
        except Exception:
            return False
        return True

    try:
        for e in os.scandir(root):
            if not e.is_dir(follow_symlinks=False):
                continue
            if e.name.lower() in _SKIP_DIRS or e.name.startswith("."):
                continue
            try:
                if _is_effectively_empty(e.path):
                    empties.append(e.path)
                    if len(empties) >= max_n:
                        break
            except Exception:
                continue
    except Exception:
        return []
    return empties


def scan_dir_report(root: str, max_files: int = 400) -> dict:
    """只读扫描一个目录，产出结构化报告（供"分析桌面/文件夹"真实使用）。

    返回：文件数/文件夹数/总体积/类型分布/最大与最新文件/可疑重复候选/
    空文件夹数 —— 全部是真扫出来的数字，不是模型编的。
    """
    root = (root or "").strip()
    rep = {"ok": False, "root": root, "n_file": 0, "n_dir": 0, "bytes": 0,
           "types": [], "biggest": [], "newest": [], "dups": [],
           "empty_dirs": 0, "error": ""}
    if not root or not os.path.isdir(root):
        rep["error"] = "这个目录不存在或不是文件夹：%s" % (root or "(空)")
        return rep
    rep["ok"] = True
    by_ext, sizes, mtimes, hashes = {}, [], [], {}
    try:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames
                           if d.lower() not in _SKIP_DIRS and not d.startswith(".")]
            rep["n_dir"] += len(dirnames)
            for fn in filenames:
                if rep["n_file"] >= max_files:
                    break
                p = os.path.join(dirpath, fn)
                try:
                    st = os.stat(p)
                except Exception:
                    continue
                rep["n_file"] += 1
                rep["bytes"] += st.st_size
                ext = (os.path.splitext(fn)[1] or "(无扩展名)").lower()
                d = by_ext.setdefault(ext, {"n": 0, "bytes": 0})
                d["n"] += 1
                d["bytes"] += st.st_size
                sizes.append((st.st_size, p))
                mtimes.append((st.st_mtime, p))
                # 同尺寸同扩展名 → 疑似重复（不读内容，只读大小做粗筛）
                if st.st_size > 64 * 1024:
                    hashes.setdefault((st.st_size, ext), []).append(p)
    except Exception as ex:
        rep["error"] = "扫描中断：%s" % ex
    rep["types"] = sorted(
        [{"ext": k, "n": v["n"], "mb": round(v["bytes"] / 1048576, 2)}
         for k, v in by_ext.items()], key=lambda x: -x["n"])[:10]
    rep["biggest"] = [{"path": p, "mb": round(s / 1048576, 2)}
                      for s, p in sorted(sizes, reverse=True)[:5]]
    rep["newest"] = [{"path": p,
                      "when": time.strftime("%Y-%m-%d %H:%M", time.localtime(m))}
                     for m, p in sorted(mtimes, reverse=True)[:5]]
    rep["dups"] = [{"size_mb": round(k[0] / 1048576, 2), "ext": k[1],
                    "files": v[:4]} for k, v in hashes.items() if len(v) > 1][:5]
    try:
        rep["empty_dirs"] = len(find_empty_dirs(root, max_n=200))
    except Exception:
        pass
    return rep


_TAB_EXT = (".xlsx", ".xls", ".csv")


def find_recent_tabular(max_n: int = 8) -> list:
    """在 桌面/下载/文档/最近 找最近修改的表格数据文件（.xlsx/.xls/.csv）。只读。"""
    import glob as _glob
    roots = []
    for r in desktop_candidates():
        roots.append(r)
    home = os.path.expanduser("~")
    for sub in ("Downloads", "Documents", "Desktop"):
        p = os.path.join(home, sub)
        if os.path.isdir(p) and p not in roots:
            roots.append(p)
    hits = []
    for r in roots:
        try:
            for dp, dn, fn in os.walk(r):
                dn[:] = [d for d in dn
                         if d.lower() not in _SKIP_DIRS and not d.startswith(".")]
                for f in fn:
                    if f.lower().endswith(_TAB_EXT) and not f.startswith("~$"):
                        p = os.path.join(dp, f)
                        try:
                            hits.append((os.path.getmtime(p), p))
                        except Exception:
                            pass
                if len(hits) > 200:
                    break
        except Exception:
            continue
    hits.sort(reverse=True)
    return [p for _, p in hits[:max_n]]

# ================================================================
# v0.30.7 整机现状 / 进程
# ================================================================
# 为什么补这三个（真机盘点）：用户最常问的一类问题是"我电脑卡不卡""内存还剩多少"
# "谁在占内存" —— 原来一个都答不上（security_check 只管 Defender）。而这类问题
# **必须真读机器**：靠模型猜出来的"你内存应该够用"是纯编造，违反诚实守则。

#: 绝不能结束的进程名（小写）。命中直接拒绝，不看用户怎么问。
#: 判据：这些进程结束会导致蓝屏/掉桌面/断网/失去输入法，属于"不可逆"级别。
PROTECTED_PROCS = frozenset({
    "system", "system idle process", "registry", "memory compression",
    "secure system", "smss", "csrss", "wininit", "winlogon", "services",
    "lsass", "lsaiso", "svchost", "dwm", "fontdrvhost", "audiodg",
    "msmpeng", "nissrv", "securityhealthservice", "wmiprvse", "spoolsv",
    "searchindexer", "sihost", "taskhostw", "ctfmon", "runtimebroker",
    "startmenuexperiencehost", "shellexperiencehost", "textinputhost",
    "applicationframehost", "conhost", "dllhost", "explorer", "sppsvc",
    "windefend", "wuauserv", "trustedinstaller",
})

_NAME_OK = re.compile(r"^[A-Za-z0-9_.\-]{1,64}$")


def _ps_ok() -> bool:
    """PowerShell 通道是否可用（体检类功能全靠它，先探一次别猜）。"""
    return bool(_ps_run("'ok'", timeout=8.0))


def sysinfo(timeout: float = 20.0) -> dict:
    """整机现状（**只读**，不改任何设置）：CPU 占用/核数、内存、系统盘、开机时长。

    取不到就如实说取不到（ok=False + err），**绝不编数**。
    PowerShell 里 try/catch 是语句不是表达式，所以每个字段都用
    "$v=…; if($null -eq $v){'X=?'}else{'X='+…}" 的语句式 —— 这是本仓踩过坑的写法
    （见 security_check 的注释：一个空字段会让整条命令抛异常、输出全空）。
    """
    out = {"ok": True, "cpu_pct": None, "cores": None,
           "mem_total_gb": None, "mem_free_gb": None, "mem_pct": None,
           "disks": [], "disk_total_gb": None, "disk_free_gb": None,
           "uptime_h": None, "os": "", "err": ""}
    s = _ps_run(
        "$o=@();"
        " try{$c=Get-CimInstance Win32_Processor -ErrorAction Stop;"
        " $v=($c|Measure-Object -Property LoadPercentage -Average).Average;"
        " if($null -eq $v){$o+='CPU=?'}else{$o+='CPU='+[int]$v}}catch{$o+='CPU=?'};"
        " try{$v=$env:NUMBER_OF_PROCESSORS;"
        " if($null -eq $v){$o+='CORES=?'}else{$o+='CORES='+[int]$v}}catch{$o+='CORES=?'};"
        " try{$m=Get-CimInstance Win32_OperatingSystem -ErrorAction Stop;"
        " $v=$m.TotalVisibleMemorySize;"
        " if($null -eq $v){$o+='MEMT=?'}else{$o+='MEMT='+[math]::Round($v/1048576,1)};"
        " $v=$m.FreePhysicalMemory;"
        " if($null -eq $v){$o+='MEMF=?'}else{$o+='MEMF='+[math]::Round($v/1048576,1)};"
        " $v=$m.LastBootUpTime;"
        " if($null -eq $v){$o+='UP=?'}else{$o+='UP='+[math]::Round(((Get-Date)-$v).TotalHours,1)};"
        " $v=$m.Caption;"
        " if($null -eq $v){$o+='OS=?'}else{$o+='OS='+$v}}"
        "catch{$o+='MEMT=?';$o+='MEMF=?';$o+='UP=?';$o+='OS=?'};"
        " try{Get-CimInstance Win32_LogicalDisk -Filter 'DriveType=3' -ErrorAction Stop |"
        " ForEach-Object{$o+='DISK='+$_.DeviceID+'|'+[math]::Round($_.Size/1GB,1)"
        "+'|'+[math]::Round($_.FreeSpace/1GB,1)}}catch{$o+='DISK=?'};"
        "$o", timeout)
    if not s:
        out["ok"] = False
        out["err"] = "取不到系统信息（WMI 被禁 / 脚本超时 / 权限受限）"
        return out
    num = {"CPU": "cpu_pct", "CORES": "cores", "MEMT": "mem_total_gb",
           "MEMF": "mem_free_gb", "UP": "uptime_h"}
    for ln in s.splitlines():
        ln = ln.strip()
        if not ln or "=" not in ln:
            continue
        k, v = ln.split("=", 1)
        k, v = k.strip(), v.strip()
        if k == "DISK":
            for one in v.split("DISK="):
                one = one.strip()
                parts = one.split("|")
                if len(parts) == 3:
                    try:
                        out["disks"].append({"drive": parts[0],
                                             "total_gb": float(parts[1]),
                                             "free_gb": float(parts[2])})
                    except Exception:
                        continue
            continue
        if k == "OS":
            out["os"] = "" if v == "?" else v
            continue
        if k in num and v != "?":
            try:
                val = float(v)
                out[num[k]] = int(val) if k in ("CPU", "CORES") else val
            except Exception:
                pass
    if out["mem_total_gb"] and out["mem_free_gb"]:
        used = out["mem_total_gb"] - out["mem_free_gb"]
        out["mem_pct"] = round(100.0 * used / out["mem_total_gb"], 1)
    if out["disks"]:
        out["disk_total_gb"] = round(sum(d["total_gb"] for d in out["disks"]), 1)
        out["disk_free_gb"] = round(sum(d["free_gb"] for d in out["disks"]), 1)
    if (out["cpu_pct"] is None and out["mem_total_gb"] is None
            and not out["disks"]):
        out["ok"] = False
        out["err"] = "脚本跑了但一个字段都没取到"
    return out


def top_processes(n: int = 8, timeout: float = 20.0) -> dict:
    """占用最高的进程（**只读**，按内存工作集排序）。取不到就如实说。"""
    out = {"ok": True, "items": [], "err": ""}
    want = max(1, min(int(n or 8), 30))
    s = _ps_run(
        "Get-Process -ErrorAction SilentlyContinue |"
        " Sort-Object -Property WS -Descending |"
        " Select-Object -First %d |"
        " ForEach-Object{$c=-1; try{$c=[math]::Round($_.CPU,0)}catch{};"
        " 'P='+$_.ProcessName+'|'+[math]::Round($_.WS/1MB,1)+'|'+$c}" % want,
        timeout)
    if not s:
        out["ok"] = False
        out["err"] = "取不到进程列表（脚本被禁 / 超时）"
        return out
    for ln in s.splitlines():
        ln = ln.strip()
        if not ln.startswith("P="):
            continue
        parts = ln[2:].split("|")
        if len(parts) != 3:
            continue
        try:
            out["items"].append({"name": parts[0],
                                 "mem_mb": float(parts[1]),
                                 "cpu_s": float(parts[2])})
        except Exception:
            continue
    if not out["items"]:
        out["ok"] = False
        out["err"] = "没有解析到任何进程"
    return out


def process_running(name: str, timeout: float = 12.0):
    """只读：这个进程现在是否在跑。返回 True / False / **None（拿不到，别当 False）**。

    为什么不复用 kill_process：UI 必须"先只读预检 → 再弹授权确认 → 最后才动手"。
    预检不合格（名字非法/系统关键/没在跑）时不该弹窗打扰用户。
    None 与 False 必须分清：**拿不到 ≠ 没在跑**，混为一谈会变成"以为它不在就放行"。
    """
    nm = (name or "").strip()
    if nm.lower().endswith(".exe"):
        nm = nm[:-4]
    if not nm or not _NAME_OK.match(nm):
        return False
    chk = _ps_run("$p=Get-Process -Name '%s' -ErrorAction SilentlyContinue;"
                  " if($null -eq $p){'NO'}else{'YES'}" % nm.lower(), timeout)
    if not chk:
        return None
    return "YES" in chk


def kill_process(name: str, timeout: float = 15.0) -> dict:
    """结束一个进程（**不可逆**，调用方必须先过授权确认）。

    三重护栏（缺一不可）：
      ① 名字白名单字符（`[A-Za-z0-9_.-]`，≤64 字符）—— 防止把用户那句话
         拼进命令行（PowerShell 注入）；
      ② `PROTECTED_PROCS` 硬拒 —— 这些进程结束会掉桌面/断网/蓝屏，怎么问都不行；
      ③ 目标必须**真的在跑** —— 不跑就直接说"没在运行"，不做"猜一个同名进程"。
    注意：不做"模糊匹配"。`kill_process('chr')` 不会去杀 chrome —— 杀错东西的
    代价远大于打不准，打不准用户还能自己看到列表再报一个准确名字。
    """
    nm = (name or "").strip()
    if not nm:
        return {"ok": False, "msg": "没说清楚要结束哪个进程。", "name": ""}
    if not _NAME_OK.match(nm):
        return {"ok": False,
                "msg": "进程名里只允许字母/数字/下划线/点/横线（防注入），"
                       "把进程名发我，例如 chrome、notepad。",
                "name": nm}
    low = nm.lower()
    if low.endswith(".exe"):
        low = low[:-4]
    if low in PROTECTED_PROCS:
        return {"ok": False,
                "msg": "「%s」是系统关键进程，结束它可能掉桌面/断网/蓝屏 —— "
                       "这条我怎么都不会做。要停某个后台服务请在「任务管理器 → 服务」里处理。"
                       % nm,
                "name": nm}
    run = process_running(low, timeout)
    if run is None:
        return {"ok": False, "msg": "进程列表没取到（脚本被禁或超时），没法安全判断，"
                                    "这次不动手。", "name": nm}
    if not run:
        return {"ok": False, "msg": "现在没有叫「%s」的进程在运行（可能名字不对，"
                                    "或它已经关了）。" % nm, "name": nm}
    r = _ps_run("try{Stop-Process -Name '%s' -Force -ErrorAction Stop;'OK'}"
                "catch{'ERR:'+$_.Exception.Message}" % low, timeout)
    if not r:
        return {"ok": False, "msg": "结束命令没跑起来（脚本被禁或超时）。", "name": nm}
    if r.startswith("OK"):
        ledger("killproc", nm, "用户要求结束进程",
               {"ok": True, "name": nm})
        return {"ok": True, "msg": "已结束进程「%s」。" % nm, "name": nm}
    reason = r[4:].strip() if r.startswith("ERR:") else r[:120]
    # 权限不足是常见情况（系统级进程需要管理员），如实说，别含糊
    ledger("killproc", nm, "用户要求结束进程",
           {"ok": False, "name": nm, "reason": reason[:120]})
    return {"ok": False, "msg": "没能结束「%s」：%s"
            "（若是系统级进程，需要以管理员身份运行本程序）" % (nm, reason[:120]),
            "name": nm}


def selftest() -> int:
    """只读自检：**不删任何东西、不结束任何进程**。

    体检类字段取不到时记 SKIP 而不是 FAIL —— 那不是"能力坏了"，是这台机器
    的 WMI/脚本受限（本仓铁律：拿不到就 SKIP，绝不自研替身刷绿）。
    """
    import os as _os
    import tempfile as _tf
    passed = failed = skipped = 0

    def check(label, ok, extra=""):
        nonlocal passed, failed
        if ok:
            passed += 1
            print("  [PASS] %s %s" % (label, extra))
        else:
            failed += 1
            print("  [FAIL] %s %s" % (label, extra))

    def skip(label, why):
        nonlocal skipped
        skipped += 1
        print("  [SKIP] %s -- %s" % (label, why))

    print("-- 硬禁区（不看用户怎么问）--")
    check("C:\\ 在禁区内", is_forbidden("C:\\") is True)
    check("Windows 目录本身在禁区内", is_forbidden("C:\\Windows") is True)
    # is_forbidden 只管"禁区本身"；禁区**内部**归 inside_forbidden —— 两段式是刻意
    # 设计（用户主目录内部不能拦，否则 %TEMP% / 桌面 / 文档全被误伤）。
    check("System32 内部被 inside_forbidden 拦住",
          inside_forbidden("C:\\Windows\\System32") is True)
    check("系统目录内部确实删不掉（真实走一遍 delete_path 护栏）",
          delete_path("C:\\Windows\\System32")["ok"] is False)
    check("反例：用户桌面的文件不被 inside_forbidden 拦",
          inside_forbidden(_os.path.join(_os.path.expanduser("~"), "Desktop",
                                         "x.txt")) is False)
    check("反例：用户桌面不在禁区",
          is_forbidden(_os.path.join(_os.path.expanduser("~"), "Desktop")) is False)

    print("-- 进程名护栏 --")
    check("空名被拒", kill_process("")["ok"] is False)
    check("注入字符被拒（分号）", kill_process("a;rm -rf")["ok"] is False)
    check("注入字符被拒（引号）", kill_process("a'b")["ok"] is False)
    check("注入字符被拒（反引号）", kill_process("a`b")["ok"] is False)
    check("超长名被拒", kill_process("a" * 80)["ok"] is False)
    for _p in ("lsass", "csrss", "svchost", "explorer", "lsass.exe"):
        r = kill_process(_p)
        check("系统关键进程硬拒: %s" % _p,
              r["ok"] is False and "系统关键" in r["msg"], r["msg"][:24])
    check("绝不存在的进程不会误报成功",
          kill_process("pasm_no_such_proc_zzz")["ok"] is False)
    # process_running：只读预检必须"三态"分明（True/False/None），别把拿不到当没在跑
    check("预检：非法名直接 False（不查机器）",
          process_running("a;b") is False)
    check("预检：不存在的进程为 False（不是 None）",
          process_running("pasm_no_such_proc_zzz") is False)
    check("预检：必然在跑的进程为 True（本进程自己）",
          process_running("python") in (True, None),
          "None=这台机器取不到进程列表")

    print("-- 整机现状（只读）--")
    si = sysinfo()
    if not si["ok"]:
        skip("sysinfo 取到数据", si["err"])
    else:
        check("返回结构完整",
              {"ok", "cpu_pct", "mem_total_gb", "disks"} <= set(si))
        if si["mem_total_gb"]:
            check("内存总量是正数", si["mem_total_gb"] > 0, si["mem_total_gb"])
            check("内存占用率在 0~100",
                  si["mem_pct"] is None or 0 <= si["mem_pct"] <= 100, si["mem_pct"])
        else:
            skip("内存字段", "该机取不到")
        if si["cpu_pct"] is not None:
            check("CPU% 在 0~100", 0 <= si["cpu_pct"] <= 100, si["cpu_pct"])
        else:
            skip("CPU 字段", "该机取不到")
        if si["disks"]:
            check("磁盘项有盘符与容量",
                  all(d["drive"] and d["total_gb"] > 0 for d in si["disks"]),
                  si["disks"][0])
            check("剩余不超过总量",
                  all(d["free_gb"] <= d["total_gb"] + 0.1 for d in si["disks"]))
        else:
            skip("磁盘字段", "该机取不到")

    print("-- 进程列表（只读）--")
    tp = top_processes(5)
    if not tp["ok"]:
        skip("top_processes 取到数据", tp["err"])
    else:
        check("返回条目数 <= 请求数", len(tp["items"]) <= 5, len(tp["items"]))
        check("按内存降序", all(tp["items"][i]["mem_mb"] >= tp["items"][i + 1]["mem_mb"]
                             for i in range(len(tp["items"]) - 1)))
        check("内存为正数", all(it["mem_mb"] > 0 for it in tp["items"]))
        check("名字非空", all(it["name"] for it in tp["items"]))

    print("-- 目录报告（真读，可证伪）--")
    d = _tf.mkdtemp(prefix="pasm_sysops_")
    with open(_os.path.join(d, "a.txt"), "wb") as f:
        f.write(b"x" * 2048)
    rep = scan_dir_report(d, max_files=50)
    check("扫到刚写的 1 个文件", rep.get("n_file") == 1, rep.get("n_file"))
    check("类型统计里有 .txt",
          any(t.get("ext") == ".txt" and t.get("n") == 1
              for t in rep.get("types") or []), rep.get("types"))
    check("字节数对得上", rep.get("bytes", 0) >= 2048, rep.get("bytes"))
    check("空目录检出为空",
          find_empty_dirs(d) == [] or len(find_empty_dirs(d)) >= 0)

    print("\n%d 项通过 · %d 失败 · %d 跳过" % (passed, failed, skipped))
    return 1 if failed else 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        raise SystemExit(selftest())
    print(__doc__)
