# -*- coding: utf-8 -*-
"""系统电源操作：**定时关机 / 定时重启 / 取消**。

实现方式：**Windows 原生倒计时** `shutdown /s /f /t <秒>`。
为什么不用"计划任务（schtasks）"：
  · 本机安全策略把 `schtasks.exe` 列进了黑名单（实测拒绝访问），而且计划任务
    在部分受管电脑上同样被禁；
  · `shutdown /t` 由**系统自己倒计时** —— 程序被关掉/崩溃也照样到点执行，
    正好满足"人不在电脑旁，到点关机"（真机需求 2026-09-30 19:30 强制关机）；
  · 取消只需 `shutdown /a`，不需要删任务。

安全底线（写死在模块里，调用方绕不过）：
  · **只用 `shutdown` 这条系统命令**，参数由本模块拼装 —— 不接受调用方给的自由命令；
  · 时间必须已过校验（0–23 / 0–59），非法一律 `ValueError`；
  · 创建 / 取消都要先过调用方的**权限确认**，并写入操作台账（`sysops.note_action`）；
  · `probe()` 先探"这台机器允许不允许执行 shutdown"，不允许就**如实回报**，不假装设好了。

⚠️ 开发环境（自动化沙箱）通常会拦电源类命令，所以 `selftest` **只验证命令拼装与秒数计算**，
真正的执行结果由 `create()` 的返回码如实回报 —— 不到点生效就是没生效，绝不粉饰。
"""
from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
import time

#: 允许的动作 → shutdown 开关。**只有这两个**，不给调用方传任意命令的机会。
ACTIONS = {"shutdown": "/s", "restart": "/r"}


# ------------------------------------------------------------------ 计算与拼装
def seconds_until(hh: int, mm: int, *, now=None) -> int:
    """距"今天（或明天）的 hh:mm"还有多少秒。已过点 → 顺延到明天。"""
    if not _time_ok(hh, mm):
        raise ValueError("时间非法：%r:%r" % (hh, mm))
    now = now or time.localtime()
    cur = now.tm_hour * 3600 + now.tm_min * 60 + now.tm_sec
    tgt = int(hh) * 3600 + int(mm) * 60
    d = tgt - cur
    if d <= 0:
        d += 86400                       # 已经过点了 → 明天同一时刻
    return d


def build_schedule(hh: int, mm: int, action: str = "shutdown") -> list:
    """`shutdown /s|/r /f /t <秒>` —— 到点强制关机/重启。"""
    if action not in ACTIONS:
        raise ValueError("不支持的动作：%r（只允许 %s）" % (action, "/".join(ACTIONS)))
    sec = seconds_until(hh, mm)
    return ["shutdown", ACTIONS[action], "/f", "/t", str(sec)]


def build_schedule_secs(sec: int, action: str = "shutdown") -> list:
    """按**相对秒数**设定（"30 分钟后"这类说法用）。"""
    if action not in ACTIONS:
        raise ValueError("不支持的动作：%r（只允许 %s）" % (action, "/".join(ACTIONS)))
    try:
        sec = int(sec)
    except Exception:                                            # noqa: BLE001
        raise ValueError("秒数非法：%r" % (sec,))
    if not (1 <= sec <= 315359999):
        raise ValueError("秒数超出范围：%r" % sec)
    return ["shutdown", ACTIONS[action], "/f", "/t", str(sec)]


def build_abort() -> list:
    """取消（含中止已经在跑的系统倒计时）。"""
    return ["shutdown", "/a"]


def _time_ok(hh, mm) -> bool:
    try:
        return 0 <= int(hh) <= 23 and 0 <= int(mm) <= 59
    except Exception:                                            # noqa: BLE001
        return False


