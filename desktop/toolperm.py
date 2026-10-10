# -*- coding: utf-8 -*-
"""toolperm.py —— **工具层**的统一权限门（P0-3）。

为什么需要：`permission.py` 的 `decide(level, risk)` 早就写好了，但全仓只有
`pasm_companion.py` 一处调用它 —— 也就是说权限只挡在"入口"，模型一旦走到
工具层（写文件/跑脚本/删东西）就**不再过门**。真机后果：设置里选了"安全"档，
模型照样能写盘、能执行。

本模块把"工具 → 风险等级"钉成一张表，并在**执行出口**统一过门：
  · `allow` → 直接放行；
  · `ask` → 有确认通道（UI 弹窗回调）就问用户；**没有通道就拒绝**（安全默认，
    并把"为什么没做"如实返回，绝不静默执行）。
每次判定都记一笔（`sysops.note_action("perm", ...)`），便于事后审计"谁批的、批了什么"。

与 `devloop` 的关系：devloop 有自己的命令白名单（进程级安全）；本模块管的是
**能力级**授权（"允许不允许你执行命令/写盘"），两者叠加，不互相替代。
"""
from __future__ import annotations

import time

import permission as PERM

# ---------------------------------------------------------------------------
# 工具 → 风险等级（新增工具必须在这里登记；未登记的按 HIGH 处理，fail-safe）
#   low  = 只读，不改变系统状态
#   med  = 写文件 / 打开外部程序（可恢复）
#   high = 执行脚本、运行项目、删除、清理（可能造成不可逆后果）
#   crit = 批量删除 / 系统级操作
# ---------------------------------------------------------------------------
TOOL_RISK = {
    # 只读
    "read": "low", "list": "low", "peek_file": "low", "read_folder": "low",
    "read_project": "low", "list_dir": "low", "find_files": "low",
    "fetch_text": "low", "web_search": "low", "recall": "low", "verify": "low",
    # ★ 打开类：打开应用 / 文件 / 文件夹 / 浏览器 —— **无副作用、可逆**
    #   打开软件不会改任何东西，随手就能关掉；它和"删除/执行脚本"不是一个量级。
    #   旧版误登记为 med，导致「安全档」下说一句"打开网易云音乐"都被拦成
    #   "需要确认"（且没有通道时直接拒绝）—— 不符合直觉，也不符合最小惊讶原则。
    #   风险定级只认"能不能造成不可逆后果"：打开 = 没有，故 low。
    "open_app": "low", "open_browser": "low", "open_path": "low",
    # 中风险（可恢复：会写盘/改状态，但可撤销）
    "genfile": "med", "write": "med", "save_project": "med",
    "write_script": "med",
    # 高风险（可能造成不可逆后果：执行代码、下载、安装、推远端）
    "run_script": "high", "run_project": "high", "run": "high",
    "open_url_download": "high", "install": "high", "git_push": "high",
    # 危险
    "delete": "crit", "clean_junk": "crit", "remove": "crit", "format": "crit",
}

#: 给用户看的风险中文名（拒绝/确认文案用）。**必须与风险等级一一对应** ——
#: 旧版把 med 也写成"高风险操作"，用户看到"打开软件 = 高风险"自然觉得不对劲。
RISK_LABEL = {
    "low": "只读操作",
    "med": "会改动系统的操作",
    "high": "高风险操作",
    "crit": "不可逆操作",
}

_LEVEL = "safe"
_ASK = None          # 确认回调：ask(tool, risk, target) -> bool
_LOG = None
#: ★ 动态提供者：app 用它把"当前档位/弹窗通道"实时喂进来（比 push 一次更可靠 ——
#: 用户在设置里改了档位，下一次工具调用立刻生效，不用重启）。
_LEVEL_PROVIDER = None
_ASK_PROVIDER = None


def set_level_provider(fn) -> None:
    global _LEVEL_PROVIDER
    _LEVEL_PROVIDER = fn


def set_ask_provider(fn) -> None:
    global _ASK_PROVIDER
    _ASK_PROVIDER = fn


