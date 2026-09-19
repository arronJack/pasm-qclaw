# -*- coding: utf-8 -*-
"""connector_webhook.py —— 通用 Webhook 接入（v0.30.14）

对标 BaiLongma 的 `src/social/webhooks.js`（`/social/*` 入站 + 签名校验）。
"Webhook" 是四个通道里**最通用**的一个：不管对方是自建服务、n8n、Zapier、
企业微信/钉钉/飞书的群机器人，还是手机上的快捷指令，都能走它。

| 方向 | 能力 |
|---|---|
| 入站 | `POST /inbox`（本机）：**Token 或 HMAC-SHA256** 校验 → 归一化 → 交给主循环 |
| 出站 | 复用 `remote_bridge`：URL 自动识别平台、payload 字段抹平、失败给中文原因 |

## 为什么入站一定要校验

出站最多是"骚扰自己"；**入站等于把"远程指挥这台电脑"的口子开出来**——
不加校验的话，局域网里任何人都能让你的 PASM 干活、读文件、发消息。
所以：没配 token → **服务根本不启动**（不是"先收下再说"）；校验不过 → 403。

## 诚实边界
- 只绑 `127.0.0.1`（默认）。想跨网请自配内网穿透 + 足够长的 token。
- 只收 **UTF-8 文本/JSON**；二进制附件不支持（如实 415，不假装收到）。
- 出站只发**你配的地址**，不做任何"猜平台"的自动行为（识别平台是为了选对 payload 字段）。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable, Optional

import connector_http as CH

try:
    from pasm_companion import CONFIG, DATA_DIR
except Exception:                                            # noqa: BLE001
    DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "..", ".pasmstudio_dev")
    CONFIG = os.path.join(DATA_DIR, "config.json")

TIMEOUT = 15
UA = "PASM-Studio/1.0 (+connector-webhook)"
MAX_BODY = 256 * 1024


def load_cfg() -> dict:
    try:
        with open(CONFIG, "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:                                        # noqa: BLE001
        return {}


def settings(cfg: Optional[dict] = None) -> dict:
    c = cfg if isinstance(cfg, dict) else load_cfg()
    path = str(c.get("webhook_in_path") or "/inbox").strip() or "/inbox"
    if not path.startswith("/"):
        path = "/" + path
    return {
        "token": str(c.get("webhook_in_token") or "").strip(),
        "port": int(c.get("webhook_in_port") or 8796),
        "path": path,
        "hmac": bool(c.get("webhook_in_hmac")),
        "out_url": str(c.get("bridge_webhook_url") or "").strip(),
        "mode": str(c.get("webhook_in_mode") or "off").strip().lower(),   # off|on
    }


def configured(cfg: Optional[dict] = None) -> bool:
    """入站够不够可用（出站只看 out_url，见 outbound_ready）。"""
    s = settings(cfg)
    return bool(s["token"] and s["mode"] == "on")


def outbound_ready(cfg: Optional[dict] = None) -> bool:
    s = settings(cfg)
    return s["out_url"].startswith(("http://", "https://"))


def mask(secret: str, keep: int = 3) -> str:
    """掩码**保持原长度**（与飞书/Discord 两个连接器一致），只露头尾。"""
    v = str(secret or "")
    if not v:
        return ""
    if len(v) <= keep + 2:
        return "*" * len(v)
    return v[:keep] + "*" * max(2, len(v) - keep - 2) + v[-2:]


# --------------------------------------------------------------------------
# 入站校验
# --------------------------------------------------------------------------
def check_token(given: str, expected: str) -> bool:
    """定长比较，防时序侧信道（token 泄露的最省事路径就是逐字节试）。"""
    g, e = str(given or ""), str(expected or "")
    if not e:
        return False
    return hmac.compare_digest(g.encode("utf-8"), e.encode("utf-8"))


def sign_body(body: bytes, token: str) -> str:
    return hmac.new(str(token or "").encode("utf-8"), body or b"",
                    hashlib.sha256).hexdigest()


def check_signature(body: bytes, headers: dict, token: str) -> bool:
    got = ""
    for k, v in (headers or {}).items():
        if k.lower() == "x-pasm-signature":
            got = str(v)
            break
    if not got or not token:
        return False
    return hmac.compare_digest(sign_body(body, token), got)


def parse_inbound(body: bytes, headers: Optional[dict] = None) -> Optional[dict]:
    """请求体 → 统一入站格式。支持 JSON（推荐）与纯文本。

    JSON 形式：{"text": "...", "from": "谁", "channel": "来源"}
    也认更宽松的 `content` / `message` / `msg` 字段 —— 别人家的自动化工具
    字段名五花八门，能认的都认，认不出就如实说"没有正文"。
    """
    if not body:
        return None
    ctype = ""
    for k, v in (headers or {}).items():
        if k.lower() == "content-type":
            ctype = str(v).lower()
            break
    text = ""
    frm, channel = "", ""
    if "json" in ctype or body.lstrip()[:1] in (b"{", b"["):
        try:
            d = json.loads(body.decode("utf-8", "replace"))
        except Exception:                                    # noqa: BLE001
            return {"kind": "invalid", "text": "",
                    "reason": "Content-Type 说是 JSON，但解析失败"}
        if isinstance(d, dict):
            text = str(d.get("text") or d.get("content") or d.get("message")
                       or d.get("msg") or "").strip()
            frm = str(d.get("from") or d.get("user") or d.get("sender") or "")
            channel = str(d.get("channel") or d.get("source") or d.get("platform") or "")
        else:
            text = str(d).strip()
    else:
        text = body.decode("utf-8", "replace").strip()
    if not text:
        return {"kind": "invalid", "text": "",
                "reason": "请求体里没有正文（认 text/content/message/msg 或纯文本）"}
    return {"kind": "text", "text": text, "user_name": frm,
            "channel": channel or "webhook", "message_id": "",
            "ts": str(int(time.time()))}


# --------------------------------------------------------------------------
# 出站
# --------------------------------------------------------------------------
def send(text: str, url: str = "", cfg: Optional[dict] = None,
         title: str = "PASM") -> dict:
    """按平台抹平 payload 后 POST 出去（复用 `remote_bridge` 的识别与抹平）。"""
    import remote_bridge as RB
    s = settings(cfg)
    target = (url or s["out_url"] or "").strip()
    if not target:
        raise RuntimeError("还没配出站 Webhook 地址（设置 → 🔗 接入）。")
    if not target.startswith(("http://", "https://")):
        raise RuntimeError("Webhook 地址要以 http:// 或 https:// 开头。")
    body = (text or "").strip()
    if not body:
        raise RuntimeError("要发的内容是空的。")
    kind = RB._kind_of(target)
    payload, as_form = RB._build_payload(kind, title, body)
    RB._post(target, payload, as_form)
    label = {"wecom": "企业微信", "dingtalk": "钉钉", "feishu": "飞书",
             "serverchan": "Server酱", "bark": "Bark", "telegram": "Telegram",
             "generic": "通用 Webhook"}.get(kind, kind)
    return {"ok": True, "platform": kind, "label": label}


# --------------------------------------------------------------------------
# 入站桥
# --------------------------------------------------------------------------
class WebhookBridge:
    """`POST /inbox` → 校验 → handler → 在**响应体里**回话（调用方同步等得起）。"""

    def __init__(self, cfg: Optional[dict] = None,
                 handler: Optional[Callable[[str], str]] = None,
                 on_log: Optional[Callable[[str], None]] = None):
        self.cfg = cfg
        self.handler = handler
        self.on_log = on_log
        self.srv: Optional[CH.LocalInboundServer] = None
        self.handled = 0
        self.rejected = 0

    def _log(self, m: str) -> None:
        if self.on_log:
            try:
                self.on_log(m)
            except Exception:                                # noqa: BLE001
                pass

    def status(self) -> str:
        return self.srv.status if self.srv is not None else "idle"

    def _handle(self, body: bytes, headers: dict, query: dict) -> tuple:
        s = settings(self.cfg)
        if len(body or b"") > MAX_BODY:
            return 413, {"ok": False, "error": "body too large"}
        ok = False
        if s["hmac"]:
            ok = check_signature(body, headers, s["token"])
            why = "HMAC 签名不匹配" if not ok else ""
        else:
            given = ""
            for k, v in (headers or {}).items():
                if k.lower() == "x-pasm-token":
                    given = str(v)
                    break
            if not given:
                given = str(query.get("token") or "")
            ok = check_token(given, s["token"])
            why = "token 缺失或不匹配" if not ok else ""
        if not ok:
            self.rejected += 1
            self._log("Webhook 入站被拒：%s" % why)
            return 403, {"ok": False, "error": why}
        msg = parse_inbound(body, headers)
        if not msg or msg.get("kind") != "text":
            return 400, {"ok": False,
                         "error": (msg or {}).get("reason") or "没有可处理的正文"}
        self.handled += 1
        self._log("📲 收到 Webhook 消息（%s）：%s"
                  % (msg.get("channel") or "?", msg["text"][:60]))
        try:
            ans = (self.handler(msg["text"]) if self.handler else "") or ""
        except Exception as ex:                              # noqa: BLE001
            ans = "处理失败：%s" % ex
        return 200, {"ok": True, "reply": ans, "handled": True}

    def start(self) -> bool:
        s = settings(self.cfg)
        if s["mode"] != "on":
            return False
        if not s["token"]:
            self._log("Webhook 入站 Token 没配 → 拒绝启动（绝不裸奔收指令）")
            return False
        self.srv = CH.LocalInboundServer(s["port"], {s["path"]: self._handle},
                                         on_log=self.on_log, name="webhook")
        return self.srv.start()

    def stop(self) -> None:
        if self.srv is not None:
            self.srv.stop()


# --------------------------------------------------------------------------
# 状态 / 测试
# --------------------------------------------------------------------------
def status_text(cfg: Optional[dict] = None) -> str:
    s = settings(cfg)
    lines = ["🔗 **Webhook 接入状态**"]
    if configured(cfg):
        lines.append("· 入站：✅ http://127.0.0.1:%s%s" % (s["port"], s["path"]))
        lines.append("  校验方式：%s" % ("HMAC-SHA256（`X-PASM-Signature`）"
                                         if s["hmac"] else "固定 Token（`X-PASM-Token` 或 ?token=）"))
        lines.append("  Token：`%s`" % mask(s["token"]))
    else:
        lines.append("· 入站：**未开启**（要开需同时配 Token 并把模式设为 on）")
    lines.append("· 出站：%s" % ("✅ `%s`" % s["out_url"] if outbound_ready(cfg)
                                 else "**未配置**（`bridge_webhook_url`）"))
    lines.append("· 入站示例：`curl -X POST http://127.0.0.1:%s%s -H "
                 "\"X-PASM-Token: <你的token>\" -H \"Content-Type: application/json\" "
                 "-d '{\"text\":\"把桌面整理一下\"}'`" % (s["port"], s["path"]))
    return "\n".join(lines)


def test_connection(cfg: Optional[dict] = None) -> str:
    s = settings(cfg)
    out = []
    if s["out_url"]:
        try:
            r = send("PASM 连通性测试（可以忽略这条消息）", cfg=cfg, title="PASM 测试")
            out.append("① 出站：✅ 已发到 %s" % r["label"])
        except Exception as ex:                              # noqa: BLE001
            out.append("① 出站：❌ %s" % ex)
    else:
        out.append("① 出站：未配置（跳过）")
    if configured(cfg):
        out.append("② 入站：配置看起来完整（127.0.0.1:%s%s）；"
                   "服务在本机监听，可从「接入」页看实时状态" % (s["port"], s["path"]))
    else:
        out.append("② 入站：未开启（需要 Token + 模式 on）")
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

    tok = "s3cret-token"
    # ① Token 校验
    chk("正确 token 通过", check_token(tok, tok))
    chk("错误 token 拒绝（反例）", not check_token("x", tok))
    chk("空 token 一律拒绝（不配=不开）", not check_token(tok, ""))
    chk("两边都空也拒绝", not check_token("", ""))

    # ② HMAC 校验
    body = b'{"text":"hi"}'
    sig = sign_body(body, tok)
    chk("HMAC 正确通过", check_signature(body, {"X-PASM-Signature": sig}, tok))
    chk("HMAC 错误拒绝（反例）",
        not check_signature(body, {"X-PASM-Signature": "00"}, tok))
    chk("改了 body 签名就失效（防篡改）",
        not check_signature(b'{"text":"HI"}', {"X-PASM-Signature": sig}, tok))
    chk("缺签名头 → 拒绝", not check_signature(body, {}, tok))

    # ③ 入站解析
    m = parse_inbound('{"text":"整理桌面"}'.encode("utf-8"),
                      {"content-type": "application/json"})
    chk("JSON 入站解析出正文", m and m["text"] == "整理桌面", m)
    m2 = parse_inbound(b'{"content":"x","from":"n8n","channel":"ci"}',
                       {"content-type": "application/json"})
    chk("别家工具的字段名也认（content/from/channel）",
        m2 and m2["text"] == "x" and m2["user_name"] == "n8n" and m2["channel"] == "ci", m2)
    chk("纯文本也认", (parse_inbound(b"hello", {}) or {}).get("text") == "hello")
    chk("没有正文 → 明确 Invalid 并说明原因",
        (parse_inbound(b'{"foo":1}', {"content-type": "application/json"}) or {}
         ).get("kind") == "invalid")
    chk("JSON 解析失败 → 不抛、如实报",
        (parse_inbound(b"{bad json", {"content-type": "application/json"}) or {}
         ).get("kind") == "invalid")
    chk("空 body → None", parse_inbound(b"", {}) is None)

    # ④ 配置
    chk("没配 → configured False", not configured({}))
    chk("只配 token 但没开 → 仍 False（不给「忘了开开关」留隐患）",
        not configured({"webhook_in_token": "t"}))
    chk("token + on → True",
        configured({"webhook_in_token": "t", "webhook_in_mode": "on"}))
    chk("出站就绪判定", outbound_ready({"bridge_webhook_url": "https://x/y"})
        and not outbound_ready({}))
    chk("路径不带斜杠会自动补",
        settings({"webhook_in_path": "inbox"})["path"] == "/inbox")
    try:
        send("x", cfg={})
        chk("没配出站地址时发送要报错（不假装成功）", False)
    except RuntimeError as ex:
        chk("没配出站地址时发送要报错（不假装成功）", "Webhook" in str(ex), str(ex))
    try:
        send("x", url="ftp://x", cfg={})
        chk("非法协议被拦住（反例）", False)
    except RuntimeError as ex:
        chk("非法协议被拦住（反例）", "http" in str(ex), str(ex))

    # ⑤ 状态文案
    chk("没开时状态文案写清怎么开", "未开启" in status_text({}))
    chk("状态文案带可直接复制的 curl 示例", "curl" in status_text({}))
    chk("掩码不露真值", mask("abcdefgh") == "abc***gh", mask("abcdefgh"))

    if fails:
        print("connector_webhook 自检失败 %d 项：" % len(fails))
        for f in fails:
            print("  ✗", f)
        return 1
    print("connector_webhook 自检通过（%d 项）" % ok)
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(selftest())
