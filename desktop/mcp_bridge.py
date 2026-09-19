# -*- coding: utf-8 -*-
"""mcp_bridge.py —— PASM Studio 与 pasm-mcp-server 的 stdio MCP 客户端桥（v0.30.10 / #11）。

把「pasm-mcp-server」暴露的 PASM 认知工具（pasm_observe / pasm_context / pasm_feel …
共 13 个）接进桌面端的「工作 / 聊天」工具总线，让模型在工作流里真能调这些 MCP 工具。

设计原则（对照桌面端「绝不假装成功 / 失败即降级」铁律）：
  · 连接是**懒加载 + 失败缓存**——连不上就返回 None，APP 完全不受影响（不会崩、不会卡）；
  · MCP 工具名在喂给模型时统一加 `mcp_` 前缀（避免与本地 recall/verify/learn 撞名），
    调用时自动剥前缀再发给 server；
  · 任何异常都被吞掉并降级成"MCP 未连接/工具失败"文案，绝不上抛到对话主路径。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

_PROTOCOL = "2025-03-26"


def _detect_cmd_and_cwd():
    """返回 (cmd_list, cwd)。优先用装好的 console_script，否则退回 `-m pasm_mcp_server`。"""
    exe = shutil.which("pasm_mcp_server")
    if exe:
        return [exe], None
    here = os.path.dirname(os.path.abspath(__file__))
    # 六仓布局：desktop 与 pasm-mcp-server 是同级兄弟仓（具体路径见仓库根生态索引）
    candidates = [
        os.path.normpath(os.path.join(here, "..", "..", "pasm-mcp-server")),
        os.path.normpath(os.path.join(here, "..", "pasm-mcp-server")),
    ]
    for c in candidates:
        if os.path.isdir(os.path.join(c, "pasm_mcp_server")):
            return [sys.executable, "-m", "pasm_mcp_server"], c
    return [sys.executable, "-m", "pasm_mcp_server"], None


class MCPBridge:
    def __init__(self, timeout: float = 25.0):
        self._proc = None
        self._id = 0
        self._tools = {}
        self._timeout = timeout
        self._cmd, self._cwd = _detect_cmd_and_cwd()

    # ---------------- 连接 ----------------
    def connect(self) -> bool:
        if self._proc is not None:
            return True
        env = dict(os.environ)
        # 本地开发便利：pasm-skills 没装时退回到兄弟仓（与 stdio_client_demo 一致）
        sibling = os.path.normpath(os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "..", "..", "pasm-skills"))
        if os.path.isdir(os.path.join(sibling, "pasm_skills")):
            env["PYTHONPATH"] = sibling + os.pathsep + env.get("PYTHONPATH", "")
        try:
            self._proc = subprocess.Popen(
                self._cmd, cwd=self._cwd,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", env=env, bufsize=1)
            self._send("initialize", {
                "protocolVersion": _PROTOCOL,
                "clientInfo": {"name": "pasm-studio", "version": "0.30.10"}})
            self._recv()
            self._send("notifications/initialized", {}, notify=True)
            self._send("tools/list")
            r = self._recv()
            for t in (r.get("result", {}).get("tools", []) or []):
                self._tools[t["name"]] = t
            return True
        except Exception as e:  # noqa: BLE001
            self._safe_close()
            self._last_error = str(e)
            return False

    def _send(self, method, params=None, notify=False):
        self._id += 1
        msg = {"jsonrpc": "2.0", "method": method}
        if not notify:
            msg["id"] = self._id
        if params is not None:
            msg["params"] = params
        self._proc.stdin.write(json.dumps(msg, ensure_ascii=False) + "\n")
        self._proc.stdin.flush()

    def _recv(self):
        line = self._proc.stdout.readline()
        if not line:
            return None
        return json.loads(line)

    # ---------------- 对桌面端暴露的接口 ----------------
    @property
    def available(self) -> bool:
        return bool(self._tools)

    def tool_names(self):
        return list(self._tools.keys())

    def openai_tools(self):
        """转成 OpenAI 风格 tool schema，统一加 `mcp_` 前缀避免与本地工具撞名。"""
        out = []
        for name, t in self._tools.items():
            out.append({
                "type": "function",
                "function": {
                    "name": "mcp_" + name,
                    "description": (t.get("description") or "")[:600],
                    "parameters": t.get("inputSchema",
                                       {"type": "object", "properties": {}}),
                }})
        return out

    def call(self, name: str, args: dict):
        """name 是带 `mcp_` 前缀的名字；自动剥前缀后发给 server。返回字符串结果。"""
        real = name[4:] if name.startswith("mcp_") else name
        try:
            if not self.connect():
                return "⚠️ MCP 未连接（pasm-mcp-server 未安装或启动失败）"
            self._send("tools/call", {"name": real, "arguments": args or {}})
            r = self._recv()
            if not r:
                return "⚠️ MCP 工具无响应"
            if "error" in r:
                return "⚠️ MCP 工具协议错误：%s" % (r["error"].get("message", r["error"]))
            res = r.get("result", {})
            if res.get("isError"):
                sc = res.get("structuredContent") or {}
                return "⚠️ MCP 工具执行失败：" + str(sc.get("error") or res.get("content"))
            sc = res.get("structuredContent")
            if sc is not None:
                return json.dumps(sc, ensure_ascii=False)[:4000]
            content = res.get("content") or []
            parts = [c.get("text", "") for c in content
                     if isinstance(c, dict) and c.get("type") == "text"]
            return "\n".join(parts)[:4000]
        except Exception as e:  # noqa: BLE001
            return "⚠️ MCP 调用异常：%s" % e

    def _safe_close(self):
        if self._proc is None:
            return
        try:
            self._proc.stdin.close()
        except Exception:
            pass
        try:
            self._proc.wait(timeout=5)
        except Exception:
            try:
                self._proc.kill()
            except Exception:
                pass
        self._proc = None

    def close(self):
        self._safe_close()


# ---------------- 单例（带失败缓存）----------------
_BRIDGE = None
_FAILED = False


def get_bridge() -> "MCPBridge | None":
    """全局懒加载单例；连不上就把 `_FAILED` 置位，避免每次调用都重连。"""
    global _BRIDGE, _FAILED
    if _FAILED:
        return None
    if _BRIDGE is None:
        _BRIDGE = MCPBridge()
        if not _BRIDGE.connect():
            _FAILED = True
            _BRIDGE = None
            return None
    return _BRIDGE


def reset_cache():
    """测试 / 设置变更后清缓存用。"""
    global _BRIDGE, _FAILED
    if _BRIDGE is not None:
        _BRIDGE.close()
    _BRIDGE, _FAILED = None, False


if __name__ == "__main__":
    b = get_bridge()
    if not b:
        print("MCP 不可用：", getattr(_BRIDGE, "_last_error", "连接失败"))
        raise SystemExit(2)
    print("MCP 已连接，工具：", b.tool_names())
    print("openai_tools 数量：", len(b.openai_tools()))
    print("MCPBRIDGE_OK")