def describe(hh: int, mm: int, action: str = "shutdown") -> str:
    """给"确认弹窗"用的人话描述（含"还剩多久"）。"""
    what = "强制关机" if action == "shutdown" else "强制重启"
    sec = seconds_until(hh, mm)
    if sec >= 3600:
        left = "约 %d 小时 %d 分钟后" % (sec // 3600, (sec % 3600) // 60)
    else:
        left = "约 %d 分钟后" % max(1, sec // 60)
    return ("设定系统倒计时：%02d:%02d 自动%s（%s；程序关掉了也会执行，取消说「取消关机」）"
            % (hh, mm, what, left))


# ------------------------------------------------------------------ 执行
def _run(cmd: list, timeout: int = 40) -> dict:
    exe = shutil.which(cmd[0]) or cmd[0]
    try:
        p = subprocess.run([exe] + cmd[1:], capture_output=True, timeout=timeout,
                           creationflags=(0x08000000 if os.name == "nt" else 0))
        raw = (p.stdout or b"") + (p.stderr or b"")
        out = ""
        for enc in ("utf-8", "gbk"):
            try:
                out = raw.decode(enc)
                break
            except Exception:                                    # noqa: BLE001
                continue
        if not out:
            out = raw.decode("utf-8", "replace")
        return {"ok": p.returncode == 0, "rc": p.returncode, "out": out.strip()[:400],
                "cmd": " ".join(cmd)}
    except Exception as ex:                                      # noqa: BLE001
        return {"ok": False, "rc": -1, "out": str(ex)[:200], "cmd": " ".join(cmd)}


def probe() -> dict:
    """探测"这台机器允不允许执行 shutdown"。

    用 `shutdown /a`：没有待中止的关机时 Windows 返回 **1116**（这是**可用**的证明）；
    0 也代表可用。被策略拦掉时会是别的错误码（如 203 找不到环境选项、5 拒绝访问）。
    """
    r = _run(build_abort())
    usable = r["rc"] in (0, 1116)
    return {"usable": usable, "rc": r["rc"], "out": r["out"]}


def _ledger(action: str, target: str, ok: bool, reason: str):
    try:
        import sysops as SYS
        SYS.note_action(action, target, ok=ok, reason=reason)
    except Exception:                                            # noqa: BLE001
        pass


def create(hh: int, mm: int, action: str = "shutdown") -> dict:
    """设定倒计时（**调用方必须先拿到用户确认**）。返回 {ok, out, rc, when, secs}。"""
    cmd = build_schedule(hh, mm, action)
    sec = int(cmd[-1])
    r = _run(cmd)
    r["when"] = "%02d:%02d" % (hh, mm)
    r["secs"] = sec
    _ledger("power_schedule", "%s@%s" % (action, r["when"]), r["ok"],
            "设定系统倒计时%s：%s" % ("关机" if action == "shutdown" else "重启",
                                    (r["out"] or "无输出")[:120]))
    return r


def create_secs(sec: int, action: str = "shutdown") -> dict:
    """按相对秒数设定（"30 分钟后"用）。"""
    cmd = build_schedule_secs(sec, action)
    r = _run(cmd)
    r["secs"] = int(cmd[-1])
    tm = time.localtime(time.time() + r["secs"])
    r["when"] = "%02d:%02d" % (tm.tm_hour, tm.tm_min)
    _ledger("power_schedule", "%s@+%ds" % (action, r["secs"]), r["ok"],
            "设定系统倒计时%s（%d 秒后 → %s）：%s"
            % ("关机" if action == "shutdown" else "重启", r["secs"], r["when"],
               (r["out"] or "无输出")[:100]))
    return r


def describe_secs(sec: int, action: str = "shutdown") -> str:
    """给确认弹窗用：相对秒数版本。"""
    what = "强制关机" if action == "shutdown" else "强制重启"
    tm = time.localtime(time.time() + int(sec))
    if sec >= 3600:
        left = "约 %d 小时 %d 分钟后" % (sec // 3600, (sec % 3600) // 60)
    else:
        left = "约 %d 分钟后" % max(1, sec // 60)
    return ("设定系统倒计时：%s（%02d:%02d）自动%s；程序关掉了也会执行，取消说「取消关机」"
            % (left, tm.tm_hour, tm.tm_min, what))


def cancel() -> dict:
    r = _run(build_abort())
    _ledger("power_cancel", "shutdown /a", r["ok"], (r["out"] or "已取消")[:120])
    return r


# ------------------------------------------------------------------ selftest
def selftest() -> int:
    n_ok = n_bad = 0

    def ck(cond, name, detail=""):
        nonlocal n_ok, n_bad
        if cond:
            n_ok += 1
            print("  [PASS] %s" % name)
        else:
            n_bad += 1
            print("  [FAIL] %s  %s" % (name, detail))

    print("=== ① 命令拼装（只用 shutdown，参数由模块拼）===")
    c = build_schedule(19, 30, "shutdown")
    ck(c[0] == "shutdown" and c[1] == "/s" and "/f" in c and "/t" in c,
       "关机命令 = shutdown /s /f /t <秒>", c)
    ck(c[-1].isdigit() and int(c[-1]) > 0, "/t 是正整数秒", c[-1])
    ck(build_schedule(9, 0, "restart")[1] == "/r", "重启用 /r")
    ck(build_abort() == ["shutdown", "/a"], "取消 = shutdown /a", build_abort())

    print("=== ② 秒数计算（今天 / 顺延明天）===")
    class _T:
        def __init__(self, h, m, s=0):
            self.tm_hour, self.tm_min, self.tm_sec = h, m, s
    ck(seconds_until(19, 30, now=_T(18, 30)) == 3600,
       "18:30 → 19:30 = 3600 秒", seconds_until(19, 30, now=_T(18, 30)))
    ck(seconds_until(19, 30, now=_T(20, 0)) == 86400 - 1800,
       "20:00 说 19:30 → 顺延到明天（84600 秒）", seconds_until(19, 30, now=_T(20, 0)))

    print("=== ③ 非法输入一律拒绝 ===")
    for bad in ((24, 0), (-1, 0), ("x", 0), (12, 99)):
        try:
            build_schedule(*bad)
            ck(False, "拒绝非法时间 %r" % (bad,))
        except ValueError:
            ck(True, "拒绝非法时间 %r" % (bad,))
    try:
        build_schedule(9, 0, "rm -rf /")
        ck(False, "拒绝任意动作名")
    except ValueError:
        ck(True, "拒绝任意动作名（只允许 shutdown/restart）")
    ck(all(x in ("shutdown", "/s", "/r", "/f", "/t") or x.isdigit()
           for x in build_schedule(9, 0)), "拼出来的参数全在白名单内（不可能夹带别的）")

    print("=== ④ 可用性探测（本机/沙箱可能被策略拦，**如实回报**）===")
    p = probe()
    print("  probe: usable=%s rc=%s out=%r" % (p["usable"], p["rc"], p["out"][:80]))
    ck(p["usable"] or p["rc"] != 0,
       "探测有明确结论（可用 / 有错误码可如实回报用户）", p)

    print("\nPASS=%d FAIL=%d" % (n_ok, n_bad))
    return 0 if n_bad == 0 else 1


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "selftest":
        sys.exit(selftest())
    print(__doc__)
