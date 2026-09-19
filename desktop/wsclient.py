# -*- coding: utf-8 -*-
"""wsclient.py —— 极小 RFC6455 WebSocket 客户端（纯标准库，v0.30.14）

## 为什么自己写

Discord 网关和飞书长连接**都是 WebSocket**，而本机（打包 venv / 运行环境）
**没有 `websockets` / `websocket-client`**（实测两个 venv 都缺）。引依赖意味着：
① 安装包变大；② 新增 `_PASM_HEAVY` 连坐风险；③ 又要过一遍 hiddenimports。
这个项目一贯的做法是**能用标准库就不加依赖**（见 `remote_bridge` / `connector_mail`），
所以这里只实现我们真正需要的那一小块：客户端握手 + 掩码帧收发 + ping/pong/close。

## 边界（诚实）

- **只做客户端**，不实现服务端（服务端分片、扩展协商、压缩都不做）。
- 不支持 `permessage-deflate`（Discord 网关**可以不请求压缩**；飞书长连接也不要求）。
- 支持：文本/二进制帧、分片重组、ping/pong 自动应答、close 握手、读超时返回 None。
- 未实现：`Sec-WebSocket-Extensions`、代理、HTTP 重定向。

## 用法

    ws = WSClient("wss://example.com/path?x=1")
    ws.connect(timeout=10)
    ws.send_text('{"op":1}')
    frame = ws.recv(timeout=5)        # None = 超时（调用方继续心跳/检查停止位）
    if frame and frame["opcode"] == 1:
        data = frame["data"].decode("utf-8")
    ws.close()
"""
from __future__ import annotations

import base64
import hashlib
import os
import socket
import ssl
import struct
import threading
import urllib.parse

_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

OP_CONT, OP_TEXT, OP_BIN, OP_CLOSE, OP_PING, OP_PONG = 0, 1, 2, 8, 9, 10
_MAX_FRAME = 8 * 1024 * 1024          # 单帧上限（飞书/Discord 的消息远小于它）


class WSError(RuntimeError):
    """握手失败 / 协议错误 / 连接已关。"""


