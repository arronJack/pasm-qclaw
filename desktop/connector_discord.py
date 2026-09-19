# -*- coding: utf-8 -*-
"""connector_discord.py —— Discord 接入（v0.30.14）

对标 BaiLongma 的 `src/social/discord.js`（它用 `ws` + Node，这里用标准库）：
- **出站**：Bot Token → `POST /channels/{id}/messages`
- **入站**：Discord **Gateway WebSocket** ——
  `GET /gateway/bot` 拿地址 → `HELLO(op10)` → `IDENTIFY(op2)` → 心跳(op1/op11)
  → `MESSAGE_CREATE` 派发 → 断了按指数退避重连（可选 RESUME）。

## 为什么能纯标准库
Discord 网关**不强制压缩**：网关地址不带 `compress=zlib-stream` 时，帧里就是
普通 JSON 文本。所以我们只要一个 WebSocket 客户端（`wsclient.py`，自研）+ `json` 就够。

## 诚实边界
- **没有与真实 Discord 联调过**（需要真 Bot Token）。`MESSAGE_CONTENT` 是**特权 intent**，
  必须在开发者后台勾选，否则收到的 `content` 是空的 —— 这时我们**如实提示**，
  不假装"收到空消息"。界面给了「🔎 测试连接」用你自己的 token 真连一次。
- 只处理**文本**；附件/表情/嵌入内容不对应文本时如实说明。
- 单条上限 2000 字符 —— 超了**主动分段**发（不是截断）。
- 不实现分片（shard）多连接（个人用不到；大群才需要）。
"""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable, Optional

try:
    from pasm_companion import CONFIG, DATA_DIR
except Exception:                                            # noqa: BLE001
    DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "..", ".pasmstudio_dev")
    CONFIG = os.path.join(DATA_DIR, "config.json")

API = "https://discord.com/api/v10"
TIMEOUT = 15
UA = "PASM-Studio/1.0 (+connector-discord)"
MAX_MSG = 2000
CHUNK = 1900

#: 只申请真正需要的 intent：
#:   GUILDS(1) 取频道信息 / GUILD_MESSAGES(512) 服务器消息 /
#:   DIRECT_MESSAGES(4096) 私聊 / MESSAGE_CONTENT(32768) 读正文（**特权，要后台勾选**）
INTENTS = 1 | 512 | 4096 | 32768

OP_DISPATCH, OP_HEARTBEAT, OP_IDENTIFY, OP_RESUME = 0, 1, 2, 6
OP_RECONNECT, OP_INVALID_SESSION, OP_HELLO, OP_ACK = 7, 9, 10, 11