def _level() -> str:
    if _LEVEL_PROVIDER:
        try:
            lv = _LEVEL_PROVIDER()
            if lv:
                return PERM.normalize_level(lv)
        except Exception:                                        # noqa: BLE001
            pass
    return _LEVEL


def configure(level: str = None, ask=None, log=None) -> None:
    """由 app 在读取配置时调用（level=安全/标准/完全访问；ask=UI 弹窗回调）。"""
    global _LEVEL, _ASK, _LOG
    if level is not None:
        _LEVEL = PERM.normalize_level(level)
    if ask is not None:
        _ASK = ask
    if log is not None:
        _LOG = log


def current_level() -> str:
    return _LEVEL


def risk_of(tool: str) -> str:
    """查工具风险；**未登记一律 high**（宁可多问一句，不可放行未知能力）。"""
    return TOOL_RISK.get((tool or "").strip().lower(), "high")


def _audit(tool: str, target: str, action: str, risk: str, why: str) -> None:
    try:
        import sysops as SYS
        SYS.note_action("perm", "%s → %s" % (tool, str(target)[:60]),
                        ok=(action == "allow"),
                        reason="[%s档/%s风险] %s" % (_LEVEL, risk, why))
    except Exception:                                            # noqa: BLE001
        pass
    if _LOG:
        try:
            _LOG(tool, target, action, risk, why)
        except Exception:                                        # noqa: BLE001
            pass


def decide(tool: str, target: str = "") -> dict:
    """只看策略，不执行、不打扰用户。返回 {allow, need_confirm, risk, reason}。"""
    risk = risk_of(tool)
    lv = _level()
    verdict = PERM.decide(lv, risk)
    need = (verdict == PERM.ASK)
    return {"allow": not need, "need_confirm": need, "risk": risk,
            "reason": ("%s档 × %s风险 → 需要你确认" % (lv, risk)) if need else
                      ("%s档 × %s风险 → 放行" % (lv, risk))}


def guard(tool: str, target: str = "") -> dict:
    """执行前的统一门。返回 {ok, need_confirm, risk, reason}；`ok=False` 时调用方**必须停手**。"""
    d = decide(tool, target)
    ask = _ASK
    if ask is None and _ASK_PROVIDER is not None:
        try:
            ask = _ASK_PROVIDER()
        except Exception:                                        # noqa: BLE001
            ask = None
    if d["allow"]:
        _audit(tool, target, "allow", d["risk"], "策略放行")
        return {"ok": True, "need_confirm": False, "risk": d["risk"], "reason": d["reason"]}
    if ask is None:
        # 没有确认通道 → 拒绝（安全默认），并把原因说清（不静默、不假装做了）
        why = "需要确认但没有确认通道 → 已拒绝"
        _audit(tool, target, "deny", d["risk"], why)
        _rk = RISK_LABEL.get(d["risk"], "需要确认的操作")
        return {"ok": False, "need_confirm": True, "risk": d["risk"],
                "reason": "这一步属于%s（%s，%s档），需要你确认；"
                          "当前没有可用的确认通道，所以我**没有执行**。"
                          % (_rk, d["risk"], _level())}
    t0 = time.time()
    try:
        approved = bool(ask(tool, d["risk"], target))
    except Exception as ex:                                      # noqa: BLE001
        approved = False
        why = "确认通道异常：%s" % str(ex)[:80]
    else:
        why = "用户%s（%.1fs）" % ("同意" if approved else "拒绝", time.time() - t0)
    _audit(tool, target, "allow" if approved else "deny", d["risk"], why)
    if approved:
        return {"ok": True, "need_confirm": True, "risk": d["risk"], "reason": why}
    return {"ok": False, "need_confirm": True, "risk": d["risk"],
            "reason": "你拒绝了这一步（%s/%s），我没有执行。" % (tool, d["risk"])}


