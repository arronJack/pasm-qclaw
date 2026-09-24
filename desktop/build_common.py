# -*- coding: utf-8 -*-
"""构建参数的**单一真相源** —— Windows 的 `PASMStudio.spec` 与 Linux/macOS 的
CLI 构建脚本（`build_linux.sh` / `build_macos.sh`）共用这一份。

⚠️ 本文件是**唯一真相源**：`PASMStudio.spec`（Windows）import 它，
`build_linux.sh` / `build_macos.sh` 用 `python3 desktop/build_common.py cli` 取命令行参数。
**加新模块请改这里**，不要再往 spec 里堆参数——那正是当初漂移出问题的原因。

为什么要单独抽出来
------------------
2026-09-20 实测：spec 与裸 CLI 是两条独立维护的参数路径，CLI 那条**漏了**：
  · `datas` 里的 `desktop/skills`（技能库资源）
  · 115 个**懒加载** hiddenimports（`executors` / `media_job` / `agent_tools` /
    `platform_ops` / `cog` / `pet3d.*` / `workflow_engine` …）
  · `collect_all('playwright')`（浏览器自动化驱动，含 node.exe ~88MB）
后果：Linux/macOS 包在冻结版里这些能力**静默失效** —— 而冻结冒烟**照样 PASS**
（懒加载的模块不 import 就不报错）。两条路必然漂移，所以合成一份。

`HIDDEN_IMPORTS` 的分组含义（按加进来的批次）
--------------------------------------------
（历史上每组都对应一次"功能没反应"的排障；名字按原顺序保留）
  门面/工具 · 符号与世界模型（facts/worldmodel，动态 `__import__`）·
  引擎接口（`pasm.engine_api`、`pasm.cognitive.*`）·
  第三方接入（connector_*，全部函数内懒加载）·
  3D 小人（pet3d.*）、附件/权限/转场/语音唤醒/迁移 ·
  能力注册表与自主 Tick（capability/autopilot/context_assembly）·
  工作流与广告设计（workflow_engine/ad_design，彼此函数内互引）·
  工作根与产物分类（workspace）· 框架/收单/MCP（scaffold/payment/mcp_bridge）·
  媒体（planner/videoeng）· 界面（effect_view + PySide6 QtWebEngine/Quick/Qml）

用法
----
    # spec 里
    from build_common import HIDDEN_IMPORTS, DATAS, EXCLUDES, playwright_parts

    # shell 脚本里
    EXTRA="$(python3 desktop/build_common.py cli)"
    python3 -m PyInstaller --noconfirm ... desktop/pasm_main.py $EXTRA
"""
from __future__ import annotations

import os
import sys

#: 需要**显式声明**的模块。几乎都是**函数内懒加载** —— 静态分析看不见，
#: 不声明就会在 frozen 版里 `ModuleNotFoundError`，且常被 try/except 吞掉，
#: 表现为"功能没反应"。**新增桌面模块请加到这里。**
HIDDEN_IMPORTS = [
    'executors',
    'media_job',
    'kb_bridge',
    'msg_source',
    'asr',
    'updater',
    'qt_compat',
    'pasm_light',
    'agent_tools',
    'knowledge',
    'growth',
    'offline_brain',
    'cog',
    'memory_layers',
    'todo',
    'pet_avatar',
    'skillstore',
    'llm_gateway',
    'creators',
    'audio',
    'sysops',
    'cando',
    'platform_ops',
    'accent',
    'persona_style',
    'worklog',
    'symbolic',
    'memrouter',
    'memvec',
    'workctx',
    'facts',
    'worldmodel',
    'engine_factory',
    'browser_agent',
    'live_data',
    'scheduler',
    'connector_mail',
    'connector_calendar',
    'remote_bridge',
    'self_evolve',
    'connector_hub',
    'connector_feishu',
    'connector_discord',
    'connector_wechat',
    'connector_webhook',
    'connector_http',
    'wsclient',
    'pasm.engine_api',
    'pasm.cognitive.agent_team',
    'pasm.cognitive.mathlab',
    'pasm.cognitive.cog',
    'pasm.cognitive.memory_layers',
    'pasm.cognitive.percept',
    'pasm.cognitive.quantum',
    'pasm.cognitive.selfheal',
    'pasm.cognitive.coder',
    'pasm.cognitive.symbolic',
    'pasm.cognitive.memrouter',
    'pasm.cognitive.memvec',
    'pasm.cognitive.learning',
    'pasm.cognitive.workctx',
    'pasm.cognitive.facts',
    'pasm.cognitive.worldmodel',
    'pasm.cognitive.ir',
    'pasm.cognitive.irextract',
    'pasm.cognitive.operators',
    'pasm.cognitive.planning',
    'pasm.cognitive.verifier',
    'attachments',
    'permission',
    'transition',
    'wakeword',
    'option_prompt',
    'process_narration',
    'self_verify',
    'migrate',
    'autostart',
    'docsuite',
    'logsetup',
    'pet3d',
    'pet3d.glconst',
    'pet3d.m4',
    'pet3d.mesh',
    'pet3d.mat',
    'pet3d.rig',
    'pet3d.anim',
    'pet3d.renderer',
    'pet3d.scene',
    'edge_tts',
    'edge_tts.communicate',
    'aiohttp',
    'workflow_engine',
    'ad_design',
    'capability',
    'autopilot',
    'context_assembly',
    'ui_tech',
    'pet_tuning',
    'workspace',
    'pasm.cognitive.workspace',
    'scaffold',
    'payment',
    'mcp_bridge',
    'planner',
    'videoeng',
    'pasm_pet',
    'pet_behavior',
    'agent_team',
    'coder',
    'selfheal',
    'effect_view',
    'PySide6.QtWebEngineWidgets',
    'PySide6.QtWebEngineCore',
    'PySide6.QtWebEngineQuick',
    'PySide6.QtQuick',
    'PySide6.QtQuickWidgets',
    'PySide6.QtQml',
    'PySide6.QtWebChannel',
    # v0.31.2：场景导入（读 scenarios/*.json → 人格 + 知识）。
    # ⚠️ 必须登记：pasm_companion 里是**函数内延迟导入**（省启动时间），
    #    PyInstaller 静态分析看不见 → frozen 版会静默丢掉整个「📦 场景」页。
    'scenario',
]

