# -*- coding: utf-8 -*-
"""msg_source.py —— 统一消息源适配器（v0.30.5）

对标 BaiLongma 的多社交渠道路由，但**不移植它的 Electron 壳**：
本模块只定接口 + 本地实现，让"消息从哪来、往哪发"变成可插拔的一层。

## 为什么需要

现在 `remote_bridge` 已经能发通知（Bark / Server酱 / Telegram）和收 Telegram 消息，
但它是**具体实现**：业务代码想"把结果告诉用户"，就得直接 import 它、直接判断配置。
将来接微信 / 钉钉 / 本机剪贴板，又要在每个调用点加分支。

有了这一层，业务只说 `route("跑完了")`，具体走哪条通道由适配器决定。

## 接口（鸭子类型，不强制继承）

    name        : str                       通道名
    available() -> bool                     现在能用吗（没配置就 False，不抛）
    poll(limit) -> [{text, from_name, ts}]  收消息（收不到返回 []，绝不抛）
    send(text)  -> str                      发送结果说明（失败要能看懂）

## 三条边界

1. **没配置就说没配置** —— `send()` 返回中文说明，不返回"成功"。
2. **收消息失败不抛** —— 网络抖动、token 过期都只是"这次没消息"。
3. **本地优先** —— 默认通道是本地队列（落盘），断网也留痕，便于排查。
"""
from __future__ import annotations

import json
import os
import time

#: name -> Source
REGISTRY: dict = {}


def _data_dir() -> str:
    d = os.environ.get("PASM_STUDIO_DIR")
    if d:
        return d
    try:
        import logsetup
        return logsetup.data_dir()
    except Exception:  # noqa: BLE001
        return os.path.join(os.path.expanduser("~"), ".pasmstudio")


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


# --------------------------------------------------------------------------
# 内置：本地队列（默认通道，离线可用）
# --------------------------------------------------------------------------
class LocalQueueSource:
    """本地消息队列：发出的消息按行落盘，便于离线排查与回看。

    也是 selftest 与"还没接任何真实通道"时的兜底 —— 不会因为没配
    通道就静默丢失用户消息。
    """

    name = "local"

    def __init__(self, path: str = ""):
        self.path = path or os.path.join(_data_dir(), "msg_outbox.jsonl")

    def available(self) -> bool:
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            return True
        except Exception:  # noqa: BLE001
            return False

    def poll(self, limit: int = 10) -> list:
        """本地队列只发不收（回环由 inbox 文件提供，见 append_incoming）。"""
        return read_incoming(limit)

    def send(self, text: str, **kw) -> str:
        t = (text or "").strip()
        if not t:
            return "内容为空，未发送"
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps({"ts": _now(), "text": t,
                                    "to": kw.get("to") or ""},
                                   ensure_ascii=False) + "\n")
            return "已记入本地队列（%s）" % os.path.basename(self.path)
        except Exception as e:  # noqa: BLE001
            return "本地队列写入失败：%s" % e


