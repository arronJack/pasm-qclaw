# -*- coding: utf-8 -*-
"""connector_wechat.py —— 微信接入（v0.30.14）

## 为什么是「微信公众号」而不是「ClawBot」

BaiLongma 的 `src/social/wechat-clawbot.js`（682 行）依赖第三方**非官方**微信协议库
`wechat-ilink-client`：二维码登录个人微信号、自己实现 CDN 下载与媒体 AES-ECB 解密。
PASM **刻意不移植这条线**，三个理由（都是硬约束，不是偷懒）：

1. **合规与账号风险**：非官方协议登录个人微信违反微信服务条款，**账号可能被封**；
   这是用户的号，我们不能替他承担这个风险。
2. **技术上装不进桌面发行版**：那是个 Node 包，PASM 是 Python/PySide6 打包；
   把 Node 运行时+协议库塞进安装包，体积与维护成本都不成比例。
3. **有人能接的替代品**：微信官方给开发者的是**公众号**（服务号可主动推送 +
   接收用户消息），微信/企业微信的**群机器人 webhook** 也完全够"把结果推到微信"。

所以这里实现的是**官方、合规**的微信路线：

| 能力 | 实现 |
|---|---|
| 群机器人单向推送（企业微信 / 微信群机器人） | ✅ 出站（复用 `remote_bridge` 的 payload 抹平） |
| 公众号 · 出站：客服消息（可主动推给 48h 内互动过的用户） | ✅ `send_text` |
| 公众号 · 入站：用户给公众号发消息 → PASM 干活 → 回消息 | ✅ `WechatBridge`（签名校验 + 防重放 + XML） |
| 个人微信 ClawBot（非官方协议） | ❌ **不移植**（上面三个理由）；用群机器人/公众号替代 |

## 诚实边界
- 公众号入站需要**公网可达**（微信服务器要能访问你的回调 URL）→ 本模块只绑
  `127.0.0.1`，跨网请自配内网穿透，**绝不把用户的电脑暴露成公网中继**。
- 客服消息有**48 小时窗口**限制（用户最后一次互动起算）；超窗发送会失败，
  我们会把微信的 errcode 原样报出来，不假装成功。
- 加密模式（`encodingAESKey`）不支持（标准库解不了 AES）—— 在公众号后台选**明文模式**。
"""
from __future__ import annotations

import hashlib
import json
import threading
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

API = "https://api.weixin.qq.com"
TIMEOUT = 15
UA = "PASM-Studio/1.0 (+connector-wechat)"
REPLAY_WINDOW = 300                    # 微信签名防重放：5 分钟
_TOKEN_LOCK = threading.Lock()
_TOKEN = {"token": "", "expire_at": 0.0}


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
    return {
        "appid": str(c.get("wechat_appid") or "").strip(),
        "secret": str(c.get("wechat_secret") or "").strip(),
        "token": str(c.get("wechat_token") or "").strip(),
        "port": int(c.get("wechat_port") or 8798),
        "default_openid": str(c.get("wechat_default_openid") or "").strip(),
        "mode": str(c.get("wechat_mode") or "off").strip().lower(),   # off|callback
        "wecom_bot": str(c.get("wecom_bot_url") or "").strip(),
    }


def configured(cfg: Optional[dict] = None) -> bool:
    """公众号出站需要 appid+secret；只配群机器人 webhook 也能「推到微信」，那不算这里。"""
    s = settings(cfg)
    return bool(s["appid"] and s["secret"])


def mask(secret: str, keep: int = 4) -> str:
    v = str(secret or "")
    if not v:
        return ""
    if len(v) <= keep + 2:
        return "*" * len(v)
    return v[:keep] + "*" * max(4, len(v) - keep - 2) + v[-2:]


# --------------------------------------------------------------------------
# 出网
# --------------------------------------------------------------------------
def _get_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return json.loads(r.read().decode("utf-8", "replace") or "{}")
    except urllib.error.URLError as ex:
        raise RuntimeError("连不上微信 API（%s）" % ex.reason)
    except Exception as ex:                                  # noqa: BLE001
        raise RuntimeError("微信 API 返回异常：%s" % ex)


