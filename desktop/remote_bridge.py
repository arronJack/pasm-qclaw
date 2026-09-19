"""remote_bridge —— 跨端远程消息桥（v0.28.x）
==============================================

对齐 QClaw / OpenClaw / WorkBuddy 的「跨端远程」能力：让 PASM 不困在桌面里 ——
**在手机上发一句话，桌上的 PASM 干活，结果再推回手机**。

三个方向：
1. **推出去** `notify(text)` —— 把结果推到手机。支持：
   · Telegram Bot（最省事，免费、跨平台）
   · 企业微信 / 钉钉 / 飞书 群机器人（填个 Webhook URL 就行）
   · Server酱 / Bark（个人微信 / iOS 推送）
   URL 类型自动识别，用户不用选。
2. **收回来** `fetch_messages()` —— 轮询 Telegram getUpdates，取回你从手机发的指令，
   交给 PASM 处理（`reply_loop` 会串起来）。
3. **本机接收端** `LocalInbox` —— 一个**默认关闭**的本地 HTTP 端点（`POST /msg`），
   给「家里局域网 / 内网穿透」场景用。**只绑 127.0.0.1、必须带 token**，否则拒绝启动
   —— 绝不把用户的电脑暴露成公网开放中继。

实现要点：
- **纯标准库**（urllib / http.server / json），零依赖，安装包不变大；
- 所有出网调用带 timeout，失败给中文原因，绝不假装"已发送"；
- token / 授权码只存在本机 config.json，不写日志。
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, List, Optional

try:
    from pasm_companion import CONFIG, DATA_DIR
except Exception:                                # 独立导入兜底（测试用）
    DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".pasmstudio_dev")
    CONFIG = os.path.join(DATA_DIR, "config.json")

STATE_PATH = os.path.join(DATA_DIR, "bridge_state.json")
TIMEOUT = 20
UA = "PASM-Studio/1.0 (+remote-bridge)"

GUIDE = (
    "📡 跨端推送还没配置。挑一种就行（都是免费的）：\n\n"
    "**最简单 · Telegram**（手机装 Telegram → @BotFather 发 /newbot 拿 token →\n"
    "  再跟你的 bot 说句话，然后用 @userinfobot 查自己的 chat id）\n"
    "  · 填 `bridge_tg_token` 和 `bridge_tg_chat`\n\n"
    "**国内最省事 · 群机器人**（企业微信/钉钉/飞书群里「添加机器人」→ 复制 Webhook URL）\n"
    "  · 只填 `bridge_webhook_url` 一项即可，我自动认平台\n\n"
    "**个人推送 · Server酱 / Bark**\n"
    "  · 把它的推送 URL 填进 `bridge_webhook_url` 即可\n\n"
    "填完就能说：「把刚才的结果推到我手机」。"
)


# ---------------- 配置 ----------------
def load_cfg() -> dict:
    try:
        with open(CONFIG, "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def settings(cfg: Optional[dict] = None) -> dict:
    c = cfg if isinstance(cfg, dict) else load_cfg()
    return {
        "webhook": str(c.get("bridge_webhook_url") or "").strip(),
        "tg_token": str(c.get("bridge_tg_token") or "").strip(),
        "tg_chat": str(c.get("bridge_tg_chat") or "").strip(),
        "token": str(c.get("bridge_token") or "").strip(),
        "port": int(c.get("bridge_inbox_port") or 8799),
    }


def configured(cfg: Optional[dict] = None) -> bool:
    s = settings(cfg)
    return bool(s["webhook"] or (s["tg_token"] and s["tg_chat"]))


def _kind_of(url: str) -> str:
    """按 URL 认出是哪家平台 —— 用户不用自己选。"""
    u = (url or "").lower()
    if "qyapi.weixin.qq.com" in u:
        return "wecom"
    if "oapi.dingtalk.com" in u:
        return "dingtalk"
    if "open.feishu.cn" in u or "open.larksuite.com" in u:
        return "feishu"
    if "sctapi.ftqq.com" in u or "pushplus" in u:
        return "serverchan"
    if "api.day.app" in u:
        return "bark"
    if "api.telegram.org" in u:
        return "telegram"
    return "generic"


# ---------------- 出网 ----------------
def _post(url: str, payload: dict, as_form: bool = False) -> str:
    if as_form:
        data = urllib.parse.urlencode(payload).encode("utf-8")
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
    else:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json; charset=utf-8"}
    headers["User-Agent"] = UA
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.read().decode("utf-8", "replace")[:400]
    except urllib.error.HTTPError as ex:
        detail = ""
        try:
            detail = ex.read().decode("utf-8", "replace")[:200]
        except Exception:
            pass
        raise RuntimeError("平台返回 HTTP %s：%s" % (ex.code, detail or ex.reason))
    except urllib.error.URLError as ex:
        raise RuntimeError("连不上推送服务器（%s）。请检查网络。" % ex.reason)
    except Exception as ex:
        raise RuntimeError("推送请求失败：%s" % ex)


def _build_payload(kind: str, title: str, text: str) -> tuple:
    """返回 (payload, as_form)。不同平台的字段名完全不一样，这里统一抹平。"""
    if kind == "wecom":                      # 企业微信群机器人
        return {"msgtype": "text", "text": {"content": (title + "\n" + text)[:2000]}}, False
    if kind == "dingtalk":                   # 钉钉群机器人
        return {"msgtype": "text", "text": {"content": (title + "\n" + text)[:2000]}}, False
    if kind == "feishu":                     # 飞书群机器人
        return {"msg_type": "text", "content": {"text": (title + "\n" + text)[:2000]}}, False
    if kind == "serverchan":                 # Server酱（form 表单）
        return {"title": title[:60], "desp": text[:4000]}, True
    if kind == "generic":
        # 通用：把常见的几个字段都带上，尽量让对方的模板能取到值
        return {"text": text, "content": (title + "\n" + text),
                "title": title, "msg": (title + "\n" + text)}, False
    return {"text": (title + "\n" + text)}, False


def notify(text: str, title: str = "PASM", cfg: Optional[dict] = None) -> str:
    """把一段文字推到手机。返回中文结果；失败抛 RuntimeError。"""
    s = settings(cfg)
    text = (text or "").strip()
    if not text:
        raise RuntimeError("要推的内容是空的。")
    sent = []

    # 1) Telegram
    if s["tg_token"] and s["tg_chat"]:
        url = "https://api.telegram.org/bot%s/sendMessage" % s["tg_token"]
        _post(url, {"chat_id": s["tg_chat"],
                    "text": ("%s\n%s" % (title, text))[:4000],
                    "disable_web_page_preview": True})
        sent.append("Telegram")

    # 2) Webhook（企业微信/钉钉/飞书/Server酱/Bark/通用）
    if s["webhook"]:
        kind = _kind_of(s["webhook"])
        if kind == "bark":
            # Bark 走路径式： https://api.day.app/<key>/<title>/<body>
            base = s["webhook"].rstrip("/")
            url = "%s/%s/%s" % (base, urllib.parse.quote(title[:40]),
                                urllib.parse.quote(text[:800]))
            _post(url, {})
            sent.append("Bark")
        else:
            payload, as_form = _build_payload(kind, title, text)
            _post(s["webhook"], payload, as_form)
            sent.append({"wecom": "企业微信", "dingtalk": "钉钉", "feishu": "飞书",
                         "serverchan": "Server酱", "generic": "Webhook"}.get(kind, kind))

    if not sent:
        raise RuntimeError(GUIDE)
    return "📤 已推送到 %s ✅（%d 字）" % ("、".join(sent), len(text))


# ---------------- 收回来（Telegram 轮询）----------------
def _load_state() -> dict:
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _save_state(d: dict) -> None:
    try:
        os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
        with open(STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
    except Exception:
        pass


def fetch_messages(cfg: Optional[dict] = None) -> List[dict]:
    """拉取手机上发给 bot 的新消息（已去重，不会重复消费）。

    返回 [{text, from_name, chat_id}]；未配置 Telegram 时返回 []。
    """
    s = settings(cfg)
    if not (s["tg_token"]):
        return []
    state = _load_state()
    offset = int(state.get("tg_offset") or 0) + 1
    url = ("https://api.telegram.org/bot%s/getUpdates?timeout=0&offset=%d"
           % (s["tg_token"], offset))
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
    except Exception:
        return []
    out: List[dict] = []
    max_id = offset - 1
    for upd in (data.get("result") or []):
        try:
            uid = int(upd.get("update_id") or 0)
            max_id = max(max_id, uid)
            msg = upd.get("message") or upd.get("edited_message") or {}
            txt = str(msg.get("text") or "").strip()
            if not txt:
                continue
            frm = msg.get("from") or {}
            name = (str(frm.get("first_name") or "") + str(frm.get("last_name") or "")
                    or str(frm.get("username") or "") or "用户")
            out.append({"text": txt, "from_name": name.strip(),
                        "chat_id": str((msg.get("chat") or {}).get("id") or "")})
        except Exception:
            continue
    if max_id >= offset:
        state["tg_offset"] = max_id
        _save_state(state)
    return out


def reply_loop(handler: Callable[[str], str],
               poll: int = 5,
               on_log: Optional[Callable[[str], None]] = None) -> bool:
    """后台线程：不断把手机发来的话交给 handler 处理，再把结果推回手机。

    handler(text) -> 回复文本。适合「人在外面，让电脑上的 PASM 干活」。
    """
    def _run():
        while not _loop_stop.is_set():
            try:
                msgs = fetch_messages()
                for m in msgs:
                    try:
                        if on_log:
                            on_log("📲 收到来自「%s」：%s" % (m["from_name"], m["text"][:40]))
                        ans = handler(m["text"]) or "（我处理完了，但没生成内容）"
                        notify(str(ans), title="PASM 回复")
                    except Exception as ex:
                        try:
                            notify("处理失败：%s" % ex, title="PASM")
                        except Exception:
                            pass
            except Exception:
                pass
            if _loop_stop.wait(max(2, int(poll))):
                return

    global _loop_thread
    if _loop_thread is not None and _loop_thread.is_alive():
        return True
    _loop_stop.clear()
    _loop_thread = threading.Thread(target=_run, name="pasm-bridge", daemon=True)
    _loop_thread.start()
    return True


def stop_loop() -> None:
    _loop_stop.set()


_loop_stop = threading.Event()
_loop_thread: Optional[threading.Thread] = None


# ---------------- 本机接收端（默认关闭）----------------
class LocalInbox:
    """一个**只能本机访问**的小 HTTP 端点：`POST /msg` 带 token 即可投递消息。

    刻意不做公网监听 —— 默认绑 127.0.0.1。要跨网用请自行配内网穿透，
    并把 token 设得足够长。启动前必须已配置 token，否则拒绝启动。
    """

    def __init__(self, token: str, port: int = 8799, host: str = "127.0.0.1"):
        self.token = (token or "").strip()
        self.port = int(port)
        self.host = host
        self._srv: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self.inbox: List[dict] = []

    def start(self) -> tuple:
        if not self.token:
            return False, ("为了安全，本机接收端必须先设一个 `bridge_token`（随便一长串字符），"
                           "否则任何本机程序都能往 PASM 里塞指令。")
        inbox = self.inbox
        lock = self._lock
        token = self.token

        class _H(BaseHTTPRequestHandler):
            def log_message(self, *a):        # 静音，别刷控制台
                pass

            def _deny(self, code=403, msg="forbidden"):
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"ok": False, "error": msg}).encode())

            def do_POST(self):
                if self.path.split("?")[0] != "/msg":
                    return self._deny(404, "not found")
                qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                given = (self.headers.get("X-PASM-Token") or (qs.get("token") or [""])[0])
                if given != token:
                    return self._deny()
                try:
                    n = int(self.headers.get("Content-Length") or 0)
                    body = self.rfile.read(min(n, 200000)).decode("utf-8", "replace")
                except Exception:
                    return self._deny(400, "bad body")
                text = ""
                try:
                    j = json.loads(body)
                    text = str(j.get("text") or j.get("msg") or "") if isinstance(j, dict) else str(j)
                except Exception:
                    text = body
                text = text.strip()
                if not text:
                    return self._deny(400, "empty")
                with lock:
                    inbox.append({"text": text, "t": time.strftime("%Y-%m-%d %H:%M:%S")})
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"ok": True}).encode())

            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(
                    {"ok": True, "service": "PASM local inbox"}).encode())

        try:
            srv = ThreadingHTTPServer((self.host, self.port), _H)
        except OSError as ex:
            return False, "端口 %d 起不来（%s）。换一个端口再试。" % (self.port, ex)
        self._srv = srv
        self._thread = threading.Thread(target=srv.serve_forever, name="pasm-inbox", daemon=True)
        self._thread.start()
        return True, "📥 本机接收端已启动：http://%s:%d/msg（POST，需带 X-PASM-Token）" % (
            self.host, self.port)

    def poll(self) -> List[dict]:
        with self._lock:
            got, self.inbox = self.inbox, []
        return got

    def stop(self) -> None:
        try:
            if self._srv:
                self._srv.shutdown()
                self._srv.server_close()
        except Exception:
            pass
        self._srv = None


# ---------------- 文案 ----------------
def status_text(cfg: Optional[dict] = None) -> str:
    s = settings(cfg)
    if not configured(cfg):
        return GUIDE
    lines = ["📡 跨端远程桥"]
    if s["tg_token"]:
        lines.append("· Telegram：已配 token%s"
                     % ("，chat=%s" % s["tg_chat"] if s["tg_chat"] else "（缺 chat id）"))
    if s["webhook"]:
        lines.append("· Webhook：%s（识别为 %s）"
                     % (re.sub(r"(?<=/)[^/]{12,}", "<已隐藏>", s["webhook"]), _kind_of(s["webhook"])))
    lines.append("· 本机接收端：%s" % ("已设 token（可开启）" if s["token"] else "未设 token（关闭）"))
    return "\n".join(lines)


def selftest() -> str:
    """离线自检：只验证平台识别与载荷构造，不出网。"""
    out = []
    cases = [
        ("https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=x", "wecom"),
        ("https://oapi.dingtalk.com/robot/send?access_token=x", "dingtalk"),
        ("https://open.feishu.cn/open-apis/bot/v2/hook/x", "feishu"),
        ("https://sctapi.ftqq.com/SCT123.send", "serverchan"),
        ("https://api.day.app/abc123", "bark"),
        ("https://example.com/hook", "generic"),
    ]
    ok = 0
    for url, want in cases:
        got = _kind_of(url)
        flag = "OK " if got == want else "BAD"
        if got == want:
            ok += 1
        out.append("%s %-52s -> %s" % (flag, url[:50], got))
    for kind in ("wecom", "dingtalk", "feishu", "serverchan", "generic"):
        p, form = _build_payload(kind, "标题", "正文")
        out.append("   载荷[%s] form=%s keys=%s" % (kind, form, sorted(p.keys())))
    out.append("结果：%d/%d 通过" % (ok, len(cases)))
    return "\n".join(out)


if __name__ == "__main__":
    print(selftest())