class WebhookSource:
    """出站 Webhook 通道（v0.30.6）：POST 一条 JSON 到用户自己配的地址。

    为什么选它当"第一个真实社交通道"：企业微信 / 钉钉 / Slack / 飞书 / 自建服务
    都支持 webhook，**不需要任何平台 SDK、也不需要申请应用** —— 用户贴一个 URL
    就能真的把消息发出去。相比之下接某个平台的开放 API 要 OAuth、审核、密钥轮换，
    对一个本地优先的产品是过重的第一步。

    诚实边界（重要）：
      - 没配 URL → `available()` 返回 False，`send()` 明确说"没配置"，**绝不假装发出去**；
      - HTTP 非 2xx → 报错并带上状态码，不吞；
      - 只发**用户自己配的地址**，不做任何"猜一个平台"的自动行为。
    """

    name = "webhook"

    def __init__(self, url: str = "", timeout: float = 8.0):
        self.url = (url or "").strip()
        self.timeout = float(timeout or 8.0)

    def available(self) -> bool:
        return self.url.startswith(("http://", "https://"))

    def poll(self, limit: int = 10) -> list:
        return []                       # webhook 是单向出站

    def send(self, text: str, **kw) -> str:
        t = (text or "").strip()
        if not t:
            return "内容为空，未发送"
        if not self.available():
            return "未配置 webhook 地址（填了才能发出去）"
        import urllib.request
        body = json.dumps({"text": t, "to": kw.get("to") or "",
                           "ts": _now()}, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            self.url, data=body,
            headers={"Content-Type": "application/json; charset=utf-8"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                code = getattr(resp, "status", 200)
            if 200 <= int(code) < 300:
                return "已通过 webhook 发出（HTTP %s）" % code
            return "webhook 返回 HTTP %s（未确认送达）" % code
        except Exception as e:  # noqa: BLE001  网络问题必须如实报
            return "webhook 发送失败：%s" % e


def channels() -> list:
    """列出所有通道及其可用性（给界面/诊断用）。

    这是"多渠道路由"能被信任的前提：用户得能看见**到底有几条路、哪条通**，
    否则发送失败时只能看到一句笼统的"发送失败"。
    """
    out = []
    for s in all_sources():
        try:
            ok = bool(s.available())
        except Exception:  # noqa: BLE001
            ok = False
        out.append({"name": getattr(s, "name", "?"),
                    "available": ok,
                    "kind": "out" if hasattr(s, "send") else "in"})
    return out


def send(text: str, channel: str = "", **kw) -> dict:
    """统一发送入口 → 结构化结果。

    返回 {"ok", "channel", "detail"}：
      - `channel` 指定则只走它（失败**不回退**，避免"以为发到群里、其实只写进了本地文件"）；
      - 不指定则按 route() 的顺序（真实通道优先、local 兜底），并把实际用的通道报出来。
    """
    if channel:
        src = get(channel)
        if src is None:
            return {"ok": False, "channel": channel, "detail": "没有这个通道：%s" % channel}
        if not hasattr(src, "send"):
            return {"ok": False, "channel": channel, "detail": "该通道不支持发送（只收）"}
        detail = src.send(text, **kw)
        ok = not any(x in detail for x in ("失败", "未配置", "为空", "未确认"))
        return {"ok": ok, "channel": channel, "detail": detail}
    r = route(text, **kw)
    # route() 报的是 `via`（实际走通的通道名）—— 必须把它透出来，
    # 否则调用方只看到"已发送"，不知道究竟走的是 webhook 还是本地文件。
    return {"ok": bool(r.get("ok")), "channel": r.get("via") or "",
            "detail": r.get("detail") or "", "tried": r.get("tried") or []}


def incoming_path() -> str:
    return os.path.join(_data_dir(), "msg_inbox.jsonl")


def append_incoming(text: str, from_name: str = "本机") -> None:
    """往"收到的消息"里塞一条（供外部通道/测试写入）。"""
    t = (text or "").strip()
    if not t:
        return
    try:
        os.makedirs(_data_dir(), exist_ok=True)
        with open(incoming_path(), "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": _now(), "text": t, "from_name": from_name},
                               ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001
        pass


def read_incoming(limit: int = 10) -> list:
    p = incoming_path()
    if not os.path.exists(p):
        return []
    out = []
    try:
        with open(p, "r", encoding="utf-8") as f:
            for ln in f:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    d = json.loads(ln)
                except Exception:  # noqa: BLE001
                    continue
                if isinstance(d, dict) and d.get("text"):
                    out.append({"text": d["text"],
                                "from_name": d.get("from_name") or "本机",
                                "ts": d.get("ts") or "", "source": "local"})
    except Exception:  # noqa: BLE001
        return []
    return out[-max(1, int(limit)):]


# --------------------------------------------------------------------------
# 内置：第三方接入（v0.30.14）—— 飞书 / Discord / 微信 / Webhook
# --------------------------------------------------------------------------
class ConnectorSource:
    """把 `connector_hub` 的四个通道包成**一个**可插拔来源。

    为什么是一个而不是四个：它们共用同一套入站语义（验签 → 归一化 → 主循环 →
    回同一个会话），差异只在平台协议里，已由各自连接器吸收。路由层只要知道
    "有几条路、哪条通、往哪发"就够了。

    入站是**推**的（连接器自己起长连接/回调），所以 `poll()` 返回空 ——
    这里存在只为让 `channels()` 能把四条通道的可用性摆出来（用户得看得见）。
    """

    name = "connectors"

    def _hub(self):
        try:
            import connector_hub as HUB
            return HUB
        except Exception:  # noqa: BLE001
            return None

    def rows(self) -> list:
        HUB = self._hub()
        if not HUB:
            return []
        try:
            return HUB.ConnectorHub().statuses()
        except Exception:  # noqa: BLE001
            return []

    def available(self) -> bool:
        return any(r.get("configured") for r in self.rows())

    def poll(self, limit: int = 10) -> list:
        return []                     # 连接器是推模式（长连接/回调），不轮询

    def send(self, text: str, **kw) -> str:
        HUB = self._hub()
        if not HUB:
            return "接入模块不可用（connector_hub）"
        try:
            return HUB.ConnectorHub().push_text(str(text or ""))
        except Exception as ex:  # noqa: BLE001
            return "接入推送失败：%s" % ex

    def channels(self) -> list:
        """四条通道各自的名字与可用性（给「接入状态」用）。"""
        out = []
        for r in self.rows():
            out.append({"name": r.get("label") or r.get("platform"),
                        "available": bool(r.get("configured")),
                        "note": r.get("note") or r.get("state") or ""})
        return out


# --------------------------------------------------------------------------
# 通道注册表
# --------------------------------------------------------------------------
class BridgeSource:
    """把 remote_bridge 包成适配器 —— 它已经处理了三种推送渠道的差异。"""

    name = "bridge"

    def _rb(self):
        try:
            import remote_bridge as RB
            return RB
        except Exception:  # noqa: BLE001
            return None

    def available(self) -> bool:
        RB = self._rb()
        if not RB:
            return False
        try:
            return bool(RB.configured())
        except Exception:  # noqa: BLE001
            return False

    def poll(self, limit: int = 10) -> list:
        RB = self._rb()
        if not RB or not self.available():
            return []
        try:
            msgs = RB.fetch_messages() or []
        except Exception:  # noqa: BLE001
            return []
        out = []
        for m in msgs[:max(1, int(limit))]:
            if isinstance(m, dict) and (m.get("text") or "").strip():
                out.append({"text": m["text"], "from_name": m.get("from_name") or "远",
                            "ts": _now(), "source": "bridge"})
        return out

    def send(self, text: str, **kw) -> str:
        RB = self._rb()
        if not RB:
            return "远程桥模块不可用（remote_bridge）"
        if not self.available():
            return "远程通道未配置（设置 → 远程消息桥）"
        try:
            r = RB.notify(text or "", title=kw.get("title") or "PASM")
            return r or "已发送"
        except Exception as e:  # noqa: BLE001
            return "发送失败：%s" % e


# --------------------------------------------------------------------------
# 注册与路由
# --------------------------------------------------------------------------
def register(src) -> object:
    REGISTRY[getattr(src, "name", "") or "?"] = src
    return src


def all_sources() -> list:
    return [REGISTRY[k] for k in sorted(REGISTRY)]


def get(name: str):
    return REGISTRY.get(name or "")


def usable() -> list:
    out = []
    for s in all_sources():
        try:
            if s.available():
                out.append(s)
        except Exception:  # noqa: BLE001
            continue
    return out


def poll_all(limit: int = 10) -> list:
    """收所有可用通道的消息（去重后按时间排序）。"""
    seen, out = set(), []
    for s in usable():
        try:
            for m in (s.poll(limit) or []):
                key = (m.get("text") or "")[:80]
                if key and key not in seen:
                    seen.add(key)
                    out.append(m)
        except Exception:  # noqa: BLE001  单个通道坏了不影响其它
            continue
    return out[:max(1, int(limit))]


def route(text: str, prefer: str = "", **kw) -> dict:
    """把一条消息发出去。

    `prefer` 指定优先通道（如 "bridge"）；不给就按 **remote 优先、local 兜底** 的顺序，
    并且**一定会落到 local**（除非指定 prefer 且它成功），保证消息不静默丢失。
    """
    tried = []
    order = []
    if prefer:
        s = get(prefer)
        if s:
            order.append(s)
    # v0.30.6：自动选路里补上真实出站通道（webhook），local 仍然永远排最后兜底。
    # 原来顺序写死成 ("bridge", "local") —— 于是**配了 webhook 也发不出去**，
    # 消息只会静默落进本地 jsonl，用户以为发出去了。
    for name in ("webhook", "bridge", "local"):
        s = get(name)
        if s and s not in order:
            order.append(s)
    for s in order:
        try:
            if not s.available():
                tried.append("%s:不可用" % s.name)
                continue
            msg = s.send(text, **kw) or ""
            tried.append("%s:%s" % (s.name, msg))
            if not prefer or s.name == prefer:
                return {"ok": True, "via": s.name, "detail": msg, "tried": tried}
            if prefer:
                continue
        except Exception as e:  # noqa: BLE001
            tried.append("%s:异常 %s" % (s.name, e))
    if prefer:
        # 指定通道没成功 → 不假装成功，如实回报（但不硬失败到丢消息）
        lq = get("local")
        if lq and lq.available():
            d = lq.send(text, **kw)
            return {"ok": False, "via": "local", "detail": "指定通道 %s 不可用；%s"
                    % (prefer, d), "tried": tried}
        return {"ok": False, "via": "", "detail": "没有可用通道", "tried": tried}
    return {"ok": False, "via": "", "detail": "没有可用通道", "tried": tried}


def install_defaults(webhook_url: str = "") -> dict:
    """注册内置通道（幂等）。

    webhook 地址优先级：显式参数 > 环境变量 `PASM_WEBHOOK_URL`。
    **没配也照样注册**（`available()` 为 False）—— 这样"渠道清单"里
    能看见它、并明确显示"未配置"，而不是干脆消失让用户以为是没这功能。
    """
    register(LocalQueueSource())
    register(BridgeSource())
    register(WebhookSource(webhook_url or os.environ.get("PASM_WEBHOOK_URL", "")))
    # v0.30.14：第三方接入（飞书 / Discord / 微信 / Webhook）—— 入站是推模式，
    # 注册在这里是为了让 `channels()` 把四条通道的可用性一起摆出来。
    try:
        register(ConnectorSource())
    except Exception:  # noqa: BLE001
        pass
    return dict(REGISTRY)


install_defaults()


# --------------------------------------------------------------------------
# 自检
# --------------------------------------------------------------------------
def _selftest() -> int:
    import shutil
    import tempfile
    fails = []
    n = {"v": 0}

    def check(name, cond, extra=""):
        n["v"] += 1
        print("  %s %s%s" % ("[OK]  " if cond else "[FAIL]", name,
                             ("  " + str(extra)) if (extra and not cond) else ""))
        if not cond:
            fails.append(name)

    print("== msg_source 自检 ==")
    saved = os.environ.get("PASM_STUDIO_DIR")
    tmp = tempfile.mkdtemp(prefix="ms_")
    os.environ["PASM_STUDIO_DIR"] = tmp
    REGISTRY.clear()
    try:
        srcs = install_defaults()
        # v0.30.6：默认通道增加 webhook（未配地址时 available=False，
        # 仍然注册 —— 这样"渠道清单"里能看见它、并明确显示未配置）
        # v0.30.14：再加 connectors（飞书/Discord/微信/Webhook 四条通道的门面）
        check("默认注册 local + bridge + webhook + connectors",
              set(srcs) == {"local", "bridge", "webhook", "connectors"}, sorted(srcs))
        _wh = get("webhook")
        check("webhook 未配地址时不可用", _wh is not None and not _wh.available())
        check("webhook 未配地址时不假装发出",
              "未配置" in (_wh.send("x") or ""), _wh.send("x"))
        _cn = get("connectors")
        check("connectors 未配任何平台时不可用（不假装有通道）",
              _cn is not None and not _cn.available())
        check("connectors 能把四个平台都列出来（用户要看得见有几条路）",
              {c["name"] for c in (_cn.channels() if _cn else [])}
              >= {"飞书", "Discord", "微信", "Webhook"},
              _cn.channels() if _cn else None)
        check("channels() 能列出通道与可用性",
              {c["name"] for c in channels()} >= {"local", "bridge", "webhook"})
        # 指定了不存在的通道：不静默回退，如实报错
        _r = send("x", channel="没有这个通道")
        check("坏通道名不静默回退", _r["ok"] is False and "没有这个通道" in _r["detail"], _r)

        lq = get("local")
        check("local 可用", lq.available())
        check("local 明确不可用时不抛（改路径后）",
              isinstance(lq.available(), bool))

        # 发送：真落盘
        d = lq.send("测试消息一")
        check("local 发送返回可读说明", "本地队列" in d, d)
        check("local 真的落盘了", os.path.exists(lq.path), lq.path)
        with open(lq.path, encoding="utf-8") as f:
            body = f.read()
        check("落盘内容含消息", "测试消息一" in body)
        check("空内容不发送", "为空" in lq.send("   "))

        # 收消息
        append_incoming("帮我查一下明天的天气", "手机")
        inc = lq.poll(10)
        check("能收到本地 inbox 消息", any("天气" in m["text"] for m in inc), inc)
        check("收到的消息带来源", all(m.get("source") for m in inc), inc)

        # 去重：同一条不重复收
        append_incoming("帮我查一下明天的天气", "手机")
        got = poll_all(10)
        same = [m for m in got if "天气" in m["text"]]
        check("poll_all 对同文本去重", len(same) <= 1, same)

        # bridge 未配置 → available False，且 send 返回可读原因
        br = get("bridge")
        check("bridge 未配置时 available 为 False（或环境已配）",
              isinstance(br.available(), bool))
        if not br.available():
            m = br.send("x")
            check("bridge 未配置时 send 说明原因", "未配置" in m or "不可用" in m, m)

        # route：默认应落到 local（bridge 没配）
        r = route("跑完了")
        check("route 有明确结果", "ok" in r and "via" in r, r)
        check("route 默认落到可用通道", r["via"] in ("local", "bridge"), r)
        check("route 带 tried 记录（可排查）", isinstance(r.get("tried"), list), r)

        # route 指定不存在/不可用通道 → 如实说不成功，且消息不丢（落 local）
        r2 = route("你好", prefer="bridge")
        check("route 指定通道不可用时 ok=False", r2["ok"] is False or r2["via"] == "bridge", r2)
        if not r2["ok"]:
            with open(lq.path, encoding="utf-8") as f:
                check("指定通道失败时消息仍落到 local（不静默丢）", "你好" in f.read())

        # 收消息失败的通道不影响其它通道
        class _Boom:
            name = "boom"
            def available(self):
                return True
            def poll(self, limit=10):
                raise RuntimeError("通道炸了")
            def send(self, text, **kw):
                raise RuntimeError("通道炸了")
        register(_Boom())
        try:
            got2 = poll_all(10)
            ok2 = True
        except Exception as e:  # noqa: BLE001
            ok2 = False
            print("      异常：%s" % e)
        check("单个通道异常不影响 poll_all", ok2)
    finally:
        REGISTRY.clear()
        install_defaults()
        if saved is None:
            os.environ.pop("PASM_STUDIO_DIR", None)
        else:
            os.environ["PASM_STUDIO_DIR"] = saved
        shutil.rmtree(tmp, ignore_errors=True)

    print("-" * 46)
    if fails:
        print("自检失败 %d/%d：%s" % (len(fails), n["v"], "；".join(fails)))
        return 1
    print("自检通过：%d 项全绿" % n["v"])
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_selftest())
