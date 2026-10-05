# -*- mode: python ; coding: utf-8 -*-
import os
import sys
from PyInstaller.utils.hooks import collect_all

# v0.31.11 修复：collect_all('pasm') 在 spec 执行环境里必须能从仓库根 import 到
# pasm 包 —— 否则 datas（45 个 pasm/*.py）静默丢失、pasm.light/evalkit 掉出 PYZ
# （0.31.11 首次构建实测：_internal/pasm 整目录消失。显式注入 SPECPATH 兜底）。
sys.path.insert(0, SPECPATH)
_p = os.path.normcase(os.path.abspath(__import__('pasm').__file__))
assert _p.startswith(os.path.normcase(os.path.abspath(SPECPATH))), \
    "collect_all('pasm') 前置检查失败：pasm 未从仓库根解析（得到 %s）" % _p

# ★ v0.31.16：exe 的 VERSIONINFO 必须与 appinfo.APP_VERSION 一致 —— 该文件是静态的，
#   本次打包实测它还停在 0.31.14.0（文件属性/杀软启发式看到的是旧版本号）。
#   这里在构建前现读现写，杜绝版本号漂移。
sys.path.insert(0, os.path.join(SPECPATH, 'desktop'))
import build_common                                          # noqa: E402
_VI = build_common.sync_version_info()

datas = [('desktop/assets', 'assets'), ('desktop/skills', 'skills')]
binaries = []
hiddenimports = ['executors', 'media_job', 'kb_bridge', 'msg_source', 'asr', 'updater', 'qt_compat', 'pasm_light', 'agent_tools', 'knowledge', 'growth', 'offline_brain', 'cog', 'memory_layers', 'todo', 'pet_avatar', 'skillstore', 'llm_gateway',
               # ★ v0.31.22：openai 云端 SDK（含依赖链）。llm_gateway 对 openai 是
               #   延迟导入，PyInstaller 静态分析看不见 → 不声明就不进包 →
               #   装好后报 `RuntimeError: openai 库未安装`，云端模型全废。
               #   本版修复：与 build_common.HIDDEN_IMPORTS 同步补齐（两处都要有）。
               'openai', 'httpx', 'httpcore', 'anyio', 'sniffio', 'distro', 'tqdm',
               # 实测已进包但未显式声明的三个（守卫 verify_v0322_frozen_deps 会判红）：
               'pydantic', 'numpy', 'PySide6',
               'creators', 'audio', 'sysops', 'cando', 'platform_ops', 'accent', 'persona_style', 'worklog', 'symbolic', 'memrouter', 'memvec', 'workctx', 'facts', 'worldmodel', 'engine_factory', 'pasm2_bridge', 'pasm2_voice',   # pasm2_voice 在 pasm2_bridge 里'pasm2_voice',
               # ↑ pasm2_voice 在 pasm2_bridge 里是 try 内导入，PyInstaller 静态分析
               #   看不到 → 漏声明会让 frozen 版**静默丢掉整个表达层**（不报错）
               'browser_agent', 'live_data', 'scheduler', 'connector_mail', 'connector_calendar', 'remote_bridge', 'self_evolve', 'connector_hub', 'connector_feishu', 'connector_discord', 'connector_wechat', 'connector_webhook', 'connector_http', 'wsclient', 'pasm.engine_api', 'pasm.cognitive.agent_team', 'pasm.cognitive.mathlab', 'pasm.cognitive.cog', 'pasm.cognitive.memory_layers', 'pasm.cognitive.percept', 'pasm.cognitive.quantum', 'pasm.cognitive.selfheal', 'pasm.cognitive.coder', 'pasm.cognitive.symbolic', 'pasm.cognitive.memrouter', 'pasm.cognitive.memvec', 'pasm.cognitive.learning', 'pasm.cognitive.workctx', 'pasm.cognitive.facts', 'pasm.cognitive.worldmodel', 'pasm.cognitive.ir', 'pasm.cognitive.irextract', 'pasm.cognitive.operators', 'pasm.cognitive.planning', 'pasm.cognitive.verifier', 'attachments', 'permission', 'transition', 'wakeword', 'option_prompt', 'process_narration', 'repo_metrics', 'self_verify', 'migrate', 'autostart', 'docsuite', 'logsetup', 'pet3d', 'pet3d.glconst', 'pet3d.m4', 'pet3d.mesh', 'pet3d.mat', 'pet3d.rig', 'pet3d.anim', 'pet3d.renderer', 'pet3d.scene', 'edge_tts', 'edge_tts.communicate', 'aiohttp', 'workflow_engine', 'ad_design', 'capability', 'capability_bus', 'autopilot', 'context_assembly', 'ui_tech', 'pet_tuning', 'workspace', 'pasm.cognitive.workspace', 'scaffold', 'payment', 'mcp_bridge', 'planner', 'videoeng', 'pasm_pet', 'pet_behavior', 'agent_team', 'coder', 'selfheal', 'effect_view', 'PySide6.QtWebEngineWidgets', 'PySide6.QtWebEngineCore', 'PySide6.QtWebEngineQuick', 'PySide6.QtQuick', 'PySide6.QtQuickWidgets', 'PySide6.QtQml', 'PySide6.QtWebChannel', 'PySide6.QtNetwork', 'scenario',
               # ★ 0.31.18：P0-P2 十项能力模块 —— 它们全在函数内**延迟导入**
               #   （PyInstaller 静态分析看不见），漏声明 = frozen 版静默丢整块能力：
               #   分步生成/工具循环没了、模型不升级云端、上下文没地图、权限门不生效、
               #   失败册/trace 不写、诊断包点不出来。AST 判据见 SKILL §0.3。
               'single_instance',          # 0.31.20 跨进程单实例+唤醒（QLocalServer）
               'power',                    # 0.31.20 定时关机/重启（真执行）
               'devloop', 'model_route', 'repomap', 'toolperm',
               'subagents', 'failbook', 'trace', 'office_edit',
               # ★ 0.31.19：bench（能力基准）与 conncheck（外部链路体检）此前**谁也没 import**
               #   → PyInstaller 不收录 → 装好的软件里「跑个基准」「链路体检」两句指令
               #   必然 ModuleNotFoundError。加进来才算真的能点。
               'bench', 'conncheck',
               'domain_advisor_bridge']

tmp_ret = collect_all('pasm')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('playwright')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
# v0.31.12：V2 认知引擎（pasm2）随包分发 —— 设置里的「实验：启用 V2 认知引擎」
# 勾选后重启即可用，无需用户自行 pip 安装。pasm2 是纯 Python + numpy（numpy 已随包），
# 体积代价极小；collect_all 保证子模块（skills/cognitive/modules/evalkit）一个不落。
tmp_ret = collect_all('pasm2')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['desktop/pasm_main.py'],
    pathex=['E:/AI/pasm/code/PASM'],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['matplotlib', 'pytest', 'uvicorn', 'fastapi', 'pasm.agent', 'pasm.config', 'pasm.envs', 'pasm.modules', 'pasm.training', 'pasm.narrator', 'pasm.memory_tag', 'pasm.cli', 'pasm.__main__', 'torch'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='PASMStudio',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version=_VI,
    icon=['desktop/assets/icon.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=['playwright/driver/node.exe', 'QtWebEngineProcess.exe'],
    name='PASMStudio',
)
