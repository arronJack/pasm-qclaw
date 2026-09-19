# -*- coding: utf-8 -*-
"""connector_http.py —— 三个连接器共用的「本地入站端点」与 XML 小工具（v0.30.14）

飞书事件回调 / 微信公众号回调 / 通用 Webhook 收消息，本质是同一件事：
**在本机起一个小 HTTP 端点 → 校验 → 归一化 → 交给业务**。

三份各写一遍会漂移（改了一处忘了另一处），所以抽出这一层。它有两条硬边界：

1. **只绑 `127.0.0.1`** —— PASM 是桌面应用，绝不把用户的电脑变成公网中继。
   要跨网用请自己配内网穿透，把公网域名指到本机端口。
2. **校验不过就 403** —— 不静默放行、不"先收下再说"。收消息比发消息危险得多
   （发消息最多骚扰自己，收消息能远程指挥这台电脑）。
"""
from __future__ import annotations

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Optional


# --------------------------------------------------------------------------
# XML（微信公众号协议是 XML，不是 JSON）
# --------------------------------------------------------------------------
def escape_xml(value) -> str:
    return (str(value if value is not None else "")
            .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;").replace("'", "&apos;"))


def parse_xml(text: str) -> dict:
    """把微信那种扁平 XML 打成 dict（支持 CDATA；不处理嵌套同名标签）。

    比 `xml.etree` 宽松：微信/企业微信的推送里 `<![CDATA[...]]>` 很常见，
    而业务只要顶层几个字段。
    """
    out = {}
    src = str(text or "")
    # ⚠️ 值里**不许再出现 `<`**（`[^<]*`）—— 否则 `<xml>…</xml>` 这种**容器标签**
    #    会把整段子标签当成自己的值一口吞掉，里面真正的字段一个都抽不出来（踩过）。
    pat = re.compile(
        r"<([A-Za-z0-9_:-]+)>\s*(?:<!\[CDATA\[(.*?)\]\]>|([^<]*))\s*</\1>", re.S)
    for m in pat.finditer(src):
        key = m.group(1)
        val = m.group(2) if m.group(2) is not None else (m.group(3) or "")
        out[key] = val.strip() if isinstance(val, str) else ""
    return out


# --------------------------------------------------------------------------
# 本地入站端点
# --------------------------------------------------------------------------
class LocalInboundServer:
    """极小的本地回调服务：路径 → 处理函数。

    处理函数签名 `fn(body: bytes, headers: dict, query: dict) -> (status, payload)`，
    payload 为 dict（回 JSON）或 str/bytes（原样回；微信要回 XML 或 "success"）。

    ⚠️ **query 必须传进去**：微信把 `signature/timestamp/nonce` 放在 **URL** 上而不是
    请求体里，只给 body 等于逼调用方从别处偷看 path（本模块第一版就这么干过，
    又脏又不可测）。
    """

    def __init__(self, port: int, routes: Optional[dict] = None,
                 host: str = "127.0.0.1",
                 on_log: Optional[Callable[[str], None]] = None,
                 name: str = "inbound"):
        self.port = int(port)
        self.host = host
        self.routes = dict(routes or {})
        self.on_log = on_log
        self.name = name
        self.status = "idle"
        self.last_error = ""
        self.requests = 0
        self._srv: Optional[ThreadingHTTPServer] = None
        self._th: Optional[threading.Thread] = None

    def log(self, m: str) -> None:
        if self.on_log:
            try:
                self.on_log(m)
            except Exception:                                # noqa: BLE001
                pass

    def add_route(self, path: str, fn: Callable) -> None:
        self.routes[path] = fn

    @staticmethod
    def _split(raw_path: str) -> tuple:
        """把 `/wechat?a=1&b=2` 拆成 (`/wechat`, {"a": "1", "b": "2"})。"""
        import urllib.parse
        u = urllib.parse.urlsplit(raw_path or "/")
        return u.path or "/", dict(urllib.parse.parse_qsl(u.query))

    def start(self) -> bool:
        outer = self

        class _H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):                       # 静音访问日志
                return

            def _reply(self, status: int, payload) -> None:
                if isinstance(payload, dict):
                    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                    ctype = "application/json; charset=utf-8"
                elif isinstance(payload, bytes):
                    data, ctype = payload, "text/plain; charset=utf-8"
                else:
                    data = str(payload or "").encode("utf-8")
                    ctype = "text/plain; charset=utf-8"
                self.send_response(int(status))
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                try:
                    self.wfile.write(data)
                except Exception:                            # noqa: BLE001
                    pass

            def do_GET(self):
                outer.requests += 1
                path, _q = outer._split(self.path)
                if path in ("/", "/healthz", "/ping"):
                    return self._reply(200, {"ok": True, "name": outer.name,
                                             "port": outer.port})
                return self._reply(404, {"ok": False, "error": "not found"})

            def do_POST(self):
                outer.requests += 1
                path, _q = outer._split(self.path)
                fn = outer.routes.get(path)
                if fn is None:
                    # 只认登记过的路径：未知路径直接 404，不给探测留口子
                    return self._reply(404, {"ok": False, "error": "unknown path"})
                try:
                    n = int(self.headers.get("Content-Length") or 0)
                except Exception:                            # noqa: BLE001
                    n = 0
                if n > 4 * 1024 * 1024:
                    return self._reply(413, {"ok": False, "error": "body too large"})
                body = self.rfile.read(n) if n else b""
                try:
                    status, payload = fn(body, dict(self.headers), _q)
                except Exception as ex:                      # noqa: BLE001
                    outer.log("[%s] 处理 %s 时出错：%s" % (outer.name, path, ex))
                    return self._reply(500, {"ok": False, "error": str(ex)[:200]})
                return self._reply(status, payload)

        try:
            self._srv = ThreadingHTTPServer((self.host, self.port), _H)
        except Exception as ex:                              # noqa: BLE001
            self.status = "error"
            self.last_error = "端口 %s 起不来：%s" % (self.port, ex)
            self.log(self.last_error)
            return False
        self._th = threading.Thread(target=self._srv.serve_forever,
                                    name="pasm-%s" % self.name, daemon=True)
        self._th.start()
        self.status = "connected"
        self.log("[%s] 已监听 http://%s:%s%s"
                 % (self.name, self.host, self.port, "、".join(self.routes) or "/"))
        return True

    def stop(self) -> None:
        try:
            if self._srv is not None:
                self._srv.shutdown()
                self._srv.server_close()
        except Exception:                                    # noqa: BLE001
            pass
        self._srv = None
        self.status = "idle"

    def url(self, path: str = "/") -> str:
        return "http://%s:%d%s" % (self.host, self.port, path)


