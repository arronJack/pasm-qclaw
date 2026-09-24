#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""check_core_fork.py —— 分叉守卫：同一份能力只准有一处实现。

为什么必须有这个脚本：
    memory_layers 曾经**双向分叉**——核心版只有"按重要度淘汰"，桌面版只有
    "遗忘曲线 / 召回巩固 / 语义印象"，两边各持一半能力。结果桌面端跑了很久
    一个核心早已修好的 bug（重要记忆被日常琐事挤掉），而验证套件全绿：
    因为套件测的是核心那份，**产品跑的是桌面那份**（"验 A 跑 B"）。
    这种分叉不会报错、不会崩，只会静默地让产品越来越差 —— 所以要用工具守。

做三件事：
    ① 影子模块必须是**薄壳**：desktop/<name>.py 若与核心模块同名，只允许是
       再导出（行数上限 + 必须含薄壳标记），否则报 FAIL；
    ② **运行时身份校验**（决定性）：从 desktop 导入得到的模块对象，必须
       `is` 核心同名模块对象。属性拷贝式薄壳若漏了可变全局量，这里会露馅；
    ③ 全仓扫描**字节级重复副本**：任何核心模块的完整副本出现在别处 → WARN
       （今天是副本，明天就是分叉）。

用法：
    python tools/check_core_fork.py            # 人类可读报告
    python tools/check_core_fork.py --json     # 机读（CI / 发布前置检查）
退出码：0 通过 / 1 发现分叉。
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DESKTOP = os.path.join(ROOT, "desktop")
CORE_PKG = os.path.join(ROOT, "pasm")

#: 薄壳标记：必须命中至少一个
SHELL_MARKERS = ("再导出薄壳", "sys.modules[__name__]", "兼容门面",
                 "re-export", "re-export", "唯一实现位于")
#: 薄壳允许的最大行数（超过就基本不可能是再导出）
MAX_SHELL_LINES = 45

#: 已知且**允许**的例外（desktop 独有模块，核心没有同名实现，不算分叉）
#:
#: ⚠️ 这不是"绕过警告的白名单"。登记前必须逐条自查（2026-09-15 新增）：
#:   ① 承载的是**产品层**能力 —— UI / 系统集成 / 文件 IO / 安全策略；
#:      认知类能力（记忆·事实·世界模型·规划·验证·学习）一律归 pasm/cognitive/，
#:      放进 desktop 就是分叉，警告是对的，不许登记。
#:   ② 已在核心全量搜索过，确认既无**同名实现**、也无**等价能力**。
#:   ③ 名字未被核心占用、且将来也不该被核心占用（先占先得，防同名跨层冲突）。
#: 新增 desktop 模块时：先按上面三条自查，再把名字加进来。
ALLOWED_UNIQUE = {
    "executors",
    "media_job",
    "kb_bridge",
    "msg_source","accent", "ad_design", "agent_tools", "appinfo", "asr",
                  "attachments",
                  "audio", "autopilot", "autostart", "browser_agent", "cando",
                  "capability", "context_assembly",
                  "connector_calendar", "connector_mail", "creators",
                  "docsuite", "logsetup",
                  "engine_factory", "frozen_smoke", "growth", "knowledge",
                  "live_data", "llm_gateway", "migrate", "offline_brain",
                  "pasm_companion", "pasm_desktop", "pasm_light", "pasm_main",
                  "pasm_pet", "permission", "persona_style", "pet_avatar",
                  "pet_behavior", "planner", "prompts", "qt_compat",
                  "remote_bridge", "scheduler", "self_evolve", "skillstore",
                  "sysops", "todo", "transition", "tts", "updater",
                  "validator", "videoeng", "wakeword", "workflow_engine",
                  "worklog", "ui_tech", "pet_tuning",
                  # v0.30.11 新增（逐条按上面三条自查过：都是**产品层**的
                  # 文件 IO / 系统集成；核心侧既无同名实现、也无等价能力）：
                  "scaffold",     # 真跑 npm/pip 官方脚手架，纯进程与文件 IO
                  "payment",      # 收单适配层落盘 + 沙箱 HMAC 签名（本地协议实现）
                  "mcp_bridge",   # 连 pasm-mcp-server 的客户端桥
                  # v0.30.14 新增（逐条按上面三条自查过：都是**产品层**的
                  # 对外协议/系统集成；核心侧既无同名实现、也无等价能力）：
                  "connector_hub",      # 四个社交通道的启停/状态中枢
                  "connector_feishu",   # 飞书应用：出站 + 事件回调 + 长连接（对飞书协议）
                  "connector_discord",  # Discord：REST 出站 + 网关 WS 入站
                  "connector_wechat",   # 微信公众号（官方路线）+ 企业微信群机器人
                  "connector_webhook",  # 通用 Webhook 入站/出站
                  "connector_http",     # 上面几个共用的本地入站端点 + XML 小工具
                  "wsclient",           # 极小 RFC6455 客户端（Discord/飞书长连接共用）
                  # v0.31.2 新增（逐条按上面三条自查过：产品层的**文件 IO**——
                  # 读场景 JSON / CSV / SQLite 并灌进本机资料库；核心侧既无同名
                  # 实现、也无等价能力：核心不做"外部智能体场景导入"这件事）：
                  "scenario",           # Studio 场景导入（人格 + 知识源载入）
                  # 2026-09-21 补登记（**既有遗漏**，跑守门时报 WARN 才暴露）：
                  # 三者核心侧均无同名文件、也无任何引用，且都是产品层能力：
                  "build_common",       # 构建参数单一真相源（打包配置，非认知）
                  "effect_view",        # 右栏「产出」栏渲染器（UI）
                  "platform_ops",       # 跨平台系统操作层（os.startfile 等的统一封装）
                  # v0.31.3 新增：聊天「多选项确认卡」纯逻辑层（解析/渲染/判定），
                  # 零 Qt 依赖、产品层 UI 能力；核心侧无同名实现、也无等价能力。
                  "option_prompt",
                  # v0.31.4 新增：过程卡 WorkBuddy 式叙述层（时长格式化/叙述句/步尾耗时），
                  # 零 Qt 依赖、纯文本输出；核心侧无同名实现。
                  "process_narration",
                  # v0.31.7 新增：目录事实卡（量化指标 + 风险素材），零 Qt、只读；
                  # 核心侧无同名实现 —— 它服务的是"让 4B 本地模型也能给出量化+风险"。
                  "repo_metrics",
                  # v0.31.5 新增：自我复查闭环纯逻辑层（验证计划/执行/修复prompt），
                  # 零 Qt 依赖；核心侧无同名实现。
                  "self_verify",
                  }


