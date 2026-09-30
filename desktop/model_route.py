# -*- coding: utf-8 -*-
"""model_route.py —— 任务 → 模型 的路由**策略唯一来源**（P0-1）。

为什么单独成模块：路由散在 `pasm_companion._route_endpoint`、`_brain`、`_endpoint`
几处时，改一处就会漏另一处；而且"为什么这次走了本地/云端"必须能被**如实说清**
（用户看到质量波动时，第一件事就是问这个）。

策略（与小志 2026-09-30 的口径一致：**有云端就用，重活别被本地小模型拖死**）：

| 场景 | 结果 |
|---|---|
| 手选 `cloud` | 云端（Key 缺失 → 自动退回本地/离线，并说明） |
| 手选 `local:<名>` + **重活** + 云端可用 + `escalate_heavy=True`（默认） | **升级到云端**，`escalated=True`，并给出提示文案 |
| 手选 `local:<名>` + 轻活 / 无云端 / 关掉升级 | 本地 |
| `auto` + 重活 | 云端优先，否则本地 |
| `auto` + 轻活（聊天/陪伴/提炼） | 本地优先（快·私密·零成本），否则云端 |
| 都没有 | `offline`（走离线微脑） |

`escalate_heavy` 是可关的：用户在设置里固定本地（例如离线场景）时，关掉即完全尊重手选。
"""
from __future__ import annotations

import os

#: 重活任务（需要长输出/多步推理）—— 与 `pasm_companion._HEAVY_TASKS` 同口径
HEAVY_TASKS = {"filegen", "project", "dev", "copy", "skill", "code", "brain"}
#: 轻活任务（短输出，本地小模型足够）
LIGHT_TASKS = {"chat", "pet", "study", "selftest", "tool"}


def is_heavy(task: str, prompt: str = "") -> bool:
    """任务轻重判定。任务名优先；`chat/brain` 再看提示里的干活特征。"""
    import re
    t = (task or "").strip().lower()
    if t in HEAVY_TASKS:
        return True
    if t in LIGHT_TASKS:
        return bool(re.search(r"开发|写代码|实现|项目|全栈|接口|部署|重构|生成.{0,4}(文件|文档|"
                              r"表格|PPT|脚本|代码)|帮我做", (prompt or "")[:400]))
    return False


def decide(task: str, *, has_cloud: bool, has_local: bool, manual: str = "auto",
           prompt: str = "", escalate_heavy: bool = True) -> dict:
    """返回 {"kind": "cloud"|"local"|"offline", "reason": str, "escalated": bool, "heavy": bool}。"""
    heavy = is_heavy(task, prompt)
    manual = (manual or "auto").strip()
    if manual == "cloud":
        if has_cloud:
            return {"kind": "cloud", "reason": "你在设置里固定了云端模型", "escalated": False,
                    "heavy": heavy}
        if has_local:
            return {"kind": "local", "reason": "手选了云端但没填 API Key → 退回本地", "escalated": False,
                    "heavy": heavy}
        return {"kind": "offline", "reason": "手选云端、未填 Key，且没有可用本地模型", "escalated": False,
                "heavy": heavy}
    if manual.startswith("local:"):
        name = manual[len("local:"):]
        if heavy and has_cloud and escalate_heavy:
            return {"kind": "cloud", "escalated": True, "heavy": True,
                    "reason": "这是重活（%s），手选的本地模型『%s』通常写不完 → 自动升级云端"
                              "（想完全用本地：设置里关掉『重活自动升级』）" % (task, name)}
        if has_local:
            return {"kind": "local", "reason": "你在设置里固定了本地模型『%s』" % name,
                    "escalated": False, "heavy": heavy}
        if has_cloud:
            return {"kind": "cloud", "reason": "手选的本地模型不可用 → 退回云端", "escalated": False,
                    "heavy": heavy}
        return {"kind": "offline", "reason": "手选的本地模型不可用，也没有云端 Key", "escalated": False,
                "heavy": heavy}
    # auto
    if heavy:
        if has_cloud:
            return {"kind": "cloud", "reason": "重活（%s）→ 云端优先，保证质量" % task,
                    "escalated": False, "heavy": True}
        if has_local:
            return {"kind": "local", "reason": "重活但没有云端 Key → 只能用本地（建议填 Key 或走分步）",
                    "escalated": False, "heavy": True}
        return {"kind": "offline", "reason": "重活但没有任何可用模型", "escalated": False, "heavy": True}
    if has_local:
        return {"kind": "local", "reason": "轻活 → 本地优先（快·私密·零成本）",
                "escalated": False, "heavy": False}
    if has_cloud:
        return {"kind": "cloud", "reason": "没有本地模型 → 用云端", "escalated": False,
                "heavy": False}
    return {"kind": "offline", "reason": "没有任何可用模型", "escalated": False, "heavy": False}