def selftest() -> int:
    """真起一个服务，真发一次请求（含签名拒绝路径）。"""
    import urllib.error
    import urllib.request
    fails, ok = [], 0

    def chk(label, cond, extra=""):
        nonlocal ok
        if cond:
            ok += 1
        else:
            fails.append("%s %s" % (label, extra))

    # ① XML
    x = "<xml><ToUserName><![CDATA[toUser]]></ToUserName><Content>你好</Content></xml>"
    d = parse_xml(x)
    chk("CDATA 能解", d.get("ToUserName") == "toUser", d)
    chk("普通标签能解", d.get("Content") == "你好", d)
    chk("转义正确", escape_xml('a<b>&"c"') == "a&lt;b&gt;&amp;&quot;c&quot;",
        escape_xml('a<b>&"c"'))
    chk("空输入返回空 dict（不抛）", parse_xml("") == {})

    # ② 真起服务 + 真请求
    seen = {}

    def _handler(body, headers, query):
        seen["body"] = body
        seen["ua"] = headers.get("User-Agent")
        seen["query"] = query
        if body == b"deny":
            return 403, {"ok": False}
        return 200, {"ok": True, "echo": body.decode("utf-8", "replace")}

    srv = LocalInboundServer(0, {"/x": _handler}, name="selftest")
    srv.port = 0
    # 让系统分配端口：先拿一个空闲端口号
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    srv.port = port
    if not srv.start():
        chk("本地入站服务能起来", False, srv.last_error)
    else:
        chk("本地入站服务能起来", True)
        try:
            req = urllib.request.Request(
                "http://127.0.0.1:%d/x?sig=abc&ts=1" % port, data=b"hi",
                headers={"User-Agent": "t", "Content-Type": "text/plain"})
            with urllib.request.urlopen(req, timeout=5) as r:
                got = json.loads(r.read().decode())
            chk("POST 到登记路径 → 200 + 处理结果", got.get("echo") == "hi", got)
            chk("URL query 被传进处理函数（微信的 signature 就在 query 上）",
                seen.get("query") == {"sig": "abc", "ts": "1"}, seen.get("query"))
        except Exception as ex:                              # noqa: BLE001
            chk("POST 到登记路径 → 200 + 处理结果", False, ex)
        try:
            req = urllib.request.Request("http://127.0.0.1:%d/x" % port, data=b"deny")
            urllib.request.urlopen(req, timeout=5)
            chk("处理函数拒绝时真回 403", False)
        except urllib.error.HTTPError as ex:
            chk("处理函数拒绝时真回 403", ex.code == 403, ex.code)
        except Exception as ex:                              # noqa: BLE001
            chk("处理函数拒绝时真回 403", False, ex)
        try:
            urllib.request.urlopen("http://127.0.0.1:%d/nope" % port, timeout=5)
            chk("未登记路径 → 404（不给探测留口子）", False)
        except urllib.error.HTTPError as ex:
            chk("未登记路径 → 404（不给探测留口子）", ex.code == 404, ex.code)
        except Exception as ex:                              # noqa: BLE001
            chk("未登记路径 → 404（不给探测留口子）", False, ex)
        try:
            with urllib.request.urlopen("http://127.0.0.1:%d/healthz" % port,
                                        timeout=5) as r:
                h = json.loads(r.read().decode())
            chk("健康检查可用", h.get("ok") is True and h.get("port") == port, h)
        except Exception as ex:                              # noqa: BLE001
            chk("健康检查可用", False, ex)
        chk("请求计数在涨", srv.requests >= 3, srv.requests)
        srv.stop()
        chk("能停掉", srv.status == "idle")

    if fails:
        print("connector_http 自检失败 %d 项：" % len(fails))
        for f in fails:
            print("  ✗", f)
        return 1
    print("connector_http 自检通过（%d 项）" % ok)
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(selftest())