def describe_table() -> str:
    """给设置页展示：当前档位下每个工具会被放行还是询问。"""
    lv = _level()
    rows = ["当前档位：%s（%s）" % (lv, PERM.describe(lv))]
    for name in sorted(TOOL_RISK):
        d = decide(name)
        rows.append("  %-16s %-5s → %s" % (name, d["risk"],
                                          "放行" if d["allow"] else "需要确认"))
    return "\n".join(rows)


# --------------------------------------------------------------------------- selftest
def selftest() -> int:
    P = F = 0

    def ck(name, ok, detail=""):
        nonlocal P, F
        if ok:
            P += 1
        else:
            F += 1
            print("  [FAIL] %s   %s" % (name, detail))

    print("=== 风险表 ===")
    ck("只读工具是 low", risk_of("read") == "low")
    ck("写盘是中风险", risk_of("save_project") == "med")
    ck("执行脚本是高危", risk_of("run_script") == "high")
    ck("删除是危险级", risk_of("delete") == "crit")
    ck("**未登记工具按 high 处理**（fail-safe）", risk_of("某个新工具") == "high")
    # ★ 契约：打开类 = 无副作用 = low（否则安全档下"打开网易云音乐"会被拦）
    ck("打开应用/浏览器/路径都是 low",
       risk_of("open_app") == "low" and risk_of("open_browser") == "low"
       and risk_of("open_path") == "low")

    print("\n=== 安全档：打开类放行，高危必须问 ===")
    configure(level="safe", ask=None)
    ck("安全档打开应用直接放行", guard("open_app", "网易云音乐")["ok"])
    ck("安全档打开浏览器直接放行", guard("open_browser", "https://x")["ok"])
    ck("安全档打开文件夹直接放行", guard("open_path", r"D:\下载")["ok"])
    g = guard("run_script", "a.py")
    ck("安全档跑脚本被拒（无确认通道）", not g["ok"], g)
    ck("拒绝理由说得清", "没有执行" in g["reason"], g["reason"])
    ck("med 不再被误写成「高风险」",
       "高风险" not in guard("write", "x.txt")["reason"], guard("write", "x.txt")["reason"])
    g = guard("read", "a.py")
    ck("只读放行", g["ok"], g)

    print("\n=== 有确认通道：同意/拒绝都要如实 ===")
    calls = []
    configure(level="safe", ask=lambda t, r, tg: (calls.append((t, r, tg)), True)[1])
    g = guard("run_script", "a.py")
    ck("用户同意 → 放行", g["ok"] and calls, (g, calls))
    configure(level="safe", ask=lambda t, r, tg: False)
    g = guard("run_script", "a.py")
    ck("用户拒绝 → 不执行并说明", (not g["ok"]) and "拒绝" in g["reason"], g)

    print("\n=== 完全访问档：高危直接放行 ===")
    configure(level="full", ask=None)
    ck("full 档 run_script 放行", guard("run_script", "a.py")["ok"])
    ck("full 档 delete 放行", guard("delete", "x")["ok"])

    print("\n=== 审计：每次判定都留痕 ===")
    import os
    import tempfile
    os.environ["PASMSTUDIO_DATA"] = tempfile.mkdtemp(prefix="toolperm_")
    import importlib

    import sysops
    importlib.reload(sysops)
    configure(level="safe", ask=None)
    guard("run_script", "audit_probe")
    log = os.path.join(os.environ["PASMSTUDIO_DATA"], "ops_ledger.jsonl")
    txt = open(log, encoding="utf-8").read() if os.path.isfile(log) else ""
    ck("拒绝也写进了审计台账", '"action": "perm"' in txt and "audit_probe" in txt, txt[-160:])

    print("\n=== 描述表可用 ===")
    ck("describe_table 含档位说明", "当前档位" in describe_table())

    configure(level="safe", ask=None)
    print("PASS=%d FAIL=%d" % (P, F))
    print("RESULT: %s" % ("PASS" if F == 0 else "FAILED"))
    return 0 if F == 0 else 1


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "selftest":
        raise SystemExit(selftest())
    print(__doc__)
