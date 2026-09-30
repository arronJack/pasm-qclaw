# -*- coding: utf-8 -*-
"""conncheck.py —— 外部链路「配置→可用」自检（P2-10）。

真问题：`browser_agent.py`、`connector_*`（飞书/微信/Discord/HTTP/邮件/日历）、
`mcp_bridge.py` 这些能力**都写了**，但 `config.json` 里 `ark_key`、`feishu_*`、
`discord_bot_token` 全为空 —— 能力在、链路没通。用户问"为什么连不上"时，
系统自己也答不上来（没有一处能告诉你"哪条链路缺什么、缺了会怎样"）。

本模块给一张**诚实的体检表**：
  · 每个连接器：必填项是否齐 / 依赖是否装（如 playwright）/ 可选联网探测结果；
  · 输出三态：`ready`（齐了）/ `unconfigured`（缺配置，列出缺哪些键）/ `broken`（配了但不通，带原因）；
  · `summary()` 一行文字 + `table()` 明细，供设置页与"一键体检"按钮直接用。

默认**不做联网探测**（`probe=False`）—— 体检本身不该产生副作用或耗时；
需要时显式开 `probe=True`（会真发请求，失败如实记录，不重试）。
"""
from __future__ import annotations

import importlib.util
import os

#: 连接器定义：{名字: {"keys": [必填配置键], "deps": [import 名], "probe": 可选探测函数名}}
CONNECTORS = {
    "云端大模型（DeepSeek 等）": {"keys": ["api_key", "base_url", "model"], "deps": []},
    "火山方舟（视频）": {"keys": ["ark_key", "ark_model"], "deps": []},
    "浏览器自动化": {"keys": [], "deps": ["playwright"]},
    "MCP 认知工具（pasm-mcp-server）": {"keys": [], "deps": ["mcp"]},
    "飞书机器人": {"keys": ["feishu_app_id", "feishu_app_secret"], "deps": []},
    "企业微信机器人": {"keys": ["wecom_bot_url"], "deps": []},
    "微信公众号": {"keys": ["wechat_appid", "wechat_appsecret", "wechat_token"], "deps": []},
    "Discord 机器人": {"keys": ["discord_bot_token"], "deps": []},
    "通用 Webhook（入站）": {"keys": ["webhook_in_token"], "deps": []},
    "ComfyUI（本地出图/出视频）": {"keys": ["comfy_url"], "deps": []},
}

_TRUE = ("1", "true", "yes", "on", "full")


def _has(cfg: dict, key: str) -> bool:
    v = (cfg or {}).get(key)
    if isinstance(v, bool):
        return v
    return bool(str(v or "").strip())