def _hidden_imports_from_spec(spec_path: str) -> set:
    """取出打包**真正**的 hiddenimports 清单。

    ⚠️ 真相源是 ``desktop/build_common.HIDDEN_IMPORTS``：spec 里现在写的是
    ``hiddenimports = list(HIDDEN_IMPORTS)``（AST 里是 **Call** 节点），
    只从字面量抠的话**一个都取不到** → 这道守门会静默失效
    （恒报"失败 1"却指不出谁漏了，久了就没人看）。2026-09-21 修。
    """
    try:
        import importlib.util
        bc = os.path.join(DESKTOP, "build_common.py")
        if os.path.exists(bc):
            spec_ = importlib.util.spec_from_file_location("_bc_fork_check", bc)
            mod = importlib.util.module_from_spec(spec_)
            spec_.loader.exec_module(mod)
            names = {str(x) for x in (getattr(mod, "HIDDEN_IMPORTS", None) or [])}
            if names:
                return names
    except Exception:                                           # noqa: BLE001
        pass
    # 兜底：老式 spec（直接把字面量写在文件里）
    try:
        import ast
        with open(spec_path, "r", encoding="utf-8", errors="replace") as f:
            tree = ast.parse(f.read())
        hidden = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and any(
                    getattr(t, "id", None) == "hiddenimports" for t in node.targets):
                hidden |= {e.value for e in getattr(node.value, "elts", [])
                           if isinstance(e, ast.Constant)}
        return hidden
    except Exception:                                           # noqa: BLE001
        return set()


def _core_modules() -> dict:
    """核心包里的模块名 -> 文件路径。"""
    out = {}
    cog = os.path.join(CORE_PKG, "cognitive")
    if not os.path.isdir(cog):
        return out
    for fn in sorted(os.listdir(cog)):
        if fn.endswith(".py") and not fn.startswith("__"):
            out[fn[:-3]] = os.path.join(cog, fn)
    return out


def _lines(path: str) -> int:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return sum(1 for _ in f)
    except Exception:
        return -1