def _post_json(url: str, payload: dict, raw: bool = False) -> dict:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Content-Type": "application/json; charset=utf-8",
                 "User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            txt = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as ex:
        raise RuntimeError("微信返回 HTTP %s" % ex.code)
    except urllib.error.URLError as ex:
        raise RuntimeError("连不上微信 API（%s）" % ex.reason)
    if raw:
        return {"_raw": txt}
    try:
        return json.loads(txt or "{}")
    except Exception:                                        # noqa: BLE001
        raise RuntimeError("微信返回的不是 JSON：%s" % txt[:200])


def get_access_token(cfg: Optional[dict] = None, force: bool = False) -> str:
    s = settings(cfg)
    if not configured(cfg):
        raise RuntimeError("还没配公众号 AppID / AppSecret（设置 → 🔗 接入）。")
    with _TOKEN_LOCK:
        if not force and _TOKEN["token"] and time.time() < _TOKEN["expire_at"]:
            return _TOKEN["token"]
        d = _get_json("%s/cgi-bin/token?grant_type=client_credential&appid=%s&secret=%s"
                      % (API, urllib.parse.quote(s["appid"]),
                         urllib.parse.quote(s["secret"])))
        if not d.get("access_token"):
            raise RuntimeError("换 access_token 失败：%s（errcode %s）"
                               % (d.get("errmsg") or d, d.get("errcode")))
        _TOKEN["token"] = str(d["access_token"])
        _TOKEN["expire_at"] = time.time() + max(60, int(d.get("expires_in") or 7200) - 120)
        return _TOKEN["token"]


def send_text(openid: str, text: str, cfg: Optional[dict] = None) -> dict:
    """客服消息（**48 小时窗口**内可主动推）。返回 {ok}；失败抛中文错误。"""
    s = settings(cfg)
    if not configured(cfg):
        raise RuntimeError("还没配公众号 AppID / AppSecret（设置 → 🔗 接入）。")
    to = (openid or s["default_openid"] or "").strip()
    if not to:
        raise RuntimeError("没给接收方 openid，也没配「默认接收人」。")
    body = (text or "").strip()
    if not body:
        raise RuntimeError("要发的内容是空的。")
    tok = get_access_token(cfg)
    d = _post_json("%s/cgi-bin/message/custom/send?access_token=%s"
                   % (API, urllib.parse.quote(tok)),
                   {"touser": to, "msgtype": "text", "text": {"content": body[:2000]}})
    if int(d.get("errcode") or 0) != 0:
        raise RuntimeError("发送失败：%s（errcode %s）—— 客服消息只能发给 48 小时内"
                           "跟公众号互动过的用户。" % (d.get("errmsg") or d, d.get("errcode")))
    return {"ok": True}