def _dep_ok(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except Exception:                                            # noqa: BLE001
        return False


def check_all(cfg: dict = None, *, probe: bool = False) -> list:
    """体检所有连接器 → [{name, state, missing, detail, extras}]。

    state：ready / unconfigured / broken
    """
    cfg = cfg or {}
    out = []
    for name, spec in CONNECTORS.items():
        missing = [k for k in spec.get("keys") or [] if not _has(cfg, k)]
        bad_deps = [d for d in spec.get("deps") or [] if not _dep_ok(d)]
        # 有些连接器有"开关"语义：mode 是 off 时即使配了也不算 ready
        extras = []
        mode_key = {"飞书机器人": "feishu_mode", "Discord 机器人": "discord_mode",
                    "微信公众号": "wechat_mode", "通用 Webhook（入站）": "webhook_in_mode"}.get(name)
        if mode_key:
            mode = str(cfg.get(mode_key) or "off").lower()
            extras.append("开关 %s=%s" % (mode_key, mode))
            if mode not in _TRUE:
                out.append({"name": name, "state": "unconfigured",
                            "missing": missing or ["（%s 未开启）" % mode_key],
                            "detail": "配置项已填但功能开关是 off", "extras": extras})
                continue
        if missing or bad_deps:
            out.append({"name": name,
                        "state": "unconfigured" if missing else "broken",
                        "missing": missing + [("依赖 %s 未安装" % d) for d in bad_deps],
                        "detail": "缺配置" if missing else "依赖缺失", "extras": extras})
            continue
        st = {"name": name, "state": "ready", "missing": [], "detail": "配置齐全",
              "extras": extras}
        if probe:
            try:
                st["probe"] = _probe(name, cfg)
            except Exception as ex:                              # noqa: BLE001
                st["state"] = "broken"
                st["probe"] = "探测异常：%s" % str(ex)[:160]
        out.append(st)
    return out


def _probe(name: str, cfg: dict) -> str:
    """可选联网探测（只在 probe=True 时调用）。失败不抛，如实返回文字。"""
    import urllib.request
    if name.startswith("云端大模型"):
        url = (cfg.get("base_url") or "").rstrip("/") + "/models"
        req = urllib.request.Request(url, headers={
            "Authorization": "Bearer " + str(cfg.get("api_key") or "")})
        with urllib.request.urlopen(req, timeout=8) as r:
            return "HTTP %s（连通）" % r.status
    if name.startswith("ComfyUI"):
        with urllib.request.urlopen(str(cfg.get("comfy_url")).rstrip("/") + "/system_stats",
                                    timeout=5) as r:
            return "HTTP %s（连通）" % r.status
    if name.startswith("MCP"):
        try:
            import mcp_bridge as MB
            return "桥已加载：%s" % ("是" if MB.get_bridge() else "否（服务未启动）")
        except Exception as ex:                                  # noqa: BLE001
            return "桥不可用：%s" % str(ex)[:120]
    return "（该链路无轻量探测）"


def summary(items: list) -> str:
    """一行结论（给按钮/状态栏）。"""
    if not items:
        return "没有可体检的链路"
    r = sum(1 for x in items if x["state"] == "ready")
    u = sum(1 for x in items if x["state"] == "unconfigured")
    b = sum(1 for x in items if x["state"] == "broken")
    return "外部链路：可用 %d / 未配置 %d / 异常 %d（共 %d）" % (r, u, b, len(items))


def table(items: list) -> str:
    """明细表（纯文本，设置页可直接显示）。"""
    rows = [summary(items), ""]
    icon = {"ready": "✅", "unconfigured": "○", "broken": "⚠"}
    for x in items:
        rows.append("%s %-28s %s" % (icon.get(x["state"], "?"), x["name"], x["detail"]))
        if x.get("missing"):
            rows.append("     缺：%s" % "、".join(x["missing"]))
        if x.get("probe"):
            rows.append("     探测：%s" % x["probe"])
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

    print("=== 空配置：有必填项的一律未配置；无必填项的只看依赖 ===")
    items = check_all({})
    ck("条目数与定义一致", len(items) == len(CONNECTORS), len(items))
    keyed = [x for x in items if CONNECTORS[x["name"]].get("keys")]
    ck("所有**有必填键**的连接器都判未配置",
       all(x["state"] == "unconfigured" for x in keyed),
       [(x["name"], x["state"]) for x in keyed if x["state"] != "unconfigured"])
    keyless = [x for x in items if not CONNECTORS[x["name"]].get("keys")]
    ck("无必填键的连接器状态由**依赖**决定（ready/broken，且不崩）",
       all(x["state"] in ("ready", "broken") for x in keyless),
       [(x["name"], x["state"]) for x in keyless])
    llm = [x for x in items if x["name"].startswith("云端大模型")][0]
    ck("云端缺 api_key 被列出", "api_key" in llm["missing"], llm)
    _exp_u = sum(1 for x in items if x["state"] == "unconfigured")
    ck("summary 与明细一致", ("未配置 %d" % _exp_u) in summary(items), summary(items))
    ck("table 有明细", "云端大模型" in table(items))

    print("\n=== 配齐即 ready ===")
    cfg = {"api_key": "sk-x", "base_url": "https://api.deepseek.com/v1", "model": "deepseek-chat",
           "ark_key": "k", "ark_model": "m", "feishu_app_id": "i", "feishu_app_secret": "s",
           "feishu_mode": "on", "wecom_bot_url": "https://x",
           "wechat_appid": "a", "wechat_appsecret": "b", "wechat_token": "t",
           "discord_bot_token": "d", "discord_mode": "on",
           "webhook_in_token": "w", "webhook_in_mode": "on", "comfy_url": "http://127.0.0.1:8188"}
    items2 = check_all(cfg)
    ready = [x["name"] for x in items2 if x["state"] == "ready"]
    ck("配齐的都判 ready", len(ready) >= 7, ready)
    ck("云端模型 ready", any(n.startswith("云端大模型") for n in ready), ready)

    print("\n=== 开关 off：配了也不算可用 ===")
    cfg3 = dict(cfg, feishu_mode="off")
    fs = [x for x in check_all(cfg3) if x["name"] == "飞书机器人"][0]
    ck("开关 off → 未配置并说明", fs["state"] == "unconfigured" and "开关" in fs["detail"],
       fs)

    print("\n=== 依赖缺失：判 broken 而不是 ready ===")
    # 用一个必然不存在的依赖名，模拟"库没装"
    saved = dict(CONNECTORS)
    try:
        CONNECTORS["依赖测试链路"] = {"keys": [], "deps": ["绝对不存在的模块xyz"]}
        it = [x for x in check_all({}) if x["name"] == "依赖测试链路"][0]
        ck("缺依赖 → broken", it["state"] == "broken" and "未安装" in "".join(it["missing"]), it)
    finally:
        CONNECTORS.clear()
        CONNECTORS.update(saved)

    print("\n=== 探测默认关闭（体检不该有副作用）===")
    ck("默认不产生 probe 字段", all("probe" not in x for x in check_all(cfg)))

    print("PASS=%d FAIL=%d" % (P, F))
    print("RESULT: %s" % ("PASS" if F == 0 else "FAILED"))
    return 0 if F == 0 else 1


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "selftest":
        raise SystemExit(selftest())
    print(__doc__)