# --------------------------------------------------------------------------
# 配置
# --------------------------------------------------------------------------
def load_cfg() -> dict:
    try:
        with open(CONFIG, "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:                                        # noqa: BLE001
        return {}


def settings(cfg: Optional[dict] = None) -> dict:
    c = cfg if isinstance(cfg, dict) else load_cfg()
    allow = c.get("discord_allow_channels")
    if isinstance(allow, str):
        allow = [x.strip() for x in allow.replace("，", ",").split(",") if x.strip()]
    return {
        "token": str(c.get("discord_bot_token") or "").strip(),
        "default_channel": str(c.get("discord_default_channel") or "").strip(),
        "allow": [str(x) for x in (allow or []) if str(x).strip()],
        "mode": str(c.get("discord_mode") or "off").strip().lower(),   # off|gateway
    }


def configured(cfg: Optional[dict] = None) -> bool:
    return bool(settings(cfg)["token"])


def mask(secret: str, keep: int = 6) -> str:
    v = str(secret or "")
    if not v:
        return ""
    if len(v) <= keep + 2:
        return "*" * len(v)
    return v[:keep] + "*" * max(4, len(v) - keep - 2) + v[-2:]


# --------------------------------------------------------------------------
# 出网
# --------------------------------------------------------------------------
def _api(method: str, path: str, payload=None, token: str = "") -> dict:
    h = {"User-Agent": UA, "Authorization": "Bot " + (token or ""),
         "Content-Type": "application/json"}
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload else None
    req = urllib.request.Request(API + path, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            body = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as ex:
        detail = ""
        try:
            detail = ex.read().decode("utf-8", "replace")[:300]
        except Exception:                                    # noqa: BLE001
            pass
        if ex.code == 401:
            raise RuntimeError("Discord 拒绝了 Bot Token（401）—— token 不对或已被重置。")
        if ex.code == 403:
            raise RuntimeError("Discord 拒绝访问（403）：机器人不在该频道，或缺权限。%s"
                               % detail[:120])
        raise RuntimeError("Discord 返回 HTTP %s：%s" % (ex.code, detail or ex.reason))
    except urllib.error.URLError as ex:
        raise RuntimeError("连不上 Discord（%s）—— 国内网络通常需要代理。" % ex.reason)
    try:
        return json.loads(body or "{}")
    except Exception:                                        # noqa: BLE001
        return {}


def chunk_text(text: str, limit: int = CHUNK) -> list:
    """按 Discord 上限分段（**分段**，不是截断 —— 内容不许丢）。"""
    t = str(text or "")
    if len(t) <= limit:
        return [t] if t else []
    out, cur = [], ""
    for line in t.splitlines(True):
        while len(line) > limit:                             # 单行超长也要切
            out.append(line[:limit])
            line = line[limit:]
        if len(cur) + len(line) > limit:
            out.append(cur)
            cur = line
        else:
            cur += line
    if cur:
        out.append(cur)
    return out


def send_text(channel_id: str, text: str, cfg: Optional[dict] = None,
              reply_to: str = "") -> dict:
    """往频道发消息（可带回复引用）。超长自动分段；返回 {ok, ids, chunks}。"""
    s = settings(cfg)
    if not configured(cfg):
        raise RuntimeError("还没配 Discord Bot Token（设置 → 🔗 接入）。")
    cid = (channel_id or s["default_channel"] or "").strip()
    if not cid:
        raise RuntimeError("没给频道 ID，也没配「默认频道」。")
    parts = chunk_text(text)
    if not parts:
        raise RuntimeError("要发的内容是空的。")
    ids = []
    for i, part in enumerate(parts):
        body = {"content": part}
        if reply_to and i == 0:
            # 第一段挂回复引用，后面的段就接在后面，避免每段都引用一遍
            body["message_reference"] = {"message_id": str(reply_to)}
            body["allowed_mentions"] = {"replied_user": False}
        d = _api("POST", "/channels/%s/messages" % urllib.parse.quote(cid),
                 body, s["token"])
        if d.get("id"):
            ids.append(str(d["id"]))
    if not ids:
        raise RuntimeError("Discord 没有返回消息 id（发送可能没成功）。")
    return {"ok": True, "ids": ids, "chunks": len(parts)}


# --------------------------------------------------------------------------
# 入站：Gateway 协议（**纯函数**，可离线自检）
# --------------------------------------------------------------------------
def parse_message_create(d: dict) -> Optional[dict]:
    """`MESSAGE_CREATE` → 统一入站格式；机器人/空消息返回 None。

    返回 {channel_id, user_id, user_name, text, message_id, kind, guild_id}
    —— 与飞书/微信/Webhook 的入站格式一致。
    """
    if not isinstance(d, dict):
        return None
    author = d.get("author") or {}
    if author.get("bot"):                                    # 别的机器人
        return None
    content = str(d.get("content") or "").strip()
    out = {
        "channel_id": str(d.get("channel_id") or ""),
        "guild_id": str(d.get("guild_id") or ""),
        "user_id": str(author.get("id") or ""),
        "user_name": str(author.get("username") or ""),
        "message_id": str(d.get("id") or ""),
        "kind": "text" if content else "non-text",
        "text": content,
    }
    if not content:
        # MESSAGE_CONTENT 是特权 intent：没勾选时正文就是空的 —— 必须能区分
        # "对方真发了空消息" 与 "我们读不到正文"，否则会误判成"用户没说话"
        att = d.get("attachments") or []
        out["reason"] = ("读不到正文：可能是没勾选 MESSAGE_CONTENT 特权 intent"
                         if att else "对方这条没有正文")
        return out
    return out


def plan_actions(payload: dict, state: dict) -> list:
    """把一条网关消息翻译成**要做的事**（不改状态以外的东西，便于离线自检）。

    state 里维护：seq / session_id / heartbeat_ok
    返回动作列表，元素形如 {"heartbeat": <ms 或 None>} / {"identify": True} /
    {"resume": True} / {"ack": True} / {"reconnect": True} / {"ready": {...}} /
    {"event": <msg>} / {"error": "..."}
    """
    if not isinstance(payload, dict):
        return []
    op = payload.get("op")
    if payload.get("s") is not None:
        state["seq"] = payload.get("s")
    if op == OP_HELLO:
        ms = int((payload.get("d") or {}).get("heartbeat_interval") or 41250)
        state["heartbeat_ms"] = ms
        actions = [{"heartbeat": ms}]
        if state.get("session_id"):
            actions.append({"resume": True})
        else:
            actions.append({"identify": True})
        return actions
    if op == OP_ACK:
        state["heartbeat_ok"] = True
        return [{"ack": True}]
    if op == OP_HEARTBEAT:                                   # 服务端要求立刻心跳
        return [{"heartbeat": None}]
    if op == OP_RECONNECT:
        return [{"reconnect": True}]
    if op == OP_INVALID_SESSION:
        can_resume = bool(payload.get("d"))
        if not can_resume:
            state["session_id"] = ""
        return [{"resume": True} if can_resume else {"identify": True}]
    if op == OP_DISPATCH:
        t = str(payload.get("t") or "")
        d = payload.get("d") or {}
        if t == "READY":
            state["session_id"] = str(d.get("session_id") or "")
            state["bot_id"] = str(((d.get("user") or {}).get("id")) or "")
            return [{"ready": {"session_id": state["session_id"],
                               "bot": (d.get("user") or {}).get("username", ""),
                               "resume_gateway_url": d.get("resume_gateway_url", "")}}]
        if t == "MESSAGE_CREATE":
            if state.get("bot_id") and str((d.get("author") or {}).get("id")) == \
                    state["bot_id"]:
                return []                                    # 自己发的，别自问自答
            msg = parse_message_create(d)
            return [{"event": msg}] if msg else []
        return []
    return []


class DiscordGateway:
    """Discord 网关连接（后台线程）。status: idle/connecting/connected/reconnecting/error"""

    def __init__(self, cfg: Optional[dict] = None,
                 on_event: Optional[Callable[[dict], None]] = None,
                 on_log: Optional[Callable[[str], None]] = None):
        self.cfg = cfg
        self.on_event = on_event
        self.on_log = on_log
        self.status = "idle"
        self.last_error = ""
        self.events_in = 0
        self.state = {"seq": None, "session_id": "", "heartbeat_ok": True}
        self._stop = threading.Event()
        self._th: Optional[threading.Thread] = None
        self._ws = None

    def _log(self, m: str) -> None:
        if self.on_log:
            try:
                self.on_log(m)
            except Exception:                                # noqa: BLE001
                pass

    def start(self) -> bool:
        if not configured(self.cfg):
            self._log("Discord Bot Token 没配，跳过")
            return False
        if self._th is not None and self._th.is_alive():
            return True
        self._stop.clear()
        self._th = threading.Thread(target=self._run, name="pasm-discord-gw", daemon=True)
        self._th.start()
        return True

    def stop(self) -> None:
        self._stop.set()
        try:
            if self._ws is not None:
                self._ws.close()
        except Exception:                                    # noqa: BLE001
            pass
        self.status = "idle"

    def _gateway_url(self) -> str:
        s = settings(self.cfg)
        d = _api("GET", "/gateway/bot", None, s["token"])
        url = str(d.get("url") or "")
        if not url:
            raise RuntimeError("拿不到网关地址：%s" % (d.get("message") or d))
        sep = "&" if "?" in url else "?"
        # encoding=json 且**不请求压缩** —— 这样帧里就是明文 JSON（省掉 zlib 依赖）
        return "%s%sv=10&encoding=json" % (url, sep)

    def _run(self) -> None:
        import wsclient
        backoff, attempt = 1.0, 0
        while not self._stop.is_set():
            self.status = "connecting" if attempt == 0 else "reconnecting"
            try:
                url = self._gateway_url()
                self._ws = wsclient.WSClient(url, headers={"User-Agent": UA})
                self._ws.connect(timeout=15)
                self.status = "connected"
                attempt = 0
                backoff = 1.0
                hb_thread, hb_stop = None, {"stop": False}
                hello_wait = time.time() + 20

                def _heartbeat(ms: int):
                    nonlocal hb_thread
                    hb_stop["stop"] = False

                    def _loop():
                        while not hb_stop["stop"] and not self._stop.is_set():
                            if self._stop.wait(max(1.0, ms / 1000.0)):
                                return
                            try:
                                if not self.state.get("heartbeat_ok", True):
                                    self._log("Discord 心跳没被确认 → 重连")
                                    try:
                                        self._ws.close()
                                    except Exception:        # noqa: BLE001
                                        pass
                                    return
                                self.state["heartbeat_ok"] = False
                                self._ws.send_text(json.dumps(
                                    {"op": OP_HEARTBEAT, "d": self.state.get("seq")}))
                            except Exception:                # noqa: BLE001
                                return

                    hb_thread = threading.Thread(target=_loop, name="pasm-dc-hb",
                                                 daemon=True)
                    hb_thread.start()

                while not self._stop.is_set():
                    frame = self._ws.recv(timeout=2)
                    if frame is None:
                        if time.time() > hello_wait and self.status != "connected":
                            raise RuntimeError("连上网关但 20 秒没收到 HELLO")
                        continue
                    if frame.get("opcode") != 1:
                        continue
                    try:
                        payload = json.loads(frame["data"].decode("utf-8", "replace"))
                    except Exception:                        # noqa: BLE001
                        continue
                    for act in plan_actions(payload, self.state):
                        if "heartbeat" in act:
                            ms = act["heartbeat"] or self.state.get("heartbeat_ms") or 41250
                            if hb_thread is None:
                                _heartbeat(int(ms))
                            else:
                                try:
                                    self._ws.send_text(json.dumps(
                                        {"op": OP_HEARTBEAT, "d": self.state.get("seq")}))
                                except Exception:            # noqa: BLE001
                                    pass
                        elif "identify" in act:
                            self.state["heartbeat_ok"] = True
                            self._ws.send_text(json.dumps({
                                "op": OP_IDENTIFY,
                                "d": {"token": settings(self.cfg)["token"],
                                      "intents": INTENTS,
                                      "properties": {"os": "windows",
                                                     "browser": "pasm-studio",
                                                     "device": "pasm-studio"}}}))
                        elif "resume" in act:
                            self.state["heartbeat_ok"] = True
                            self._ws.send_text(json.dumps({
                                "op": OP_RESUME,
                                "d": {"token": settings(self.cfg)["token"],
                                      "session_id": self.state.get("session_id"),
                                      "seq": self.state.get("seq")}}))
                        elif "ready" in act:
                            info = act["ready"]
                            self._log("Discord 网关就绪（%s）" % info.get("bot") or "")
                        elif "reconnect" in act:
                            raise RuntimeError("Discord 要求重连（op7）")
                        elif "event" in act and act["event"]:
                            ev = act["event"]
                            if self._allow(ev.get("channel_id") or ""):
                                self.events_in += 1
                                if self.on_event:
                                    try:
                                        self.on_event(ev)
                                    except Exception as ex:  # noqa: BLE001
                                        self._log("处理 Discord 消息失败：%s" % ex)
                hb_stop["stop"] = True
            except Exception as ex:                          # noqa: BLE001
                self.status, self.last_error = "reconnecting", str(ex)
                self._log("Discord 网关断开：%s" % ex)
            finally:
                try:
                    if self._ws is not None:
                        self._ws.close()
                except Exception:                            # noqa: BLE001
                    pass
                self._ws = None
            if self._stop.is_set():
                return
            attempt += 1
            if self._stop.wait(backoff):
                return
            backoff = min(backoff * 2, 60.0)                 # 指数退避，封顶 60s

    def _allow(self, channel_id: str) -> bool:
        allow = settings(self.cfg)["allow"]
        return (not allow) or (str(channel_id) in allow)


# --------------------------------------------------------------------------
# 桥：入站 → handler → 回话
# --------------------------------------------------------------------------
class DiscordBridge:
    def __init__(self, cfg: Optional[dict] = None,
                 handler: Optional[Callable[[str], str]] = None,
                 on_log: Optional[Callable[[str], None]] = None):
        self.cfg = cfg
        self.handler = handler
        self.on_log = on_log
        self.gw: Optional[DiscordGateway] = None
        self.handled = 0
        self.replied = 0

    def _log(self, m: str) -> None:
        if self.on_log:
            try:
                self.on_log(m)
            except Exception:                                # noqa: BLE001
                pass

    def status(self) -> str:
        return self.gw.status if self.gw is not None else "idle"

    def start(self) -> bool:
        if settings(self.cfg)["mode"] != "gateway":
            return False
        self.gw = DiscordGateway(self.cfg, on_event=self._on_msg, on_log=self.on_log)
        return self.gw.start()

    def stop(self) -> None:
        if self.gw is not None:
            self.gw.stop()

    def _on_msg(self, msg: dict) -> None:
        text = str(msg.get("text") or "").strip()
        if not text:
            self._log("收到 Discord 非文本消息（%s）" % (msg.get("reason") or "无正文"))
            return
        self.handled += 1
        self._log("📲 收到 Discord 消息：%s" % text[:60])
        ans = ""
        try:
            ans = (self.handler(text) if self.handler else "") or ""
        except Exception as ex:                              # noqa: BLE001
            ans = "处理失败：%s" % ex
        if not ans:
            ans = "（我处理完了，但没生成内容）"
        try:
            send_text(msg.get("channel_id") or "", ans, cfg=self.cfg,
                      reply_to=msg.get("message_id") or "")
            self.replied += 1
        except Exception as ex:                              # noqa: BLE001
            self._log("回复 Discord 失败：%s" % ex)


# --------------------------------------------------------------------------
# 状态 / 测试
# --------------------------------------------------------------------------
def status_text(cfg: Optional[dict] = None) -> str:
    s = settings(cfg)
    lines = ["🔗 **Discord 接入状态**"]
    if not configured(cfg):
        return "\n".join(lines + ["· Bot Token：**未配置**（开发者后台 → Bot → Reset Token）"])
    lines.append("· Bot Token：`%s`" % mask(s["token"]))
    lines.append("· 接收方式：%s" % ("网关长连接" if s["mode"] == "gateway" else "关闭（只出站）"))
    if s["default_channel"]:
        lines.append("· 默认频道：`%s`" % s["default_channel"])
    if s["allow"]:
        lines.append("· 只响应这些频道：%s" % "、".join("`%s`" % x for x in s["allow"]))
    lines.append("· ⚠️ 要读消息**正文**必须在开发者后台勾选 MESSAGE_CONTENT 特权 intent；"
                 "没勾时收到的是空正文（我会如实说，不会假装你发了空消息）")
    return "\n".join(lines)


def test_connection(cfg: Optional[dict] = None) -> str:
    """真打一次 Discord API（`GET /users/@me`）—— 只有真连上才算通过。"""
    s = settings(cfg)
    if not configured(cfg):
        return "❌ 还没配 Discord Bot Token（设置 → 🔗 接入）。"
    try:
        me = _api("GET", "/users/@me", None, s["token"])
        name = me.get("username") or "?"
        gw = _api("GET", "/gateway/bot", None, s["token"])
        return ("① 校验 Bot Token：✅ 成功（我是 %s#%s）\n"
                "② 取网关地址：✅ 成功（建议分片 %s）"
                % (name, me.get("discriminator") or "0", gw.get("shards") or 1))
    except Exception as ex:                                  # noqa: BLE001
        return "❌ %s" % ex


# --------------------------------------------------------------------------
# 自检
# --------------------------------------------------------------------------
def selftest() -> int:
    fails, ok = [], 0

    def chk(label, cond, extra=""):
        nonlocal ok
        if cond:
            ok += 1
        else:
            fails.append("%s %s" % (label, extra))

    # ① 配置与掩码
    chk("掩码不露真值", mask("MTIzNDU2Nzg5MA==") == "MTIzND********==",
        mask("MTIzNDU2Nzg5MA=="))
    chk("掩码长度与原值一致（不让人从长度倒推）",
        len(mask("MTIzNDU2Nzg5MA==")) == len("MTIzNDU2Nzg5MA=="))
    chk("没配 token → configured False", not configured({}))
    chk("配了 token → configured True", configured({"discord_bot_token": "x"}))
    chk("频道白名单支持中文逗号",
        settings({"discord_allow_channels": "a，b , c"})["allow"] == ["a", "b", "c"])
    try:
        send_text("1", "hi", cfg={})
        chk("没配 token 时发送要报错（不假装成功）", False)
    except RuntimeError as ex:
        chk("没配 token 时发送要报错（不假装成功）", "Bot Token" in str(ex), str(ex))

    # ② 分段（**不许截断**）
    parts = chunk_text("x" * 4500)
    chk("超长内容被分段而不是截断", len(parts) == 3 and sum(len(p) for p in parts) == 4500,
        [len(p) for p in parts])
    chk("每段不超上限", all(len(p) <= CHUNK for p in parts))
    chk("多行内容按行切", chunk_text("a\n" * 2000)[0].count("\n") > 0)
    chk("空内容 → 不分段", chunk_text("") == [])

    # ③ MESSAGE_CREATE 解析
    d = {"id": "m1", "channel_id": "c1", "guild_id": "g1",
         "author": {"id": "u1", "username": "小志", "bot": False},
         "content": "帮我出一张海报"}
    m = parse_message_create(d)
    chk("正文抽到", m and m["text"] == "帮我出一张海报", m)
    chk("频道/用户/消息 id 抽到",
        m and (m["channel_id"], m["user_id"], m["message_id"]) == ("c1", "u1", "m1"))
    chk("别的机器人发的 → 忽略",
        parse_message_create(dict(d, author={"id": "b", "bot": True})) is None)
    nb = parse_message_create({"id": "m2", "channel_id": "c1",
                               "author": {"id": "u1"}, "content": "",
                               "attachments": [{"id": "a"}]})
    chk("读不到正文时**说明原因**（不假装是空消息）",
        nb and nb["kind"] == "non-text" and "MESSAGE_CONTENT" in nb["reason"], nb)

    # ④ 网关协议动作（离线，真判据）
    st = {"seq": None, "session_id": "", "heartbeat_ok": True}
    a = plan_actions({"op": OP_HELLO, "d": {"heartbeat_interval": 41250}}, st)
    chk("HELLO → 起心跳 + IDENTIFY",
        any("heartbeat" in x for x in a) and any("identify" in x for x in a), a)
    chk("心跳间隔被记下", st["heartbeat_ms"] == 41250)
    a2 = plan_actions({"op": OP_ACK, "s": 5}, st)
    chk("ACK → 标记心跳已被确认", st["heartbeat_ok"] is True and a2[0].get("ack") is True)
    chk("seq 被更新", st["seq"] == 5)
    a3 = plan_actions({"op": OP_DISPATCH, "t": "READY",
                       "d": {"session_id": "S1", "user": {"id": "bot9", "username": "pasm"},
                             "resume_gateway_url": "wss://r"}}, st)
    chk("READY → 记 session_id（用于 RESUME）",
        st["session_id"] == "S1" and a3[0]["ready"]["session_id"] == "S1")
    st["session_id"] = "S1"
    a4 = plan_actions({"op": OP_HELLO, "d": {"heartbeat_interval": 1000}}, st)
    chk("重连后 HELLO → 走 RESUME 而不是重新 IDENTIFY",
        any("resume" in x for x in a4) and not any("identify" in x for x in a4), a4)
    a5 = plan_actions({"op": OP_DISPATCH, "t": "MESSAGE_CREATE",
                       "d": {"id": "m3", "channel_id": "c1",
                             "author": {"id": "bot9"}, "content": "自己说的话"}}, st)
    chk("自己发的消息不回（防自问自答）", a5 == [], a5)
    a6 = plan_actions({"op": OP_INVALID_SESSION, "d": False}, st)
    chk("INVALID_SESSION(false) → 重新 IDENTIFY",
        a6[0].get("identify") is True and st["session_id"] == "", (a6, st))
    chk("op7(RECONNECT) → 要求重连",
        plan_actions({"op": OP_RECONNECT}, st)[0].get("reconnect") is True)
    chk("服务端 ping(op1) → 立刻回心跳",
        plan_actions({"op": OP_HEARTBEAT}, st)[0].get("heartbeat", 1) is None)
    chk("不认识的 op 不炸也不误触发",
        plan_actions({"op": 99, "d": {}}, st) == [])

    # ⑤ 白名单
    class _G(DiscordGateway):
        pass
    g = DiscordGateway({"discord_bot_token": "t", "discord_allow_channels": "c1, c2"})
    chk("白名单内放行", g._allow("c1"))
    chk("白名单外拦住（反例）", not g._allow("c9"))
    g2 = DiscordGateway({"discord_bot_token": "t"})
    chk("没配白名单 → 全放行", g2._allow("any"))

    # ⑥ 状态文案
    chk("没配时状态文案点名缺什么", "未配置" in status_text({}))
    chk("配了时状态文案提醒特权 intent", "MESSAGE_CONTENT" in status_text(
        {"discord_bot_token": "MTIzNDU2Nzg5MA==", "discord_mode": "gateway"}))

    if fails:
        print("connector_discord 自检失败 %d 项：" % len(fails))
        for f in fails:
            print("  ✗", f)
        return 1
    print("connector_discord 自检通过（%d 项）" % ok)
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(selftest())