def _sha(path: str) -> str:
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read().replace(b"\r\n", b"\n")).hexdigest()
    except Exception:
        return ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    core = _core_modules()
    findings = []          # {"level","what","detail"}

    # ---------------- ① 影子模块必须是薄壳 ----------------
    shells = {}
    if os.path.isdir(DESKTOP):
        for fn in sorted(os.listdir(DESKTOP)):
            if not fn.endswith(".py") or fn.startswith("__"):
                continue
            name = fn[:-3]
            if name not in core:
                if name not in ALLOWED_UNIQUE:
                    findings.append({"level": "warn", "what": "desktop 独有模块未登记",
                                     "detail": f"desktop/{fn} 不在常见清单里，"
                                               f"若是新模块请加进 ALLOWED_UNIQUE"})
                continue
            path = os.path.join(DESKTOP, fn)
            src = ""
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    src = f.read()
            except Exception:
                pass
            is_shell = any(m in src for m in SHELL_MARKERS)
            n = _lines(path)
            if not is_shell:
                findings.append({"level": "fail", "what": "影子模块不是薄壳",
                                 "detail": f"desktop/{fn} 与核心同名却含完整实现"
                                           f"（{n} 行）→ 这就是分叉的起点，"
                                           f"请改为再导出薄壳"})
            elif n > MAX_SHELL_LINES:
                findings.append({"level": "fail", "what": "薄壳过长",
                                 "detail": f"desktop/{fn} 有 {n} 行（上限 "
                                           f"{MAX_SHELL_LINES}）→ 疑似实现混入薄壳"})
            else:
                shells[name] = path

    # ---------------- ② 运行时身份校验（决定性） ----------------
    if shells:
        sys.path.insert(0, ROOT)
        sys.path.insert(0, DESKTOP)
        for name in sorted(shells):
            try:
                d_mod = importlib.import_module(name)          # 走 desktop 薄壳
                c_mod = importlib.import_module("pasm.cognitive." + name)
            except Exception as ex:
                findings.append({"level": "fail", "what": "薄壳导入失败",
                                 "detail": f"{name}: {type(ex).__name__}: {ex}"})
                continue
            if d_mod is not c_mod:
                findings.append({
                    "level": "fail", "what": "薄壳未指向核心模块本体",
                    "detail": f"{name}: desktop 导入得到 {getattr(d_mod,'__file__','?')}，"
                              f"核心是 {getattr(c_mod,'__file__','?')} → "
                              f"属性拷贝会让模块级状态（DATA_DIR 等）变成陈旧副本，"
                              f"必须用 sys.modules[__name__] 别名"})
            else:
                findings.append({"level": "ok", "what": "薄壳身份一致",
                                 "detail": f"{name} → pasm.cognitive.{name}"})

    # ---------------- ③ 字节级重复副本扫描 ----------------
    core_sha = {}
    for name, path in core.items():
        core_sha.setdefault(_sha(path), []).append(path)
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames
                       if d not in (".git", "__pycache__", ".venv", "node_modules",
                                    "build", "dist")]
        for fn in filenames:
            if not fn.endswith(".py"):
                continue
            p = os.path.join(dirpath, fn)
            if os.path.dirname(p) == os.path.dirname(next(iter(core.values()), "")):
                continue
            h = _sha(p)
            if h and h in core_sha:
                origin = core_sha[h][0]
                findings.append({"level": "warn", "what": "核心模块的字节级副本",
                                 "detail": f"{os.path.relpath(p, ROOT)} 与 "
                                           f"{os.path.relpath(origin, ROOT)} 完全相同"
                                           f" → 今天只是副本，明天就是分叉"})

    # ---------------- ④ 打包守门：hiddenimports 必须覆盖全部影子模块 ----------------
    # 为什么：核心侧不少模块是用**动态 __import__(计算名)** 懒加载的，
    # PyInstaller 静态分析看不见 → 漏声明就在 try/except 里**静默**丢掉整层功能
    # （不报错、界面正常、"功能没反应"）。本轮就差点漏掉 facts / worldmodel / workctx。
    spec_path = os.path.join(ROOT, "PASMStudio.spec")
    shadow = sorted(n for n in core if os.path.exists(os.path.join(DESKTOP, n + ".py")))
    if os.path.exists(spec_path) and shadow:
        try:
            hidden = _hidden_imports_from_spec(spec_path)
            missing = [n for n in shadow
                       if n not in hidden and ("pasm.cognitive." + n) not in hidden]
            if missing:
                findings.append({
                    "level": "fail", "what": "打包会静默丢掉整层功能",
                    "detail": f"PASMStudio.spec 的 hiddenimports 漏了："
                              f"{', '.join(missing)} → 懒加载模块 PyInstaller 看不见，"
                              f"frozen 版会在 try/except 里静默失效（不报错、功能没了）"})
            else:
                findings.append({"level": "ok", "what": "打包 hiddenimports 覆盖完整",
                                 "detail": f"{len(shadow)} 个影子模块全部已声明"})
        except Exception as ex:
            findings.append({"level": "warn", "what": "spec 解析失败",
                             "detail": f"{type(ex).__name__}: {ex}"})

    fails = [f for f in findings if f["level"] == "fail"]
    warns = [f for f in findings if f["level"] == "warn"]
    oks = [f for f in findings if f["level"] == "ok"]

    if args.json:
        print(json.dumps({"ok": len(oks), "warn": len(warns),
                          "fail": len(fails), "findings": findings},
                         ensure_ascii=False, indent=1))
    else:
        print("=" * 62)
        print("分叉守卫：同一份能力只准有一处实现")
        print("=" * 62)
        for f in fails:
            print(f"  [FAIL] {f['what']}\n         {f['detail']}")
        for f in warns:
            print(f"  [WARN] {f['what']}\n         {f['detail']}")
        for f in oks:
            print(f"  [OK]   {f['what']}  -- {f['detail']}")
        print("-" * 62)
        print(f"薄壳 {len(oks)} 个 · 警告 {len(warns)} · 失败 {len(fails)}")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