def notice(d: dict) -> str:
    """给界面/日志的一行如实说明（用词克制，不夸大）。"""
    kind = {"cloud": "云端模型", "local": "本地模型", "offline": "离线微脑"}.get(d.get("kind"), "?")
    tag = "（自动升级）" if d.get("escalated") else ""
    return "本次用%s%s：%s" % (kind, tag, d.get("reason") or "")


def cfg_flag(cfg: dict, key: str, default):
    """从 config 里取开关（容忍 '0'/'false'/'no' 这类写法）。"""
    v = (cfg or {}).get(key, default)
    if isinstance(v, str):
        return v.strip().lower() not in ("0", "false", "no", "off", "")
    return bool(v)


def escalate_enabled(cfg: dict) -> bool:
    """`auto_escalate_heavy`（默认开）—— 关掉则完全尊重手选的本地模型。"""
    return cfg_flag(cfg, "auto_escalate_heavy", True)


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

    print("=== 手选云端 ===")
    d = decide("chat", has_cloud=True, has_local=True, manual="cloud")
    ck("手选云端 → cloud", d["kind"] == "cloud", d)
    d = decide("chat", has_cloud=False, has_local=True, manual="cloud")
    ck("无 Key → 退回本地并说明", d["kind"] == "local" and "Key" in d["reason"], d)

    print("=== 手选本地 + 重活 → 自动升级（本次要补的能力）===")
    d = decide("dev", has_cloud=True, has_local=True, manual="local:qwen2.5:7b")
    ck("dev 升级到云端", d["kind"] == "cloud" and d["escalated"], d)
    ck("说明里点出原因与开关", "重活" in d["reason"] and "关掉" in d["reason"], d["reason"])
    d = decide("dev", has_cloud=True, has_local=True, manual="local:qwen2.5:7b",
               escalate_heavy=False)
    ck("关掉升级 → 尊重手选走本地", d["kind"] == "local" and not d["escalated"], d)
    d = decide("chat", has_cloud=True, has_local=True, manual="local:qwen2.5:7b")
    ck("轻活不升级（省成本/保隐私）", d["kind"] == "local", d)

    print("=== auto ===")
    d = decide("dev", has_cloud=True, has_local=True)
    ck("auto+重活 → 云端", d["kind"] == "cloud", d)
    d = decide("chat", has_cloud=True, has_local=True)
    ck("auto+轻活 → 本地", d["kind"] == "local", d)
    d = decide("chat", has_cloud=False, has_local=False)
    ck("都没 → offline", d["kind"] == "offline", d)
    d = decide("dev", has_cloud=False, has_local=True)
    ck("无云端+重活 → 本地并给建议", d["kind"] == "local" and "分步" in d["reason"], d)

    print("=== 轻重判定与文案 ===")
    ck("dev 是重活", is_heavy("dev"))
    ck("chat 默认轻", not is_heavy("chat", "你好呀"))
    ck("chat 带干活特征 → 重", is_heavy("chat", "帮我开发一个记账系统"))
    ck("notice 含人类可读说明", "本次用" in notice(decide("dev", has_cloud=True, has_local=True,
                                                          manual="local:x")))
    ck("cfg 开关解析 '0' 为假", cfg_flag({"auto_escalate_heavy": "0"}, "auto_escalate_heavy", True) is False)

    print("PASS=%d FAIL=%d" % (P, F))
    print("RESULT: %s" % ("PASS" if F == 0 else "FAILED"))
    return 0 if F == 0 else 1


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "selftest":
        raise SystemExit(selftest())
    print(__doc__)
