# -*- coding: utf-8 -*-
"""connector_feishu.py —— 飞书 / Lark 接入（v0.30.14）

对标 BaiLongma 的 `src/social/feishu-ws.js`，但**不移植它的 Node 壳与重依赖**：
这里纯标准库（urllib / socket / ssl / json / hashlib），安装包不变大。

## 四个能力（以前 PASM 只有第 1 个）

| # | 能力 | 以前 | 现在 |
|---|---|---|---|
| 1 | **群机器人单向推送**（自定义机器人 Webhook） | ✅（在 `remote_bridge`） | ✅ 保留 |
| 2 | **应用出站**：App ID/Secret → tenant_access_token → 给会话/用户发消息 | ❌ | ✅ `send_text` / `reply_text` |
| 3 | **事件回调入站**：飞书把事件推到你的 HTTPS 地址 | ❌ | ✅ `FeishuCallbackServer`（自带 challenge / 签名校验） |
| 4 | **长连接入站**（家里没公网也能用，BaiLongma 的关键优势） | ❌ | ✅ `FeishuLongConn` |

入站消息统一交给 `FeishuBridge`：去重 → `handler(text)` → 把结果**回给同一个会话**，
与桌面端走同一条管线（对齐 BaiLongma「消息统一进主循环 + 回复路由」）。

## 诚实边界（很重要，别当成已联调）

- **长连接的 protobuf 帧编解码是按官方 SDK 行为复刻的**，本地有假服务器往返测试，
  但**没有与真实飞书租户联调过**（需要真实 App 凭据 + 后台订阅配置）。界面上给了
  「🔎 测试连接」——用你自己的凭据真连一次，成不成会如实报。
- **加密推送（Encrypt Key）不支持**：飞书一旦开了事件加密，所有请求体都是
  AES-256-CBC 密文，标准库解不了。请在飞书后台**把 Encrypt Key 留空**
  （BaiLongma 的文档也写着「不要开加密推送」）。收到 `encrypt` 字段会**如实报错**而不是静默丢。
- 机器人**不能主动私聊"从未联系过它"的用户**（拿不到 open_id）——主动推送请用群 `chat_id`。
- 消息类型只处理**文本**；图片/文件/富文本会如实说明"暂不支持"，不假装收到。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import struct
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Optional

import connector_http as CH            # 共用的本地入站端点（只绑 127.0.0.1 + 路径白名单）

try:
    from pasm_companion import CONFIG, DATA_DIR
except Exception:                                            # noqa: BLE001
    DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "..", ".pasmstudio_dev")
    CONFIG = os.path.join(DATA_DIR, "config.json")

TIMEOUT = 15
UA = "PASM-Studio/1.0 (+connector-feishu)"

#: 飞书（中国）默认；Lark（国际）/自建代理可用 feishu_domain 覆盖
DOMAINS = {
    "feishu": "https://open.feishu.cn",
    "lark": "https://open.larksuite.com",
}

_TOKEN_LOCK = threading.Lock()
_TOKEN_CACHE = {"token": "", "expire_at": 0.0}
_SEEN_LOCK = threading.Lock()
_SEEN_IDS = []                       # 最近处理过的事件 id（去重，防飞书重推）
_SEEN_MAX = 500


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
    dom = str(c.get("feishu_domain") or "feishu").strip().lower()
    return {
        "app_id": str(c.get("feishu_app_id") or "").strip(),
        "app_secret": str(c.get("feishu_app_secret") or "").strip(),
        "verify_token": str(c.get("feishu_verify_token") or "").strip(),
        "encrypt_key": str(c.get("feishu_encrypt_key") or "").strip(),
        "domain": DOMAINS.get(dom, dom if dom.startswith("http") else DOMAINS["feishu"]),
        "mode": str(c.get("feishu_mode") or "off").strip().lower(),   # off|longconn|callback
        "port": int(c.get("feishu_callback_port") or 8797),
        "default_chat": str(c.get("feishu_default_chat") or "").strip(),
        "bot_webhook": str(c.get("bridge_webhook_url") or "").strip(),
    }


def configured(cfg: Optional[dict] = None) -> bool:
    """够不够"应用级"接入（出站 + 入站都靠它）。群机器人 webhook 不算。"""
    s = settings(cfg)
    return bool(s["app_id"] and s["app_secret"])


def mask(secret: str, keep: int = 4) -> str:
    """给界面看的掩码 —— 只露头尾，永不回显真值。"""
    v = str(secret or "")
    if not v:
        return ""
    if len(v) <= keep + 2:
        return "*" * len(v)
    return v[:keep] + "*" * max(4, len(v) - keep - 2) + v[-2:]


# --------------------------------------------------------------------------
# 出网
# --------------------------------------------------------------------------
def _request(url: str, payload=None, method: str = "POST",
             headers: Optional[dict] = None, form: bool = False) -> dict:
    h = {"User-Agent": UA}
    h.update(headers or {})
    data = None
    if payload is not None:
        if form:
            data = urllib.parse.urlencode(payload).encode("utf-8")
            h["Content-Type"] = "application/x-www-form-urlencoded"
        else:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            h["Content-Type"] = "application/json; charset=utf-8"
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            body = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as ex:
        detail = ""
        try:
            detail = ex.read().decode("utf-8", "replace")[:300]
        except Exception:                                    # noqa: BLE001
            pass
        raise RuntimeError("飞书返回 HTTP %s：%s" % (ex.code, detail or ex.reason))
    except urllib.error.URLError as ex:
        raise RuntimeError("连不上飞书（%s）—— 检查网络或代理。" % ex.reason)
    try:
        return json.loads(body or "{}")
    except Exception:                                        # noqa: BLE001
        raise RuntimeError("飞书返回的不是 JSON：%s" % body[:200])


# --------------------------------------------------------------------------
# ① 应用出站
# --------------------------------------------------------------------------
def get_token(cfg: Optional[dict] = None, force: bool = False) -> str:
    """tenant_access_token（带缓存与刷新）。没有凭据时抛错，不返回空串。"""
    s = settings(cfg)
    if not (s["app_id"] and s["app_secret"]):
        raise RuntimeError("还没配飞书 App ID / App Secret（设置 → 🔗 接入）。")
    with _TOKEN_LOCK:
        if not force and _TOKEN_CACHE["token"] and time.time() < _TOKEN_CACHE["expire_at"]:
            return _TOKEN_CACHE["token"]
        d = _request(s["domain"] + "/open-apis/auth/v3/tenant_access_token/internal",
                     {"app_id": s["app_id"], "app_secret": s["app_secret"]})
        if int(d.get("code") or 0) != 0 or not d.get("tenant_access_token"):
            raise RuntimeError("换 token 失败：%s" % (d.get("msg") or d))
        _TOKEN_CACHE["token"] = str(d["tenant_access_token"])
        _TOKEN_CACHE["expire_at"] = time.time() + max(60, int(d.get("expire") or 7200) - 120)
        return _TOKEN_CACHE["token"]


def send_text(text: str, receive_id: str = "", id_type: str = "chat_id",
              cfg: Optional[dict] = None) -> dict:
    """给会话（chat_id）/ 用户（open_id / user_id）发文本。返回 {ok, message_id}。"""
    s = settings(cfg)
    # 先看凭据再看接收方：没配应用时**任何**发送都必须报"没配"，不能因为
    # 恰好没填接收方就换一条更容易被当成"配置问题"的错误（判据会抓不住）。
    if not configured(cfg):
        raise RuntimeError("还没配飞书 App ID / App Secret（设置 → 🔗 接入）。")
    target = (receive_id or s["default_chat"] or "").strip()
    if not target:
        raise RuntimeError("没给接收方，也没配「默认会话 chat_id」。")
    body = (text or "").strip()
    if not body:
        raise RuntimeError("要发的内容是空的。")
    d = _request(
        s["domain"] + "/open-apis/im/v1/messages?receive_id_type=" +
        urllib.parse.quote(id_type),
        {"receive_id": target, "msg_type": "text",
         "content": json.dumps({"text": body}, ensure_ascii=False)},
        headers={"Authorization": "Bearer " + get_token(cfg)})
    if int(d.get("code") or 0) != 0:
        raise RuntimeError("发送失败：%s" % (d.get("msg") or d))
    return {"ok": True, "message_id": ((d.get("data") or {}).get("message_id") or "")}


def reply_text(message_id: str, text: str, cfg: Optional[dict] = None) -> dict:
    """回复某条消息（比"往会话里发"更准：不会串台）。"""
    s = settings(cfg)
    if not message_id:
        return send_text(text, cfg=cfg)
    d = _request(
        s["domain"] + "/open-apis/im/v1/messages/" +
        urllib.parse.quote(str(message_id)) + "/reply",
        {"msg_type": "text",
         "content": json.dumps({"text": (text or "").strip()}, ensure_ascii=False)},
        headers={"Authorization": "Bearer " + get_token(cfg)})
    if int(d.get("code") or 0) != 0:
        raise RuntimeError("回复失败：%s" % (d.get("msg") or d))
    return {"ok": True, "message_id": ((d.get("data") or {}).get("message_id") or "")}


# --------------------------------------------------------------------------
# ② 事件解析（回调入站与长连接入站**共用**，保证两条链路不会漂移）
# --------------------------------------------------------------------------
def verify_signature(headers: dict, body: bytes, cfg: Optional[dict] = None) -> bool:
    """校验 `X-Lark-Signature`：sha256(timestamp + nonce + encrypt_key + body)。

    只在配了 encrypt_key 时才可能通过 —— 飞书用 Encrypt Key 当签名密钥。
    没配 encrypt_key 时返回 False（调用方应改用 verify_token 校验，见 parse_event）。
    """
    s = settings(cfg)
    if not s["encrypt_key"]:
        return False
    h = {str(k).lower(): v for k, v in (headers or {}).items()}
    ts = str(h.get("x-lark-request-timestamp") or "")
    nonce = str(h.get("x-lark-request-nonce") or "")
    sig = str(h.get("x-lark-signature") or "")
    if not (ts and nonce and sig):
        return False
    raw = (ts + nonce + s["encrypt_key"]).encode("utf-8") + (body or b"")
    return hashlib.sha256(raw).hexdigest() == sig


def extract_message(event: dict) -> Optional[dict]:
    """从 `im.message.receive_v1` 的 event 里抽出统一格式；不是文本消息返回 None。

    返回 {chat_id, chat_type, open_id, user_name, text, message_id, ts, kind}
    —— 与 Discord / 微信 / Webhook 的入站格式保持一致（`kind` 标明消息类型）。
    """
    ev = event or {}
    msg = ev.get("message") or {}
    mtype = str(msg.get("message_type") or "").strip()
    try:
        content = json.loads(msg.get("content") or "{}")
    except Exception:                                        # noqa: BLE001
        content = {}
    if not isinstance(content, dict):
        content = {}
    sender = ev.get("sender") or {}
    sid = sender.get("sender_id") or {}
    text = str(content.get("text") or "").strip()
    # 群里 @机器人 会带 "@_user_1"，这是机器人的占位符，去掉才好当指令
    # ⚠️ 占位符是 `@_` + 名字（`_` 也算名字的一部分）→ 字符集必须含下划线，
    #    否则 `@_user_1` 只会被吃掉 `@_user`、留下 `_1` 混进指令里。
    text = re.sub(r"@_[A-Za-z0-9_]+\s*", "", text).strip()
    out = {
        "chat_id": str(msg.get("chat_id") or ""),
        "chat_type": str(msg.get("chat_type") or ""),
        "open_id": str(sid.get("open_id") or ""),
        "user_id": str(sid.get("user_id") or ""),
        "user_name": str((sender.get("sender_id") or {}).get("union_id") or ""),
        "message_id": str(msg.get("message_id") or ""),
        "ts": str(msg.get("create_time") or ""),
        "kind": "text" if mtype == "text" else (mtype or "unknown"),
    }
    if mtype != "text":
        out["text"] = ""
        return out
    if not text:
        return None
    out["text"] = text
    return out


def parse_event(body, headers: Optional[dict] = None,
                cfg: Optional[dict] = None) -> dict:
    """解析一次飞书推送。返回 {kind, ...}：

      · `challenge`  —— URL 验证，要把 `challenge` 原样回给飞书
      · `message`    —— 有真人消息，附 `msg`
      · `ignore`     —— 认识但与我们无关（如机器人自己发的）
      · `reject`     —— 验签/token 不过（**绝不静默放行**）
      · `encrypt`    —— 开了加密推送，标准库解不了（如实报）
    """
    s = settings(cfg)
    raw = body if isinstance(body, (bytes, bytearray)) else \
        json.dumps(body or {}, ensure_ascii=False).encode("utf-8")
    try:
        d = json.loads((raw or b"{}").decode("utf-8", "replace"))
    except Exception:                                        # noqa: BLE001
        return {"kind": "reject", "reason": "请求体不是 JSON"}
    if not isinstance(d, dict):
        return {"kind": "reject", "reason": "请求体不是 JSON 对象"}

    if d.get("encrypt"):
        return {"kind": "encrypt",
                "reason": "飞书这条推送是加密的（开了 Encrypt Key）。请在飞书后台把 "
                          "Encrypt Key 留空，或用事件回调/长连接的明文模式。"}

    # 验签：配了 encrypt_key 就强制校验签名；否则校验 verify_token（飞书两套机制）
    if s["encrypt_key"]:
        if not verify_signature(headers or {}, raw, cfg):
            return {"kind": "reject", "reason": "签名校验不过（encrypt_key 对不上？）"}
    elif s["verify_token"]:
        tok = str(d.get("token") or (d.get("header") or {}).get("token") or "")
        if tok != s["verify_token"]:
            return {"kind": "reject", "reason": "verification token 对不上"}

    # URL 验证（1.0 明文 / 2.0 也走这里）
    if d.get("type") == "url_verification" or "challenge" in d:
        return {"kind": "challenge", "challenge": str(d.get("challenge") or "")}

    header = d.get("header") or {}
    etype = str(header.get("event_type") or d.get("event_type") or "")
    event = d.get("event") or {}
    if etype != "im.message.receive_v1" and d.get("type") != "event_callback":
        return {"kind": "ignore", "reason": "没订阅的事件类型：%s" % (etype or "?")}
    msg = extract_message(event)
    if msg is None:
        return {"kind": "ignore", "reason": "空消息或非文本"}
    # 机器人自己发的消息不回（否则会自问自答）
    if str((event.get("sender") or {}).get("sender_type") or "") == "app":
        return {"kind": "ignore", "reason": "这条是机器人自己发的"}
    return {"kind": "message", "msg": msg,
            "event_id": str(header.get("event_id") or msg["message_id"])}


# --------------------------------------------------------------------------
# ③ 长连接（免公网）
# --------------------------------------------------------------------------
#: 飞书长连接帧（pbbp2.proto 的 Frame）—— 按官方 SDK 字段号复刻
_PB_FIELDS = {"seq": 1, "log_id": 2, "service": 3, "method": 4,
              "headers": 5, "payload_encoding": 6, "payload_type": 7,
              "payload": 8, "log_id_new": 9}


def _pb_varint(n: int) -> bytes:
    out = bytearray()
    n = int(n)
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _pb_read_varint(buf: bytes, i: int) -> tuple:
    shift = 0
    val = 0
    while True:
        if i >= len(buf):
            raise ValueError("varint 越界")
        b = buf[i]
        i += 1
        val |= (b & 0x7F) << shift
        if not (b & 0x80):
            return val, i
        shift += 7
        if shift > 63:
            raise ValueError("varint 过长")


def _pb_field(field: int, wire: int, value) -> bytes:
    """编码一个字段。wire==0 时 `value` 是 **int**；wire==2 时是 bytes。

    ⚠️ 别把"已经编码好的 varint 字节"再传进来 —— 那会被当成大整数重新编码，
    结果是**错的**（本例就踩过：时间戳级的大 seq 往返不一致）。
    """
    head = _pb_varint((field << 3) | wire)
    if wire == 0:
        return head + _pb_varint(int(value))
    if wire == 2:
        data = value if isinstance(value, (bytes, bytearray)) else \
            str(value).encode("utf-8")
        return head + _pb_varint(len(data)) + bytes(data)
    return head + bytes(value)


def pb_encode_frame(seq: int = 0, log_id: int = 0, service: int = 0,
                    method: int = 0, headers: Optional[dict] = None,
                    payload: bytes = b"", payload_type: str = "",
                    payload_encoding: str = "") -> bytes:
    """按 pbbp2.Frame 编码。只编我们真会发的字段（其余留空）。"""
    out = b""
    if seq:
        out += _pb_field(_PB_FIELDS["seq"], 0, seq)
    if log_id:
        out += _pb_field(_PB_FIELDS["log_id"], 0, log_id)
    if service:
        out += _pb_field(_PB_FIELDS["service"], 0, service)
    if method:
        out += _pb_field(_PB_FIELDS["method"], 0, method)
    for k, v in (headers or {}).items():
        sub = _pb_field(1, 2, str(k).encode("utf-8"))
        sub += _pb_field(2, 2, str(v).encode("utf-8"))
        out += _pb_field(_PB_FIELDS["headers"], 2, sub)
    if payload_encoding:
        out += _pb_field(_PB_FIELDS["payload_encoding"], 2,
                         payload_encoding.encode("utf-8"))
    if payload_type:
        out += _pb_field(_PB_FIELDS["payload_type"], 2, payload_type.encode("utf-8"))
    if payload:
        out += _pb_field(_PB_FIELDS["payload"], 2, payload)
    return out


def _pb_decode_header(buf: bytes) -> dict:
    d, i = {}, 0
    while i < len(buf):
        tag, i = _pb_read_varint(buf, i)
        wire, field = tag & 7, tag >> 3
        if wire == 2:
            ln, i = _pb_read_varint(buf, i)
            val = buf[i:i + ln]
            i += ln
            if field == 1:
                d["_k"] = val.decode("utf-8", "replace")
            elif field == 2:
                d["_v"] = val.decode("utf-8", "replace")
        elif wire == 0:
            _v, i = _pb_read_varint(buf, i)
        else:
            break
    return d


def pb_decode_frame(data: bytes) -> dict:
    """宽容解码：认识的字段填上，不认识的按 wire type 跳过（不会因为多一个字段就崩）。"""
    out = {"seq": 0, "log_id": 0, "service": 0, "method": 0, "headers": {},
           "payload_encoding": "", "payload_type": "", "payload": b"",
           "log_id_new": ""}
    i = 0
    buf = data or b""
    while i < len(buf):
        tag, i = _pb_read_varint(buf, i)
        wire, field = tag & 7, tag >> 3
        if wire == 0:
            v, i = _pb_read_varint(buf, i)
            for name in ("seq", "log_id", "service", "method"):
                if field == _PB_FIELDS[name]:
                    out[name] = v
        elif wire == 2:
            ln, i = _pb_read_varint(buf, i)
            val = buf[i:i + ln]
            i += ln
            if field == _PB_FIELDS["headers"]:
                h = _pb_decode_header(val)
                if h.get("_k"):
                    out["headers"][h["_k"]] = h.get("_v", "")
            elif field == _PB_FIELDS["payload"]:
                out["payload"] = val
            elif field == _PB_FIELDS["payload_type"]:
                out["payload_type"] = val.decode("utf-8", "replace")
            elif field == _PB_FIELDS["payload_encoding"]:
                out["payload_encoding"] = val.decode("utf-8", "replace")
            elif field == _PB_FIELDS["log_id_new"]:
                out["log_id_new"] = val.decode("utf-8", "replace")
        elif wire == 1:
            i += 8
        elif wire == 5:
            i += 4
        else:
            raise ValueError("不认识的 wire type %d（field %d）" % (wire, field))
    return out


def ws_endpoint(cfg: Optional[dict] = None) -> dict:
    """用 App 凭据换长连接地址。返回 {url, client_config}；失败抛中文错误。"""
    s = settings(cfg)
    if not (s["app_id"] and s["app_secret"]):
        raise RuntimeError("还没配飞书 App ID / App Secret（设置 → 🔗 接入）。")
    url = s["domain"] + "/callback/ws/endpoint"
    # 官方 SDK 用大写字段名；有的部署只认小写 —— 两种都试一次，别让用户猜
    attempts = [{"AppID": s["app_id"], "AppSecret": s["app_secret"]},
                {"app_id": s["app_id"], "app_secret": s["app_secret"]}]
    last = ""
    for body in attempts:
        try:
            d = _request(url, body, headers={"locale": "zh"})
        except RuntimeError as ex:
            last = str(ex)
            continue
        if int(d.get("code") or 0) == 0 and (d.get("data") or {}).get("URL"):
            data = d["data"]
            return {"url": str(data.get("URL") or ""),
                    "client_config": data.get("ClientConfig") or {},
                    "raw": data}
        last = str(d.get("msg") or d)
    raise RuntimeError("取长连接地址失败：%s" % last)


class FeishuLongConn:
    """飞书长连接客户端（后台线程）。

    生命周期：取地址 → 连 WS → 握手帧 → 收事件 → 定时 ping → 断了按 interval 重连。
    状态取值与 BaiLongma 一致：idle / connecting / connected / reconnecting / error。
    """

    def __init__(self, cfg: Optional[dict] = None,
                 on_event: Optional[Callable[[dict], None]] = None,
                 on_log: Optional[Callable[[str], None]] = None):
        self.cfg = cfg
        self.on_event = on_event
        self.on_log = on_log
        self.status = "idle"
        self.last_error = ""
        self.frames_in = 0
        self.events_in = 0
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
        if self._th is not None and self._th.is_alive():
            return True
        self._stop.clear()
        self._th = threading.Thread(target=self._run, name="pasm-feishu-ws", daemon=True)
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

    def _run(self) -> None:
        import wsclient
        ep = {"client_config": {}}
        tries = 0
        while not self._stop.is_set():
            self.status = "connecting" if tries == 0 else "reconnecting"
            try:
                ep = ws_endpoint(self.cfg)
            except Exception as ex:                          # noqa: BLE001
                self.status, self.last_error = "error", str(ex)
                self._log("飞书长连接取地址失败：%s" % ex)
                if self._stop.wait(30):
                    return
                tries += 1
                continue
            cc = ep.get("client_config") or {}
            try:
                ping = max(5, int(cc.get("PingInterval") or 120))
            except Exception:                                # noqa: BLE001
                ping = 120
            try:
                self._ws = wsclient.WSClient(ep["url"], headers={"User-Agent": UA})
                self._ws.connect(timeout=15)
                self.status = "connected"
                self._log("飞书长连接已建立（每 %ds 心跳）" % ping)
                # 握手帧：带 device_id/service_id，与官方 SDK 行为一致
                dev = "pasm-" + hashlib.md5(
                    (settings(self.cfg)["app_id"] or "x").encode()).hexdigest()[:12]
                hdr = {"type": "handshake", "device_id": dev, "biz_rt": "0"}
                svc = 0
                try:
                    q = urllib.parse.urlsplit(ep["url"]).query
                    svc = int(dict(urllib.parse.parse_qsl(q)).get("service_id") or 0)
                except Exception:                            # noqa: BLE001
                    svc = 0
                self._ws.send_binary(pb_encode_frame(
                    seq=1, log_id=1, service=svc, method=0, headers=hdr,
                    payload=b"{}", payload_type="ping"))
                last_ping = time.time()
                while not self._stop.is_set():
                    if time.time() - last_ping >= ping:
                        try:
                            self._ws.send_binary(pb_encode_frame(
                                seq=int(time.time()) & 0xFFFFFF, service=svc,
                                method=0, headers={"type": "ping", "biz_rt": "0"}))
                        except Exception:                    # noqa: BLE001
                            break
                        last_ping = time.time()
                    frame = self._ws.recv(timeout=2)
                    if frame is None:
                        continue
                    self.frames_in += 1
                    self._handle_frame(frame)
                tries = 0
            except Exception as ex:                          # noqa: BLE001
                self.status, self.last_error = "reconnecting", str(ex)
                self._log("飞书长连接断开：%s" % ex)
            finally:
                try:
                    if self._ws is not None:
                        self._ws.close()
                except Exception:                            # noqa: BLE001
                    pass
                self._ws = None
            if self._stop.is_set():
                return
            tries += 1
            max_n = int(cc.get("ReconnectCount") or 3)
            if tries > max_n:
                self.status = "error"
                self.last_error = "连续重连 %d 次仍失败" % max_n
                self._log(self.last_error + "，已停止（改配置后重启生效）")
                return
            try:
                wait = max(1, int(cc.get("ReconnectInterval") or 10))
            except Exception:                                # noqa: BLE001
                wait = 10
            if self._stop.wait(wait):
                return

    def _handle_frame(self, frame: dict) -> None:
        """帧 → 事件。二进制按 protobuf 解；文本当 JSON（宽容两态）。"""
        try:
            if frame.get("opcode") == 1:
                d = json.loads(frame["data"].decode("utf-8", "replace") or "{}")
                self._dispatch(d)
                return
            fr = pb_decode_frame(frame.get("data") or b"")
            hdr = fr.get("headers") or {}
            ptype = (fr.get("payload_type") or "").lower()
            if hdr.get("type") in ("pong", "ping"):
                return
            body = fr.get("payload") or b""
            if not body:
                return
            try:
                d = json.loads(body.decode("utf-8", "replace"))
            except Exception:                                # noqa: BLE001
                # 二进制业务消息（如加密的）—— 如实记一句，不假装处理了
                self._log("收到非 JSON 帧（payload_type=%s, %d 字节），暂不支持"
                          % (ptype or "?", len(body)))
                return
            self._ack(fr)
            self._dispatch(d)
        except Exception as ex:                              # noqa: BLE001
            self._log("解析飞书长连接帧失败：%s" % ex)

    def _ack(self, fr: dict) -> None:
        """回 ack（按 SDK 行为复刻）。失败不影响主流程。"""
        try:
            if self._ws is not None:
                self._ws.send_binary(pb_encode_frame(
                    seq=int(fr.get("seq") or 0), log_id=int(fr.get("log_id") or 0),
                    service=int(fr.get("service") or 0), method=int(fr.get("method") or 0),
                    headers={"biz_rt": "0"}))
        except Exception:                                    # noqa: BLE001
            pass

    def _dispatch(self, d: dict) -> None:
        if not isinstance(d, dict):
            return
        if d.get("type") == "url_verification" or "challenge" in d:
            return
        header = d.get("header") or {}
        if str(header.get("event_type") or d.get("event_type") or "") != \
                "im.message.receive_v1":
            return
        msg = extract_message(d.get("event") or {})
        if not msg or str((d.get("event") or {}).get("sender", {})
                          .get("sender_type") or "") == "app":
            return
        self.events_in += 1
        if self.on_event:
            try:
                self.on_event(msg)
            except Exception as ex:                          # noqa: BLE001
                self._log("处理飞书消息失败：%s" % ex)


# --------------------------------------------------------------------------
# ④ 事件回调（需要公网可达，一般要配内网穿透）
# --------------------------------------------------------------------------
class FeishuCallbackServer:
    """只绑 127.0.0.1 的本地回调端点（`POST /feishu/event`）。

    为什么只绑本机：PASM 是桌面应用，**不把用户的电脑暴露成公网中继**。
    要跨网用请自己配内网穿透（frp / cloudflared），把公网域名指到这里。
    """

    def __init__(self, cfg: Optional[dict] = None,
                 on_msg: Optional[Callable[[dict], None]] = None,
                 on_log: Optional[Callable[[str], None]] = None):
        self.cfg = cfg
        self.on_msg = on_msg
        self.on_log = on_log
        self._srv: Optional[ThreadingHTTPServer] = None
        self._th: Optional[threading.Thread] = None
        self.status = "idle"
        self.last_error = ""

    def _log(self, m: str) -> None:
        if self.on_log:
            try:
                self.on_log(m)
            except Exception:                                # noqa: BLE001
                pass

    def start(self) -> bool:
        s = settings(self.cfg)
        # v0.30.14：改用共用的 LocalInboundServer（与微信/Webhook 同一套实现）——
        # 之前自己写了一份 BaseHTTPRequestHandler，它会**响应任意路径**，
        # `/nope` 也能打到业务逻辑上（等于多开一个探测面）。共用版只认登记的路径。
        def _fn(body, headers, _query):
            r = parse_event(body, headers, self.cfg)
            if r["kind"] == "challenge":
                return 200, {"challenge": r.get("challenge", "")}
            if r["kind"] == "message":
                if self.on_msg:
                    try:
                        self.on_msg(r["msg"])
                    except Exception as ex:                  # noqa: BLE001
                        self._log("处理飞书回调消息失败：%s" % ex)
                return 200, {"code": 0}
            if r["kind"] in ("reject", "encrypt"):
                self._log("飞书回调被拒：%s" % (r.get("reason") or ""))
                return 403, {"code": 1, "msg": r.get("reason") or "rejected"}
            self._log("飞书回调忽略：%s" % (r.get("reason") or ""))
            return 200, {"code": 0}

        self._srv = CH.LocalInboundServer(
            s["port"], {"/feishu/event": _fn, "/": _fn},   # 根路径也认（穿透常指到 /）
            on_log=self.on_log, name="feishu")
        ok = self._srv.start()
        self.status = self._srv.status
        self.last_error = self._srv.last_error
        return ok

    def stop(self) -> None:
        try:
            if self._srv is not None:
                self._srv.stop()
        except Exception:                                    # noqa: BLE001
            pass
        self._srv = None
        self.status = "idle"

    def port(self) -> int:
        return int(settings(self.cfg)["port"])


# --------------------------------------------------------------------------
# 统一桥：入站 → handler → 回话
# --------------------------------------------------------------------------
class FeishuBridge:
    """把入站消息交给 handler，再把回复发回**同一个会话**（对齐 BaiLongma 的回复路由）。"""

    def __init__(self, cfg: Optional[dict] = None,
                 handler: Optional[Callable[[str], str]] = None,
                 on_log: Optional[Callable[[str], None]] = None):
        self.cfg = cfg
        self.handler = handler
        self.on_log = on_log
        self.longconn: Optional[FeishuLongConn] = None
        self.callback: Optional[FeishuCallbackServer] = None
        self.handled = 0
        self.replied = 0

    def _log(self, m: str) -> None:
        if self.on_log:
            try:
                self.on_log(m)
            except Exception:                                # noqa: BLE001
                pass

    def status(self) -> str:
        if self.callback is not None and self.callback.status == "connected":
            return "connected"
        if self.longconn is not None:
            return self.longconn.status
        return "idle"

    def start(self) -> bool:
        s = settings(self.cfg)
        if not configured(self.cfg):
            self._log("飞书应用凭据没配，跳过（只用群机器人 webhook 也能推送）")
            return False
        mode = s["mode"]
        if mode == "callback":
            self.callback = FeishuCallbackServer(self.cfg, on_msg=self._on_msg,
                                                 on_log=self.on_log)
            return self.callback.start()
        if mode == "longconn":
            self.longconn = FeishuLongConn(self.cfg, on_event=self._on_msg,
                                           on_log=self.on_log)
            return self.longconn.start()
        self._log("飞书接收方式为「关闭」，只做出站推送")
        return False

    def stop(self) -> None:
        for c in (self.longconn, self.callback):
            try:
                if c is not None:
                    c.stop()
            except Exception:                                # noqa: BLE001
                pass

    def _on_msg(self, msg: dict) -> None:
        """入站（长连接/回调共用）：去重 → handler → 回复。"""
        key = str(msg.get("message_id") or "")
        if key and self._seen(key):
            return
        text = str(msg.get("text") or "").strip()
        if not text:
            self._log("收到非文本飞书消息（%s），暂不支持" % (msg.get("kind") or "?"))
            return
        self.handled += 1
        self._log("📲 收到飞书消息：%s" % text[:60])
        ans = ""
        try:
            ans = (self.handler(text) if self.handler else "") or ""
        except Exception as ex:                              # noqa: BLE001
            ans = "处理失败：%s" % ex
        if not ans:
            ans = "（我处理完了，但没生成内容）"
        try:
            reply_text(key, str(ans)[:4000], cfg=self.cfg)
            self.replied += 1
        except Exception as ex:                              # noqa: BLE001
            self._log("回复飞书失败：%s（这次不重发，避免刷屏）" % ex)

    def _seen(self, key: str) -> bool:
        with _SEEN_LOCK:
            if key in _SEEN_IDS:
                return True
            _SEEN_IDS.append(key)
            while len(_SEEN_IDS) > _SEEN_MAX:
                _SEEN_IDS.pop(0)
            return False


# --------------------------------------------------------------------------
# 状态 / 测试
# --------------------------------------------------------------------------
def status_text(cfg: Optional[dict] = None) -> str:
    s = settings(cfg)
    lines = ["🔗 **飞书接入状态**"]
    if not configured(cfg):
        lines.append("· 应用凭据：**未配置** —— 只能发群机器人 webhook（单向推送）")
    else:
        lines.append("· App ID：`%s`" % s["app_id"])
        lines.append("· App Secret：`%s`" % (mask(s["app_secret"]) or "（空）"))
        lines.append("· 站点：%s" % s["domain"])
        mode = {"longconn": "长连接（推荐，不需要公网）",
                "callback": "事件回调（需要公网 HTTPS 地址）",
                "off": "关闭（只出站）"}.get(s["mode"], s["mode"])
        lines.append("· 接收方式：%s" % mode)
        if s["mode"] == "callback":
            lines.append("· 本地监听：http://127.0.0.1:%s/feishu/event" % s["port"])
        if s["default_chat"]:
            lines.append("· 默认推送会话：`%s`" % s["default_chat"])
        if s["encrypt_key"]:
            lines.append("· ⚠️ 配了 Encrypt Key：加密推送**解不了**，请在飞书后台清空它")
    if s["bot_webhook"]:
        lines.append("· 群机器人 webhook：已配置（出站可用）")
    lines.append("\n设好后说「接入飞书状态」可随时复查；正式接通要你自己在飞书开放平台"
                 "建一个自建应用、开机器人能力、订阅 `im.message.receive_v1`。")
    return "\n".join(lines)


def test_connection(cfg: Optional[dict] = None) -> str:
    """真去飞书跑一遍（换 token + 取长连接地址）。**只有真连上才算通过**。"""
    out = []
    try:
        get_token(cfg, force=True)
        out.append("① 换 tenant_access_token：✅ 成功")
    except Exception as ex:                                  # noqa: BLE001
        out.append("① 换 tenant_access_token：❌ %s" % ex)
        return "\n".join(out) + "\n\n（凭据不对或网络不通，后面的步骤没法继续）"
    try:
        ep = ws_endpoint(cfg)
        cc = ep.get("client_config") or {}
        out.append("② 取长连接地址：✅ 成功")
        out.append("   · 心跳间隔 %s 秒 / 重连间隔 %s 秒"
                   % (cc.get("PingInterval") or "?", cc.get("ReconnectInterval") or "?"))
    except Exception as ex:                                  # noqa: BLE001
        out.append("② 取长连接地址：❌ %s" % ex)
        out.append("   （长连接要在开放平台「事件与回调」里选「使用长连接接收事件」）")
    return "\n".join(out)


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

    # ① 掩码
    chk("掩码不露真值", mask("abcdef123456") == "abcd******56", mask("abcdef123456"))
    chk("空值掩码为空", mask("") == "")
    chk("短值全掩", mask("abc") == "***", mask("abc"))

    # ② protobuf 帧往返（编码→解码一致）
    fr = pb_encode_frame(seq=7, log_id=9, service=3, method=1,
                         headers={"type": "event", "biz_rt": "0"},
                         payload=b'{"a":1}', payload_type="event")
    d = pb_decode_frame(fr)
    chk("往返 seq", d["seq"] == 7, d)
    chk("往返 log_id", d["log_id"] == 9, d)
    chk("往返 service/method", (d["service"], d["method"]) == (3, 1), d)
    chk("往返 headers", d["headers"] == {"type": "event", "biz_rt": "0"}, d["headers"])
    chk("往返 payload", d["payload"] == b'{"a":1}', d["payload"])
    chk("往返 payload_type", d["payload_type"] == "event", d["payload_type"])

    # ③ 宽容解码：多一个未知字段（field 20, varint）也不能崩
    extra = _pb_field(20, 0, 123)
    d2 = pb_decode_frame(fr + extra)
    chk("未知字段被跳过、已知字段不受影响", d2["seq"] == 7 and d2["payload"] == b'{"a":1}')
    # 未知 length-delimited 字段
    d3 = pb_decode_frame(fr + _pb_field(21, 2, b"junk"))
    chk("未知 LEN 字段被跳过", d3["seq"] == 7, d3)
    # 大 varint（时间戳级）
    big = pb_encode_frame(seq=1704556800123, payload=b"x")
    chk("大整数 seq 往返", pb_decode_frame(big)["seq"] == 1704556800123)

    # ④ 消息抽取（含群里 @机器人）
    ev = {"sender": {"sender_id": {"open_id": "ou_x", "user_id": "u1"},
                     "sender_type": "user"},
          "message": {"message_id": "om_1", "chat_id": "oc_1", "chat_type": "group",
                      "message_type": "text",
                      "content": json.dumps({"text": "@_user_1 帮我出一张海报"})}}
    m = extract_message(ev)
    chk("@机器人占位符被剥掉", m and m["text"] == "帮我出一张海报", m and m["text"])
    chk("chat_id/open_id 抽到", m and m["chat_id"] == "oc_1" and m["open_id"] == "ou_x")
    chk("非文本消息 → text 为空且标明类型", (
        extract_message({"message": {"message_type": "image", "content": "{}"}}) or {}
    ).get("text") == "")
    chk("机器人自己发的会被识别（调用方据此忽略）",
        str((ev.get("sender") or {}).get("sender_type")) == "user")

    # ⑤ 事件解析：challenge / 正常消息 / 验签不过 / 加密
    c = {"feishu_app_id": "a", "feishu_app_secret": "b",
         "feishu_verify_token": "VT"}
    r = parse_event(json.dumps({"type": "url_verification", "challenge": "CH",
                                "token": "VT"}).encode(), {}, c)
    chk("challenge 被识别并带回", r["kind"] == "challenge" and r["challenge"] == "CH", r)
    body = json.dumps({"header": {"event_type": "im.message.receive_v1",
                                  "event_id": "e1", "token": "VT"},
                       "event": ev}).encode()
    r2 = parse_event(body, {}, c)
    chk("正常消息被识别", r2["kind"] == "message" and r2["msg"]["text"] == "帮我出一张海报", r2)
    chk("事件 id 抽到（用于去重）", r2.get("event_id") == "e1", r2)
    bad = json.dumps({"header": {"event_type": "im.message.receive_v1",
                                 "token": "WRONG"}, "event": ev}).encode()
    chk("token 不对 → reject（不静默放行）", parse_event(bad, {}, c)["kind"] == "reject")
    chk("加密推送 → 如实报 encrypt，不假装处理",
        parse_event(json.dumps({"encrypt": "xxx"}).encode(), {}, c)["kind"] == "encrypt")
    c_no_tok = {"feishu_app_id": "a", "feishu_app_secret": "b"}
    chk("没配 verify_token 时不强制校验（长连接场景）",
        parse_event(body, {}, c_no_tok)["kind"] == "message")

    # ⑥ 签名校验（有 encrypt_key 时）
    c_sig = {"feishu_app_id": "a", "feishu_app_secret": "b", "feishu_encrypt_key": "EK"}
    ts, nonce = "1710000000", "nn"
    sig = hashlib.sha256((ts + nonce + "EK").encode() + body).hexdigest()
    hdrs = {"X-Lark-Request-Timestamp": ts, "X-Lark-Request-Nonce": nonce,
            "X-Lark-Signature": sig}
    chk("签名正确 → 通过", verify_signature(hdrs, body, c_sig))
    hdrs["X-Lark-Signature"] = "deadbeef"
    chk("签名错误 → 不通过（反例）", not verify_signature(hdrs, body, c_sig))

    # ⑦ 缺凭据时不许假装可用
    empty = {}
    chk("没配凭据 → configured() 为 False", not configured(empty))
    chk("没配凭据时状态文案点名缺什么", "未配置" in status_text(empty))
    try:
        get_token(empty)
        chk("没配凭据时换 token 要报错", False)
    except RuntimeError as ex:
        chk("没配凭据时换 token 要报错", "App ID" in str(ex), str(ex))
    try:
        send_text("x", cfg=empty)
        chk("没配凭据时发送要报错（不假装成功）", False)
    except RuntimeError as ex:
        chk("没配凭据时发送要报错（不假装成功）", "App ID" in str(ex), str(ex))

    if fails:
        print("connector_feishu 自检失败 %d 项：" % len(fails))
        for f in fails:
            print("  ✗", f)
        return 1
    print("connector_feishu 自检通过（%d 项）" % ok)
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(selftest())