def send_wecom_bot(text: str, url: str = "", cfg: Optional[dict] = None) -> dict:
    """企业微信 / 微信群机器人 webhook（最省事的「推到微信」）。"""
    s = settings(cfg)
    target = (url or s["wecom_bot"] or "").strip()
    if not target:
        raise RuntimeError("还没配企业微信群机器人 webhook（设置 → 🔗 接入）。")
    payload = {"msgtype": "text", "text": {"content": str(text or "")[:2000]}}
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(target, data=data, method="POST",
                                 headers={"Content-Type": "application/json",
                                          "User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            txt = r.read().decode("utf-8", "replace")
    except urllib.error.URLError as ex:
        raise RuntimeError("连不上群机器人（%s）" % ex.reason)
    try:
        d = json.loads(txt or "{}")
    except Exception:                                        # noqa: BLE001
        return {"ok": True, "raw": txt[:120]}
    if int(d.get("errcode") or 0) != 0:
        raise RuntimeError("群机器人返回：%s（errcode %s）"
                           % (d.get("errmsg") or txt[:120], d.get("errcode")))
    return {"ok": True}


# --------------------------------------------------------------------------
# 入站：公众号回调（签名 + 防重放 + XML）
# --------------------------------------------------------------------------
def check_signature(token: str, signature: str, timestamp: str, nonce: str,
                    now: Optional[float] = None) -> tuple:
    """微信官方校验：`sha1(sorted([token, timestamp, nonce]))` == signature。

    额外做了**防重放**（时间戳偏离 > 5 分钟直接拒）—— 官方文档只要求签名，
    但只验签名的话，同一条请求可以被无限重放（抓包后就能一直指挥这台电脑）。
    返回 (是否通过, 原因)。
    """
    if not token:
        return False, "没配公众号 Token（校验无法进行）"
    if not (signature and timestamp and nonce):
        return False, "缺 signature / timestamp / nonce"
    try:
        ts = float(timestamp)
    except Exception:                                        # noqa: BLE001
        return False, "timestamp 不是数字"
    # 微信给的是**秒**；也有人传毫秒，两种都认
    if ts > 1e11:
        ts = ts / 1000.0
    drift = abs((time.time() if now is None else now) - ts)
    if drift > REPLAY_WINDOW:
        return False, "时间戳偏离 %.0f 秒（超过 %d 秒，判为重放）" % (drift, REPLAY_WINDOW)
    want = hashlib.sha1(
        "".join(sorted([token, str(timestamp), str(nonce)])).encode("utf-8")
    ).hexdigest()
    return (want == signature), ("" if want == signature else "签名不匹配")


def parse_inbound(xml_text: str) -> Optional[dict]:
    """公众号消息 XML → 统一入站格式（与飞书/Discord/Webhook 同构）。

    只处理文本消息与关注事件；其余如实标明类型。返回 None = 不是用户消息。
    """
    d = CH.parse_xml(xml_text)
    if not d:
        return None
    mtype = str(d.get("MsgType") or "").strip().lower()
    frm = str(d.get("FromUserName") or "").strip()
    out = {
        "open_id": frm,
        "chat_id": str(d.get("ToUserName") or "").strip(),
        "message_id": str(d.get("MsgId") or "").strip(),
        "ts": str(d.get("CreateTime") or "").strip(),
        "kind": mtype or "unknown",
        "text": str(d.get("Content") or "").strip(),
        "event": str(d.get("Event") or "").strip().lower(),
    }
    if mtype == "text":
        return out if out["text"] else None
    if mtype == "event" and out["event"] == "subscribe":
        out["text"] = ""                                   # 关注：没有正文，但有事件
        return out
    return out


def plan_inbound_reply(msg: dict) -> str:
    """被动回复（5 秒内）用短句先回；真正的结果稍后用客服消息推。

    为什么不当场等结果：模型干活要几十秒，微信要求 5 秒内响应，
    超时它会重试三次 → 用户会收到三条重复回复。所以**立即确认 + 异步推结果**。
    """
    if msg.get("kind") == "event" and msg.get("event") == "subscribe":
        return "你好，我是 PASM。直接说要做什么就行（例：把桌面上的报告整理成一份 PPT）。"
    return "收到，正在处理…（结果稍后发给你）"


def build_reply_xml(to_user: str, from_user: str, text: str) -> str:
    return ("<xml><ToUserName><![CDATA[%s]]></ToUserName>"
            "<FromUserName><![CDATA[%s]]></FromUserName>"
            "<CreateTime>%d</CreateTime><MsgType><![CDATA[text]]></MsgType>"
            "<Content><![CDATA[%s]]></Content></xml>"
            % (CH.escape_xml(to_user).replace("&lt;", "").replace("&gt;", ""),
               CH.escape_xml(from_user), int(time.time()),
               str(text or "").replace("]]>", "]] >")))


class WechatBridge:
    """公众号入站 → handler → 客服消息回推。"""

    def __init__(self, cfg: Optional[dict] = None,
                 handler: Optional[Callable[[str], str]] = None,
                 on_log: Optional[Callable[[str], None]] = None):
        self.cfg = cfg
        self.handler = handler
        self.on_log = on_log
        self.srv: Optional[CH.LocalInboundServer] = None
        self.handled = 0
        self.replied = 0
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
        # ① 后台「提交/校验」：微信会带 echostr 来试探，必须原样回它
        if str(query.get("echostr") or ""):
            ok, why = check_signature(s["token"], query.get("signature", ""),
                                      query.get("timestamp", ""), query.get("nonce", ""))
            if not ok:
                self.rejected += 1
                self._log("公众号校验被拒：%s" % why)
                return 403, "invalid signature"
            return 200, str(query.get("echostr"))
        # ② 正常消息推送：验签 + 防重放
        ok, why = check_signature(s["token"], query.get("signature", ""),
                                  query.get("timestamp", ""), query.get("nonce", ""))
        if not ok:
            self.rejected += 1
            self._log("公众号回调被拒：%s" % why)
            return 403, "invalid signature"
        msg = parse_inbound(body.decode("utf-8", "replace"))
        if not msg:
            return 200, "success"
        self.handled += 1
        text = str(msg.get("text") or "").strip()
        if text:
            self._log("📲 收到公众号消息：%s" % text[:60])
            threading.Thread(target=self._work, args=(msg, text), daemon=True).start()
        return 200, build_reply_xml(msg.get("open_id", ""), msg.get("chat_id", ""),
                                    plan_inbound_reply(msg))

    def _work(self, msg: dict, text: str) -> None:
        ans = ""
        try:
            ans = (self.handler(text) if self.handler else "") or ""
        except Exception as ex:                              # noqa: BLE001
            ans = "处理失败：%s" % ex
        if not ans:
            ans = "（我处理完了，但没生成内容）"
        try:
            send_text(msg.get("open_id") or "", ans, cfg=self.cfg)
            self.replied += 1
        except Exception as ex:                              # noqa: BLE001
            self._log("回推微信失败：%s" % ex)

    def start(self) -> bool:
        s = settings(self.cfg)
        if s["mode"] != "callback":
            return False
        if not s["token"]:
            self._log("公众号 Token 没配，入站无法验签 → 拒绝启动（绝不收未验签的消息）")
            return False
        self.srv = CH.LocalInboundServer(s["port"], {"/wechat": self._handle},
                                         on_log=self.on_log, name="wechat")
        return self.srv.start()

    def stop(self) -> None:
        if self.srv is not None:
            self.srv.stop()


# --------------------------------------------------------------------------
# 状态 / 测试
# --------------------------------------------------------------------------
def status_text(cfg: Optional[dict] = None) -> str:
    s = settings(cfg)
    lines = ["🔗 **微信接入状态**"]
    if not configured(cfg):
        lines.append("· 公众号 AppID/AppSecret：**未配置**")
    else:
        lines.append("· 公众号 AppID：`%s`" % s["appid"])
        lines.append("· AppSecret：`%s`" % (mask(s["secret"]) or "（空）"))
        lines.append("· 接收方式：%s" % ("回调（需公网可达）"
                                         if s["mode"] == "callback" else "关闭（只出站）"))
        if s["mode"] == "callback":
            lines.append("· 本地监听：http://127.0.0.1:%s/wechat" % s["port"])
        if s["token"]:
            lines.append("· 校验 Token：`%s`" % mask(s["token"]))
    if s["wecom_bot"]:
        lines.append("· 企业微信群机器人：已配置（最省事的「推到微信」路线）")
    lines.append("· 个人微信（ClawBot 那条非官方协议）**刻意不移植**：违反微信条款、"
                 "有封号风险，而且是 Node 包装不进桌面发行版 —— 用群机器人或公众号替代。")
    return "\n".join(lines)


def test_connection(cfg: Optional[dict] = None) -> str:
    s = settings(cfg)
    if not configured(cfg):
        return "❌ 还没配公众号 AppID / AppSecret（设置 → 🔗 接入）。"
    try:
        get_access_token(cfg, force=True)
        return "① 换 access_token：✅ 成功（凭据有效）"
    except Exception as ex:                                  # noqa: BLE001
        return "① 换 access_token：❌ %s" % ex


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

    # ① 签名校验（微信官方算法：sha1 of sorted([token,ts,nonce])）
    tok, ts, nonce = "mytoken", "1710000000", "n1"
    sig = hashlib.sha1("".join(sorted([tok, ts, nonce])).encode()).hexdigest()
    chk("签名正确 → 通过", check_signature(tok, sig, ts, nonce, now=1710000000)[0])
    chk("签名错误 → 拒绝（反例）",
        not check_signature(tok, "deadbeef", ts, nonce, now=1710000000)[0])
    chk("缺字段 → 拒绝",
        not check_signature(tok, sig, "", nonce, now=1710000000)[0])
    chk("时间戳偏离 6 分钟 → 判重放拒绝",
        not check_signature(tok, sig, ts, nonce, now=1710000000 + 360)[0])
    chk("时间戳毫秒级也认（签名按原样算、时间按秒比）",
        check_signature(
            tok, hashlib.sha1("".join(sorted([tok, "1710000000000", nonce]))
                              .encode()).hexdigest(),
            "1710000000000", nonce, now=1710000000)[0])
    chk("没配 token → 拒绝（不给「不校验」留口子）",
        not check_signature("", sig, ts, nonce, now=1710000000)[0])

    # ② XML 解析
    xml = ("<xml><ToUserName><![CDATA[gh_1]]></ToUserName>"
           "<FromUserName><![CDATA[oUser1]]></FromUserName>"
           "<CreateTime>1710000000</CreateTime><MsgType><![CDATA[text]]></MsgType>"
           "<Content><![CDATA[帮我整理桌面]]></Content><MsgId>12345</MsgId></xml>")
    m = parse_inbound(xml)
    chk("文本消息解析出正文", m and m["text"] == "帮我整理桌面", m)
    chk("openid / msgid 抽到", m and m["open_id"] == "oUser1" and m["message_id"] == "12345")
    chk("空文本 → None（不产生假消息）",
        parse_inbound(xml.replace("帮我整理桌面", "")) is None)
    sub = ("<xml><FromUserName><![CDATA[o1]]></FromUserName>"
           "<MsgType><![CDATA[event]]></MsgType><Event><![CDATA[subscribe]]></Event></xml>")
    ms = parse_inbound(sub)
    chk("关注事件被识别", ms and ms["kind"] == "event" and ms["event"] == "subscribe", ms)
    chk("关注时给欢迎语（不是「收到正在处理」）", "你好" in plan_inbound_reply(ms))
    chk("普通消息立即确认（不等结果）", "正在处理" in plan_inbound_reply(m))
    rep = build_reply_xml("oUser1", "gh_1", "hi")
    chk("被动回复 XML 结构正确",
        "<ToUserName><![CDATA[oUser1]]></ToUserName>" in rep and "</xml>" in rep, rep)
    chk("正文里的 ]]> 被破坏（防注入）", "]] >" in build_reply_xml("a", "b", "x]]>y"))

    # ③ 缺配置时不许假装
    chk("没配 → configured False", not configured({}))
    chk("没配时状态文案点名缺什么", "未配置" in status_text({}))
    try:
        send_text("o1", "hi", cfg={})
        chk("没配时发送要报错", False)
    except RuntimeError as ex:
        chk("没配时发送要报错", "AppID" in str(ex), str(ex))
    try:
        get_access_token({})
        chk("没配时换 token 要报错", False)
    except RuntimeError as ex:
        chk("没配时换 token 要报错", "AppID" in str(ex), str(ex))
    try:
        send_wecom_bot("hi", cfg={})
        chk("没配群机器人时发送要报错（不假装成功）", False)
    except RuntimeError as ex:
        chk("没配群机器人时发送要报错（不假装成功）", "webhook" in str(ex), str(ex))

    # ④ 状态文案必须写明 ClawBot 为什么不移植（用户有权知道）
    chk("状态文案说清 ClawBot 未移植的原因",
        "封号" in status_text({}) and "条款" in status_text({}))

    if fails:
        print("connector_wechat 自检失败 %d 项：" % len(fails))
        for f in fails:
            print("  ✗", f)
        return 1
    print("connector_wechat 自检通过（%d 项）" % ok)
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(selftest())