def _recv_exact(sock, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise WSError("连接被对端关闭（还差 %d 字节）" % (n - len(buf)))
        buf += chunk
    return bytes(buf)


class WSClient:
    """阻塞式 WebSocket 客户端。一次只允许一个读线程 + 多个写线程（发送加锁）。"""

    def __init__(self, url: str, headers: dict | None = None,
                 origin: str = "", timeout: float = 15.0):
        u = urllib.parse.urlsplit(url or "")
        if u.scheme not in ("ws", "wss"):
            raise WSError("只支持 ws:// 或 wss://（收到 %r）" % (url or ""))
        self.scheme = u.scheme
        self.host = u.hostname or ""
        self.port = int(u.port or (443 if u.scheme == "wss" else 80))
        self.path = (u.path or "/") + (("?" + u.query) if u.query else "")
        self.extra_headers = dict(headers or {})
        self.origin = origin
        self.timeout = float(timeout)
        self.sock = None
        self._send_lock = threading.Lock()
        self._frag = bytearray()
        self._frag_op = 0
        self.handshake_key = ""
        self.closed = False

    # ---------------- 连接 / 关闭 ----------------
    def connect(self, timeout: float | None = None) -> dict:
        """握手。成功返回响应头 dict；失败抛 WSError（带中文原因）。"""
        if not self.host:
            raise WSError("URL 里没有主机名")
        to = float(timeout if timeout is not None else self.timeout)
        raw = socket.create_connection((self.host, self.port), timeout=to)
        if self.scheme == "wss":
            ctx = ssl.create_default_context()
            raw = ctx.wrap_socket(raw, server_hostname=self.host)
        raw.settimeout(to)
        self.sock = raw

        self.handshake_key = base64.b64encode(os.urandom(16)).decode("ascii")
        lines = [
            "GET %s HTTP/1.1" % (self.path or "/"),
            "Host: %s%s" % (self.host,
                            "" if self.port in (80, 443) else ":%d" % self.port),
            "Upgrade: websocket",
            "Connection: Upgrade",
            "Sec-WebSocket-Key: %s" % self.handshake_key,
            "Sec-WebSocket-Version: 13",
        ]
        if self.origin:
            lines.append("Origin: %s" % self.origin)
        for k, v in self.extra_headers.items():
            lines.append("%s: %s" % (k, v))
        req = ("\r\n".join(lines) + "\r\n\r\n").encode("utf-8")
        try:
            raw.sendall(req)
            head = b""
            while b"\r\n\r\n" not in head:
                b1 = raw.recv(1)
                if not b1:
                    raise WSError("握手时连接被关闭（没收到响应头）")
                head += b1
                if len(head) > 64 * 1024:
                    raise WSError("响应头异常地长（>64KB），已放弃")
        except socket.timeout:
            raise WSError("握手超时（%ss）—— 检查网络 / 域名 / 端口" % to)

        head_txt = head.decode("latin-1")
        first = head_txt.split("\r\n", 1)[0]
        if " 101" not in first:
            raise WSError("握手被拒：%s" % first.strip()[:120])
        hdrs = {}
        for ln in head_txt.split("\r\n")[1:]:
            if ":" in ln:
                k, v = ln.split(":", 1)
                hdrs[k.strip().lower()] = v.strip()
        expect = base64.b64encode(
            hashlib.sha1((self.handshake_key + _GUID).encode("ascii")).digest()
        ).decode("ascii")
        got = hdrs.get("sec-websocket-accept", "")
        if got and got != expect:
            raise WSError("Sec-WebSocket-Accept 不匹配（对端不是标准 WebSocket？）")
        return hdrs

    def close(self, code: int = 1000) -> None:
        self.closed = True
        try:
            if self.sock is not None:
                self._send_frame(OP_CLOSE, struct.pack(">H", int(code)))
        except Exception:                                    # noqa: BLE001
            pass
        try:
            if self.sock is not None:
                self.sock.close()
        except Exception:                                    # noqa: BLE001
            pass
        self.sock = None

    # ---------------- 发送 ----------------
    def _send_frame(self, opcode: int, payload: bytes) -> None:
        if self.sock is None:
            raise WSError("还没连接")
        n = len(payload)
        head = bytearray([0x80 | opcode])
        if n < 126:
            head.append(0x80 | n)
        elif n < 65536:
            head.append(0x80 | 126)
            head += struct.pack(">H", n)
        else:
            head.append(0x80 | 127)
            head += struct.pack(">Q", n)
        mask = os.urandom(4)
        head += mask
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        with self._send_lock:
            self.sock.sendall(bytes(head) + masked)

    def send_text(self, text: str) -> None:
        self._send_frame(OP_TEXT, text.encode("utf-8"))

    def send_binary(self, data: bytes) -> None:
        self._send_frame(OP_BIN, data)

    def send_pong(self, data: bytes = b"") -> None:
        self._send_frame(OP_PONG, data)

    def send_ping(self, data: bytes = b"") -> None:
        self._send_frame(OP_PING, data)

    # ---------------- 接收 ----------------
    def _read_one(self, timeout: float | None):
        """读**一个**原始帧 → (fin, opcode, payload)。超时返回 None。"""
        to = timeout
        self.sock.settimeout(to)
        try:
            b0, b1 = _recv_exact(self.sock, 2)
        except socket.timeout:
            return None
        except WSError:
            raise
        fin = bool(b0 & 0x80)
        opcode = b0 & 0x0F
        masked = bool(b1 & 0x80)
        ln = b1 & 0x7F
        if ln == 126:
            ln = struct.unpack(">H", _recv_exact(self.sock, 2))[0]
        elif ln == 127:
            ln = struct.unpack(">Q", _recv_exact(self.sock, 8))[0]
        if ln > _MAX_FRAME:
            raise WSError("对端发来 %d 字节的帧，超过上限 %d" % (ln, _MAX_FRAME))
        mask = _recv_exact(self.sock, 4) if masked else b""
        data = _recv_exact(self.sock, ln) if ln else b""
        if masked and data:                     # 服务端不该掩码，但按协议得还原
            data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        return fin, opcode, data

    def recv(self, timeout: float | None = None) -> dict | None:
        """收一条**完整消息**。返回 {opcode, data}；超时返回 None；对端关闭抛 WSError。

        会自动处理：ping→回 pong、分片重组、close→抛 WSError("对端关闭")。
        """
        while True:
            got = self._read_one(timeout)
            if got is None:
                return None
            fin, opcode, data = got
            if opcode == OP_PING:
                try:
                    self.send_pong(data)
                except Exception:                            # noqa: BLE001
                    pass
                continue
            if opcode == OP_PONG:
                continue
            if opcode == OP_CLOSE:
                self.closed = True
                raise WSError("对端关闭了连接（close frame）")
            if opcode == OP_CONT:
                if not self._frag_op:
                    continue                                 # 没头没尾的续帧：丢掉
                self._frag += data
                if fin:
                    data = bytes(self._frag)
                    self._frag = bytearray()
                    op, self._frag_op = self._frag_op, 0
                    return {"opcode": op, "data": data}
                continue
            if opcode in (OP_TEXT, OP_BIN):
                if fin:
                    return {"opcode": opcode, "data": data}
                self._frag_op = opcode
                self._frag = bytearray(data)


def selftest() -> int:
    """纯协议自检：不联网，只验证握手 key/掩码/帧头编解码的确定性部分。"""
    fails = []
    ok = 0

    def chk(label, cond):
        nonlocal ok
        if cond:
            ok += 1
        else:
            fails.append(label)

    # ① 握手 accept 计算（RFC6455 官方示例向量）
    k = "dGhlIHNhbXBsZSBub25jZQ=="
    acc = base64.b64encode(hashlib.sha1((k + _GUID).encode()).digest()).decode()
    chk("Sec-WebSocket-Accept 与 RFC6455 示例一致",
        acc == "s3pPLMBiTxaQ9kYGzzhZRbK+xOo=")

    # ② URL 解析
    c = WSClient("wss://open.feishu.cn/callback/ws/endpoint?a=1")
    chk("wss 默认端口 443", c.port == 443)
    chk("path+query 保留", c.path == "/callback/ws/endpoint?a=1")
    c2 = WSClient("ws://127.0.0.1:9000/x")
    chk("ws 自定义端口", c2.port == 9000)
    try:
        WSClient("http://x")
        chk("非 ws 协议要报错", False)
    except WSError:
        chk("非 ws 协议要报错", True)

    # ③ 掩码帧构造：客户端帧必须带 mask 位
    class _FakeSock:
        def __init__(self):
            self.buf = b""

        def sendall(self, b):
            self.buf += b

    cli = WSClient("ws://127.0.0.1:1/")
    cli.sock = _FakeSock()
    cli.send_text("hi")
    raw = cli.sock.buf
    chk("文本帧 FIN+opcode=1", raw[0] == 0x81)
    chk("客户端帧必须掩码（0x80 位）", bool(raw[1] & 0x80))
    chk("长度 2", (raw[1] & 0x7F) == 2)
    mask, data = raw[2:6], raw[6:8]
    chk("掩码可还原明文",
        bytes(b ^ mask[i % 4] for i, b in enumerate(data)) == b"hi")

    # ④ 长度边界：126 用 2 字节扩展、65536 用 8 字节扩展
    cli.sock = _FakeSock()
    cli.send_text("x" * 200)
    raw = cli.sock.buf
    chk("200 字节 → 扩展长度 126", (raw[1] & 0x7F) == 126)
    chk("扩展长度值正确", struct.unpack(">H", raw[2:4])[0] == 200)
    cli.sock = _FakeSock()
    cli.send_text("y" * 70000)
    raw = cli.sock.buf
    chk("70000 字节 → 扩展长度 127", (raw[1] & 0x7F) == 127)
    chk("8 字节长度值正确", struct.unpack(">Q", raw[2:10])[0] == 70000)

    if fails:
        print("wsclient 自检失败 %d 项：" % len(fails))
        for f in fails:
            print("  ✗", f)
        return 1
    print("wsclient 自检通过（%d 项）" % ok)
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(selftest())