#: 要一起打进包的**资源目录**：(源路径, 包内路径)
DATAS = [
    ("desktop/assets", "assets"),
    ("desktop/skills", "skills"),
]

#: 明确排除完整引擎及其 torch 依赖链（防打包机装了 torch 时误打进 ~1GB 依赖）
#: ⚠ `numpy` **不在此列** —— `pasm/cognitive/mathlab.py`（数学脑）自己用它，
#:   排掉会让"表格分析"能力在产物里直接消失（详见 v0.30.8 的取证）。
PASM_HEAVY = [
    'pasm.agent',
    'pasm.config',
    'pasm.envs',
    'pasm.modules',
    'pasm.training',
    'pasm.narrator',
    'pasm.memory_tag',
    'pasm.cli',
    'pasm.__main__',
    'torch',
]

EXCLUDES = ["matplotlib", "pytest", "uvicorn", "fastapi"] + PASM_HEAVY

#: UPX 会损坏的可执行文件（压了之后进程起不来，且往往**只表现为功能静默失效**）
UPX_EXCLUDE = ["playwright/driver/node.exe", "QtWebEngineProcess.exe"]


def playwright_parts():
    """尝试收集 playwright（含驱动）。构建机没装就返回空 —— 不算失败。

    返回 (datas, binaries, hiddenimports, upx_exclude)。
    """
    try:
        from PyInstaller.utils.hooks import collect_all
    except Exception:
        return [], [], [], []
    try:
        d, b, h = collect_all("playwright")
        return d, b, h, list(UPX_EXCLUDE)
    except Exception:
        return [], [], [], []


def _repo_root() -> str:
    return os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))


def cli_args() -> list:
    """把上面这些参数转成 PyInstaller **命令行**参数（供 shell 脚本用）。"""
    # ⚠️ 必须是命令行的 `--paths`（不是 spec 字段名 `--pathex`）。
    #    Linux/macOS 通过 `build_common.py lines` 把这些参数直接喂给 pyinstaller 命令行，
    #    `--pathex` 不是合法命令行参数 → pyinstaller 直接报 unrecognized arguments。
    args = ["--paths", _repo_root()]
    for m in EXCLUDES:
        args += ["--exclude-module", m]
    # ⚠️ `--hidden-import` 在 PyInstaller 里是 `action='append'`，**不按逗号拆分** ——
    #    写成 `--hidden-import a,b,c` 会被当成**一个**叫 "a,b,c" 的模块，
    #    于是所有名字**一个都没进包**，而构建照样成功（静默失效）。
    #    必须每个模块一个参数。
    for m in HIDDEN_IMPORTS:
        args += ["--hidden-import", m]
    sep = ";" if os.name == "nt" else ":"          # --add-data 分隔符按平台
    for src, dst in DATAS:
        args.append("--add-data=%s%s%s" % (src, sep, dst))
    try:
        import playwright  # noqa: F401
    except Exception:
        print("[build_common] 未安装 playwright → 跳过（浏览器能力届时优雅提示）",
              file=sys.stderr)
    else:
        args += ["--collect-all", "playwright"]
    for f in UPX_EXCLUDE:
        args += ["--upx-exclude", f]
    return args


def main() -> int:
    what = sys.argv[1] if len(sys.argv) > 1 else "cli"
    if what == "cli":
        print(" ".join('"%s"' % a if " " in a else a for a in cli_args()))
        return 0
    if what == "lines":
        # 每行一个参数 —— 给 shell 用 `mapfile -t EXTRA < <(...)` 接收，
        # 比 cli 更适合有空格/特殊字符的路径（不用 eval）。
        for a in cli_args():
            print(a)
        return 0
    if what == "json":
        import json
        print(json.dumps({"hiddenimports": HIDDEN_IMPORTS, "datas": DATAS,
                          "excludes": EXCLUDES, "upx_exclude": UPX_EXCLUDE},
                         ensure_ascii=False, indent=2))
        return 0
    print("用法: build_common.py [cli|lines|json]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
