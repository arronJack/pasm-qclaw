"""llm_gateway —— PASM 统一 LLM 网关（v0.16.2）
================================================================
把散在各处的 OpenAI 直连收敛成一个入口，提供三类能力：

1) 网关（Gateway.complete / create）
   - 客户端复用：同一 (base_url, api_key) 只建一个 OpenAI client，底层连接池复用；
   - 统一请求：超时可配、指数退避重试、错误分类、finish=length 自动续写；
   - 并发闸：按端点限流 —— 本地 Ollama 单模型串行（并发 1，防打爆/抢显存），
     云端宽松（并发 4）；排队超过预算抛 LLMBusy，由上层转成一句人话。

2) 路由（task 参数）
   - 按任务类型给默认参数：轻任务（提炼/自测/小人搭话）小 token、低温度、
     短超时；创作/开发/技能重任务给足 token 并允许续写。
   - 调用方仍显式传 base_url/model/api_key 三元组，网关不偷换模型语义。

3) 健康统计
   - 每个端点的成功/失败计数（可展示），失败原因分类写日志。
   - 异常分类：认证/参数错误不重试（快速失败）；网络/超时/限流重试并退避；
     Ollama 模型未就绪（404 not found / 503 loading）给出可读提示。

用法（桌面同目录模块，无 UI 依赖，可独立单测）：
    from llm_gateway import gw
    text = gw.complete(base_url, model, api_key, msgs,
                       task="chat", temperature=0.9, max_tokens=1600)
"""
from __future__ import annotations

import json
import logging
import os
import random
import re
import socket
import threading
import time
import urllib.error
import urllib.request
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

try:                                    # openai 必须在运行时可用（桌面已依赖）
    from openai import OpenAI
    from openai import APIError, APITimeoutError, APIConnectionError, \
        AuthenticationError, PermissionDeniedError, RateLimitError, \
        NotFoundError, BadRequestError
except Exception:                       # 单测/文档导入兜底
    OpenAI = None
    APIError = APITimeoutError = APIConnectionError = AuthenticationError = \
        PermissionDeniedError = RateLimitError = NotFoundError = BadRequestError = Exception

log = logging.getLogger("llm_gateway")

# 任务画像：路由参数默认值（调用方可显式覆盖单个字段）
#  - light 请求尽量小 token/低温度/短超时；重任务给足生成预算
TASK_PROFILES: Dict[str, Dict[str, Any]] = {
    # v0.17.5：整体收紧等待预算——"冷加载/生成中无限等"是"思考 238s 卡死"的帮凶，
    # 超时内没出结果按超时处理（可点「⏹ 停止」立即断开，或换小模型/云 Key）
    "chat":     dict(max_tokens=1600, temperature=0.9,  timeout=90,  retries=1, continue_cuts=2, concurrency=1),
    "tool":     dict(max_tokens=900,  temperature=0.85, timeout=120, retries=0, continue_cuts=0, concurrency=1),
    "brain":    dict(max_tokens=1500, temperature=0.7,  timeout=120, retries=1, continue_cuts=1, concurrency=1),
    "skill":    dict(max_tokens=3000, temperature=0.7,  timeout=180, retries=1, continue_cuts=2, concurrency=1),
    "filegen":  dict(max_tokens=2200, temperature=0.7,  timeout=150, retries=1, continue_cuts=2, concurrency=1),
    "study":    dict(max_tokens=800,  temperature=0.3,  timeout=120, retries=1, continue_cuts=0, concurrency=1),
    "selftest": dict(max_tokens=400,  temperature=0.5,  timeout=120, retries=1, continue_cuts=0, concurrency=1),
    "pet":      dict(max_tokens=240,  temperature=0.95, timeout=90,  retries=1, continue_cuts=0, concurrency=1),
    "warm":     dict(max_tokens=1,    temperature=0.0,  timeout=60,  retries=0, continue_cuts=0, concurrency=1),
}

# v0.18.1：本地端点（Ollama 等）总预算下限——真机实测冷加载 qwen2.5:7b 需 80s+，
# 旧 25s 心跳把它掐死在"加载中"，SDK 重试又整单重发 → 永远跑不完一个窗口（假死循环）。
# 云端预算维持 v0.17.5 收紧不变；本地按任务给足下限，仍可随时「⏹ 停止」。
# v0.27.9：chat 下限 600→300——600s 的静默等待在真机上表现为"几百秒没反应"，
# 300s 已足够覆盖冷加载(≈80s)+长回答，同时把最坏等待收紧到 5 分钟内。
_LOCAL_TIMEOUT_FLOOR: Dict[str, int] = {
    "chat": 300, "tool": 480, "brain": 360, "skill": 480, "filegen": 480,
    "study": 360, "selftest": 300, "pet": 240, "warm": 300,
}
_HB_SEG_CLOUD = 25.0                     # 云端：25s 心跳分片（点停止 ≤25s 断开）
_HB_SEG_LOCAL = 120.0                    # 本地：单段要罩得住冷加载（实测 81s）
# v0.27.11：本地模型常驻时长。真机实测：模型被卸载后冷加载 **72.4s**（其中加载 69.9s），
# 而热模型 **0.17s** —— "聊天慢"的主体其实是冷加载，不是自我学习。
# Ollama 的 OpenAI 兼容端点与原生端点都接受 keep_alive（均实测 HTTP 200，/api/ps 的
# expires_at 随之 +2h）。用 extra_body 传，避免 SDK 因未知参数报 TypeError。
_LOCAL_KEEP_ALIVE = "8h"
# v0.27.12（真机实测，Win10 + RX580 4G + Ryzen 2600X）——三个"等待很久"的真凶：
#  ①【最大】Ollama 的 OpenAI 兼容端点 /v1/chat/completions 会**静默丢弃** keep_alive 与
#    num_ctx（实测：原生端点设 1 分钟驻留后再走兼容端点设 2h，剩余仍只有 1 分钟；
#    ctx 恒为模型默认 65536）→ 所以 0.27.11 走 extra_body 的 keep_alive 从未生效，
#    模型闲置几分钟仍被卸载，下一句白等 **72s 冷加载**。
#  ②【最大】思考链：模型默认开思考（thinking/reasoning），App 只读 content、思考令牌全被丢弃，
#    但时间照付——实测一句"介绍一下你自己"思考 257 片、**39.6s 还没吐正文**；
#    关思考后首字 **0.45s**（约 40~90 倍）。兼容端点认 reasoning_effort="none"，原生端点认 think=false。
#  ③ 上下文：默认 ctx=65536 时模型 5.4G 只有 46% 上 GPU，生成 **6.2 tok/s**；
#    ctx=16384 时 9.5 tok/s（+53%），8192 时 10.1 tok/s。取 16384 兼顾速度与长上下文。
# 结论：本地 Ollama 一律走**原生 /api/chat**（只有它能带 keep_alive/num_ctx/think）；
# 兼容端点仅作兜底，并在兜底时补 reasoning_effort="none"。
_LOCAL_NUM_CTX = 16384

# ================= v0.29.1「本地模型驻留守卫」=================
# 现象（真机 2026-09-14，Qwen3.5-4B / RX580 4G）：同一条闲聊 0.28.5 约 10s，0.29.0 变成 52s。
# 逐项实测定位（不是猜）：
#   · 提示只有 1467 tok、思考链关着、生成 10.4 tok/s —— 与基线一致，**推理侧没有退化**；
#   · 「首字 48.92s」里有 **40.2s 是 Ollama 冷加载**（日志：装载40.2s）；
#   · 0.29.0 新增的认知层（fact_ingest / memrouter / 世界模型 / replay）每轮只花 **约 21ms**。
# 根因：keep_alive 是"逐请求"带的（原生 /api/chat 带 8h），而 **Ollama 服务端的
#      OLLAMA_KEEP_ALIVE 默认只有 5 分钟**（本机实测 env: OLLAMA_KEEP_ALIVE:5m0s）。
#      于是只要出现下列任一情况，模型就被卸载，下一句白等 40s+：
#        ① App 侧超过 5 分钟没有本地请求（用户在打字 / 切窗口 / 想事情）；
#        ② 某条路径掉到 OpenAI 兼容端点兜底 —— 它会**静默丢弃** keep_alive；
#        ③ Ollama 自身重启（本次实测 17:30:53 重启，17:31:59 的首句就白等 40.2s 装载）。
# 对策（双保险，互不依赖）：
#   ① **驻留守卫心跳**：每 _LOCAL_GUARD_GAP 秒用原生 /api/chat 发 1 个 token，
#      把 keep_alive 续到 8h。成本约 1 个 token，换来"不用再等 40s"。
#   ② **请求前体检**：模型不在显存时先**显式预热**（同一条原生通路 + 同一个 num_ctx），
#      把"装载"从正式请求的首字里剥离出来 —— 既不再伪装成"模型卡死/读超时"，
#      也让日志一眼能区分"慢在装载"还是"慢在生成"。
# 关掉办法：设环境变量 PASM_LOCAL_KEEPALIVE=0。
_LOCAL_GUARD_GAP = 240.0        # 秒：多久体检一次（体检本身零成本，只是查 /api/ps）
_LOCAL_GUARD_AHEAD = 120.0      # 秒：只剩这么点保活时间才续期（避免无谓打扰丢前缀缓存）
_LOCAL_GUARD_OFF = str(os.environ.get("PASM_LOCAL_KEEPALIVE", "1")).strip().lower() \
    in ("0", "false", "off", "no")
_local_guard = {"origin": "", "model": "", "thread": None, "lock": threading.Lock()}
LAST_COLD_LOAD = {"model": "", "secs": 0.0, "ts": 0.0}

# ================= v0.30.7「本机推理速度」实测注册表 =================
# 为什么非要有它（真机事故 2026-09-16，朋友机 Win / 无 GPU）：
#   用 Qwen3:0.6b 问一句「你好」，界面报"我这边出错了，没有成功回应你"。日志给全了答案：
#     `提示1730 tok | 首字110.24s | 总119.25s | 生成44 tok@4.9tok/s | 装载0.0s`
#   ① 提示词 1730 tok —— 其中约 1377 tok 是系统提示（单「规则0 能力清单」就 ~700 字）；
#   ② 这台机器**预填充只有 15.7 tok/s**（无 GPU）。本机带 RX580 实测 580 tok/s —— **差 37 倍**；
#      于是同一段提示：本机 3 秒读完，朋友机 110 秒；
#   ③ 换成 Qwen3:1.7b（预填充更慢）同样的 1730 tok 需要 >300s → 撞穿 chat 的 300s 预算
#      → 抛 APITimeoutError → 上层兜底文案"我这边出错了"。
# 结论：**提示词预算不能写死** —— 它是"按有 GPU 的机器"定的。必须按本机实测速度算。
# 这里就是那个实测值：每次原生调用完顺手记一笔，预算与超时都读它。
SPEED: Dict[str, dict] = {}
_SPEED_LOCK = threading.Lock()
# 未知速度时的**保守假设**：宁可先给个小提示词，测出来再放开。
_ASSUMED_PREFILL = 60.0     # tok/s；介于本机 580 与朋友机 15.7 之间，偏保守
_ASSUMED_GEN = 8.0          # tok/s
# 中文 ≈ 0.67 tok/字（仓库既有口径），用于「字符预算 <-> token 预算」换算
TOK_PER_CHAR = 0.67
# 慢机也要给足身份/安全类提示，不能再往下砍
_MIN_SYSTEM_CHARS = 1800
_MIN_HIST_CHARS = 300

# v0.30.13：预填充「疑似缓存命中」的识别阈值。
# Ollama 命中 KV 前缀缓存时 `prompt_eval_count` 仍报全量、`prompt_eval_duration`
# 只算新增部分 → 速度虚高。朋友机 2026-09-17 实测 3977.7 tok/s，而真实约 17.8
# （同一台机、同一模型，前后相差 220 倍）。这种样本不是"这台机器能跑多快"，
# 只是"这段话上次评估过"——采信它会让系统提示预算在 1000 / 9000 / 3047 字之间乱跳，
# 用户看到的现象就是"有时答得好、有时像失忆"。
_PREFILL_SUSPECT_TPS = 1500.0   # 超过它基本只可能是缓存命中或测量故障
_PREFILL_MIN_RATIO = 0.25       # 预填充至少要占「首字 − 装载」的 25% 才算真在算


def _speed_host(base_url: str) -> str:
    """速度按 (本机端点, 模型) 记账 —— 不同机器/不同端口的速度差别极大。"""
    try:
        return _ollama_origin(base_url) or (base_url or "")
    except Exception:
        return base_url or ""


def record_speed(model: str, host: str = "", *, prefill_tps: float = 0.0,
                 gen_tps: float = 0.0, prompt_tokens: int = 0) -> None:
    """记一次实测（EMA 平滑）。只收有效样本，别把噪声写进预算。"""
    if prefill_tps <= 0 and gen_tps <= 0:
        return
    k = "%s|%s" % ((host or "").strip().lower(), (model or "").strip().lower())
    with _SPEED_LOCK:
        d = SPEED.setdefault(k, {"prefill": 0.0, "gen": 0.0, "n": 0,
                                 "pt": 0, "ts": 0.0})
        first = d["n"] == 0
        if prefill_tps > 0:
            if first:
                d["prefill"] = prefill_tps
            else:
                # v0.30.13：**非对称 EMA** —— 向小值学得快、向大值保持原速。
                # 理由：预填充速度**高估**会让提示词给得过长 → 首字越来越慢；
                # 低估只是少给点上下文，不会更慢。所以向小值用 0.5 快速纠正；
                # 向大值仍用 0.3（与旧版一致，不让快机爬升变滞后）。
                # 虚高样本已由 `prefill_sample_ok` 挡在门外，向上不需要额外保守。
                _a = 0.5 if prefill_tps < d["prefill"] else 0.3
                d["prefill"] = (1 - _a) * d["prefill"] + _a * prefill_tps
        if gen_tps > 0:
            if first:
                d["gen"] = gen_tps
            else:
                # 生成速度同理：高估会让 max_tokens 给过大 → 慢机一轮跑几分钟。
                _ag = 0.5 if gen_tps < d["gen"] else 0.3
                d["gen"] = (1 - _ag) * d["gen"] + _ag * gen_tps
        d["n"] += 1
        d["pt"] = max(d["pt"], int(prompt_tokens or 0))
        d["ts"] = time.time()


def speed_of(model: str, host: str = "") -> dict:
    """该模型在本机的实测速度；**没测过就返回保守假设**并标 measured=False。"""
    with _SPEED_LOCK:
        d = dict(SPEED.get("%s|%s" % ((host or "").strip().lower(),
                                      (model or "").strip().lower())) or {})
    if not d or d.get("n", 0) <= 0:
        return {"prefill": _ASSUMED_PREFILL, "gen": _ASSUMED_GEN,
                "measured": False, "n": 0}
    return {"prefill": d.get("prefill") or _ASSUMED_PREFILL,
            "gen": d.get("gen") or _ASSUMED_GEN,
            "measured": True, "n": d.get("n") or 0}


def budget(model: str, host: str = "", *, want_first_secs: float = 10.0,
           what: str = "system") -> int:
    """按本机实测速度给出**字符预算**（system 提示 / 历史上下文）。

    口径（真机数据）：
      · 本机 RX580 580 tok/s → 10s 可读 5800 tok ≈ 8600 字 ≈ 旧上限 8000 字 → **行为不变**；
      · 朋友机 15.7 tok/s → 只够 234 tok → 落到下限 1400 字（≈940 tok ≈ 60s 首字）：
        仍然慢，但**能出结果**，而不是 110s 干等或干脆判失败。
    """
    sp = speed_of(model, host)
    want = max(1.0, float(sp["prefill"])) * float(want_first_secs)
    chars = int(want / TOK_PER_CHAR)
    floor = _MIN_SYSTEM_CHARS if what == "system" else _MIN_HIST_CHARS
    cap = 9000 if what == "system" else 8000
    return max(floor, min(cap, chars))


#: 各任务"用户愿意等多久才看到完整回答"的目标秒数（用于反推 max_tokens）。
_TASK_TARGET_SECS = {
    "chat": 45.0, "tool": 30.0, "brain": 60.0, "study": 30.0,
    "selftest": 30.0, "pet": 20.0, "skill": 120.0, "filegen": 90.0,
}


def clamp_max_tokens(model: str, host: str = "", want: int = 0,
                     target_secs: float = 45.0, floor: int = 240,
                     cap: int = 1600) -> int:
    """按**本机实测生成速度**把单轮生成长度收口（v0.30.13）。

    为什么必须做：朋友机（生成 6.8 tok/s）用默认 1600 → 单轮 235s；加上续写
    （`continue_cuts=2`）最坏要 12 分钟。用户看到的现象是"发一句，等 3~4 分钟
    才出字"，会直接以为卡死。日志证据（2026-09-17 朋友机）：
        总247.31s | 生成1626 tok@6.8tok/s
        总217.63s | 生成1260 tok@6.4tok/s

    口径：`目标秒数 × 实测生成速度 × 1.5`（那 1.5 是"宁可多等一点换完整回答"的宽限），
    再夹在 [floor, cap] 里。`floor` 保证答案不被截成半句；`cap` 用任务 profile
    自己的 max_tokens，所以**配置上限定多少就绝不超过多少**。
    """
    want = int(want or 0)
    sp = speed_of(model, host)
    gen = max(1.0, float(sp.get("gen") or 0.0))
    by_time = int(gen * max(5.0, float(target_secs)) * 1.5)
    upper = min(int(cap), by_time)
    return max(int(floor), min(want or int(cap), upper))


def prefill_sample_ok(pf: float, ped: float, span: float) -> bool:
    """这个预填充样本可信吗？（v0.30.13）

    Ollama 命中 KV 前缀缓存时 `prompt_eval_count` 仍报全量、`prompt_eval_duration`
    只算新增部分 → 算出速度虚高。朋友机 2026-09-17 实测同一模型：真实 17.8 tok/s，
    缓存命中时 3977.7 tok/s（**220 倍**）。这种样本不是"这台机器能跑多快"，
    只是"这段话上次评估过"。

    两条判据任一命中即判为不可信：
      ① 超过物理合理上限 `_PREFILL_SUSPECT_TPS`；
      ② 预填充只占「首字 − 装载」的极小部分（说明时间花在排队/缓存，不是在算）。
    """
    try:
        pf = float(pf or 0.0)
        ped = float(ped or 0.0)
        span = float(span or 0.0)
    except Exception:                       # noqa: BLE001
        return False
    if pf <= 0:
        return False
    if pf > _PREFILL_SUSPECT_TPS:
        return False
    if span > 0 and ped < _PREFILL_MIN_RATIO * span:
        return False
    return True


def think_budget_of(task: str, model: str, host: str = "") -> float:
    """思考阶段的秒级预算（v0.31.6：用户显式设置 > 强制开 > 按本机速度收口）。

    优先级（v0.31.6 新增前两条——真机实录「思考到一小段就断了」就是第三条 8s 太短）：
      1. 界面/命令显式设过预算 → **以用户为准**（想多久用户说了算）；
      2. 思考档为**强制开**（"on"）→ 用户明确要看深度思考，不再按慢机砍到 20s，
         给足重活预算（45s）；
      3. 否则按本机生成速度收口：慢机（<15 tok/s 或没实测过）取 min(车道预算, 20s)。

    背景（保留 v0.30.13 的取舍）：朋友机日志里思考开时首字稳定 43.6~45.5s、7 次全撞
    45s 预算；那台机器想满预算也没换来更好答案，只是让用户多等。所以**自动档**仍收口，
    但 8s 太短（小志机 9.7 tok/s 只想了 313 字就被掐），20s 是更合理的折中。
    """
    if _THINK_BUDGET_OVERRIDE > 0:
        return float(_THINK_BUDGET_OVERRIDE)
    base = (_THINK_BUDGET_SEC_HEAVY if task in _THINK_AUTO
            else _THINK_BUDGET_SEC)
    try:
        if str(get_local_think() or "").lower() == "on":
            return base                     # 用户强制开 → 别替用户省时间
    except Exception:                       # noqa: BLE001
        pass
    try:
        sp = speed_of(model, host)
        if (not sp.get("measured")) or float(sp.get("gen") or 0.0) < 15.0:
            return min(base, _THINK_BUDGET_SEC_SLOW)
    except Exception:                           # noqa: BLE001
        return base
    return base


def is_timeout(ex) -> bool:
    """这个异常是不是"超时"？（v0.30.7）

    ⚠️ 必须按**类型名**认，不能用 isinstance：本模块在 openai 不可用时会执行
    `APITimeoutError = Exception` 兜底，那样 isinstance(ex, APITimeoutError) 对
    **任何**异常都成立 —— 上层会把所有错误都当成"超时"，真实原因全被吞掉。
    """
    if type(ex).__name__ in ("APITimeoutError", "ReadTimeout", "ConnectTimeout",
                             "TimeoutError", "Timeout", "ReadTimeoutError"):
        return True
    return isinstance(ex, TimeoutError)


def predict_secs(messages, model: str, host: str = "", max_tokens: int = 800) -> dict:
    """预测本轮首字/总耗时（供 UI 提前如实告知，而不是让用户干等）。

    token 数按字符估（0.67 tok/字），**只用于告知与超时预算**，不参与任何正确性判定。
    """
    chars = 0
    for m in (messages or []):
        try:
            chars += len(str(m.get("content") or ""))
        except Exception:
            pass
    pt = int(chars * TOK_PER_CHAR)
    sp = speed_of(model, host)
    first = pt / max(1.0, sp["prefill"])
    gen = float(max_tokens or 0) / max(1.0, sp["gen"])
    return {"prompt_tokens": pt, "chars": chars,
            "prefill_tps": sp["prefill"], "gen_tps": sp["gen"],
            "measured": sp["measured"], "first": first, "total": first + gen}


def adapt_timeout(timeout: float, base_url: str, model: str, messages,
                  max_tokens: int, cap: float = 600.0) -> int:
    """慢机不该因为"提示词太长"被判失败：按实测速度把预算抬到够用（有硬上限）。

    ⚠️ 这只是**保险丝**，不是主修：主修是 `budget()` 把提示词本身缩小。
    若这台机器慢到连"最小提示词"都要 >cap 秒，那就该如实告诉用户（换小模型/用云端），
    而不是无限等下去。
    """
    try:
        p = predict_secs(messages, model, _speed_host(base_url), max_tokens)
        need = p["first"] * 1.8 + p["total"] * 0.5 + 30.0
        return int(min(max(float(timeout), need), max(float(timeout), float(cap))))
    except Exception:
        return int(timeout)


def _ollama_ps(origin: str, timeout: float = 2.0) -> list:
    """Ollama 当前**已驻留**（在显存/内存里）的模型列表。

    **探不到会抛异常** —— 由调用方区分"不知道"与"确定不在"。
    这个区分很重要：把"探不到"当成"不在"，会在 Ollama 只是短暂没应答时
    误触发一次装载，白白重置前缀缓存。
    """
    with urllib.request.urlopen(origin + "/api/ps", timeout=timeout) as r:
        d = json.loads((r.read() or b"{}").decode("utf-8", "ignore"))
    return list(d.get("models") or [])


def resident_info(base_url: str, model: str):
    """目标模型的驻留详情（含 expires_at）；不在驻留列表里返回 None。

    **探不到 Ollama 时返回 {}**（= "不知道"，与"确定不在"区分开）。
    """
    if not model:
        return {}
    origin = _ollama_origin(base_url)
    try:
        ms = _ollama_ps(origin)
    except Exception:
        return {}                      # 探不到 → "不知道"，绝不当成"不在"
    want = str(model)
    for m in ms:
        nm = str((m or {}).get("name") or "")
        if nm == want or (nm and nm.split(":")[0] == want.split(":")[0]):
            return dict(m or {})
    return None


def local_model_resident(base_url: str, model: str) -> bool:
    """本地模型此刻是否已驻留（False ⇒ 这一句要付冷加载代价，本机实测约 40s）。

    **探不到就返回 True** —— 宁可不打扰，也不要误报"要冷加载"。
    """
    if not model:
        return True
    return resident_info(base_url, model) is not None      # None=确定不在；{}=探不到→当好态


def prewarm_local(base_url: str, model: str, timeout: float = 150.0) -> bool:
    """把本地模型拉进显存并续上 keep_alive（原生 /api/chat，返回是否成功）。

    与聊天走**同一条通路、同一个 num_ctx** —— 避免"预热按 A 配置装载、聊天按 B 配置请求
    → Ollama 又得换载"的旧坑：v0.27.9 只锁了模型名，这里把 ctx 也一起锁死。
    """
    if not model:
        return False
    origin = _ollama_origin(base_url)
    # 硬化：只预热**本机已装**的模型，避免任何情况下让 Ollama 去联网拉模型
    try:
        with urllib.request.urlopen(origin + "/api/tags", timeout=3) as r:
            tags = json.loads((r.read() or b"{}").decode("utf-8", "ignore"))
        have = [str((m or {}).get("name") or "") for m in (tags.get("models") or [])]
        want = str(model)
        if have and not any(h == want or h.split(":")[0] == want.split(":")[0]
                            for h in have):
            log.debug("prewarm_local 跳过：%s 不在已装模型里", model)
            return False
    except Exception:
        pass
    opts = {"num_ctx": _LOCAL_NUM_CTX}
    # ① 首选「只装载、不推理」：POST /api/generate 不带 prompt —— Ollama 实测返回
    #    {"response":"","done_reason":"load"}，既把模型拉进显存、又把 keep_alive 续上，
    #    却**不跑任何生成**。预热的语义本来就只是"装载"，用这个最干净也最快。
    for path, body in (
        ("/api/generate", {"model": model, "keep_alive": _LOCAL_KEEP_ALIVE,
                           "options": opts}),
        # ② 兜底：老版本若不认"空 prompt 装载"，退回发 1 个 token 的对话请求
        ("/api/chat", {"model": model, "keep_alive": _LOCAL_KEEP_ALIVE,
                       "messages": [{"role": "user", "content": "hi"}],
                       "stream": False,
                       "options": dict(opts, num_predict=1)}),
    ):
        try:
            raw = json.dumps(body).encode("utf-8")
            req = urllib.request.Request(origin + path, data=raw,
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                r.read()
            return True
        except Exception as ex:
            log.debug("prewarm_local(%s) 经 %s 失败：%s", model, path, str(ex)[:120])
    return False


def _local_guard_tick():
    """守卫循环：**只在模型真的不在（或即将过期）时才动手**。

    【为什么不周期性续期】实测（2026-09-14）：Ollama 的前缀 KV 缓存能省掉整整一次
    prompt 评估 —— 同一段 616 token 的提示，热前缀 prompt_eval 0.30s，冷前缀 4.23s。
    而**任何**触碰该模型的请求（哪怕只是"hi"或纯装载）都会重置这个缓存。
    所以守卫必须是"按需"的：模型好好驻留着就绝不打扰，否则等于每 4 分钟白扣一句几秒。
    """
    while True:
        time.sleep(_LOCAL_GUARD_GAP)
        if _LOCAL_GUARD_OFF:
            return
        with _local_guard["lock"]:
            origin, model = _local_guard["origin"], _local_guard["model"]
        if not origin or not model:
            continue
        try:
            info = resident_info(origin, model)
            if info is None:
                # 真不在了（被换出 / Ollama 重启 / 别的模型把它顶了）→ 立刻补回来
                log.info("本地模型 %s 已不在显存（被换出或 Ollama 重启）→ 后台重新装载",
                         model)
                _t = time.time()
                if prewarm_local(origin, model):
                    log.info("守卫重新装载完成 %.1fs，模型已驻留", time.time() - _t)
                continue
            if not info:
                continue                      # 探不到 Ollama：不动手，避免误判
            exp = str(info.get("expires_at") or "")
            if exp:
                try:
                    import datetime as _dt
                    e = _dt.datetime.fromisoformat(exp.replace("Z", "+00:00"))
                    now = _dt.datetime.now(e.tzinfo) if e.tzinfo else _dt.datetime.now()
                    if (e - now).total_seconds() < _LOCAL_GUARD_AHEAD:
                        # 驻留着但快过期（说明某条路径用了短 keep_alive）→ 续一次
                        prewarm_local(origin, model)
                except Exception:
                    pass
        except Exception:
            log.debug("驻留守卫检查失败", exc_info=True)


def _local_guard_note(origin: str, model: str) -> None:
    """记下"当前在用的本地模型"，首次调用时启动驻留守卫线程（幂等）。"""
    if _LOCAL_GUARD_OFF or not origin or not model:
        return
    try:
        with _local_guard["lock"]:
            _local_guard["origin"], _local_guard["model"] = origin, model
            if _local_guard["thread"] is None:
                t = threading.Thread(target=_local_guard_tick,
                                     name="pasm-local-guard", daemon=True)
                _local_guard["thread"] = t
                t.start()
    except Exception:
        log.debug("驻留守卫启动失败", exc_info=True)


# ================= v0.27.13「按价值分配思考链」=================
# 问题：思考链要不要开？—— 真机实测（RX580 4G + Ryzen 2600X，Qwen3.5-4B，ctx=16384）
#   任务        关思考                     开思考                    思考链买到什么
#   闲聊        首字 1.25s / 总 4.8s       首字 133.4s / 总 136.8s   无（答案几乎一样）
#   算术推理    首字 1.40s / 总 6.3s 对    总 165.1s / 正文 0 字      无（还更糟：思考吃光配额）
#   代码排错    12.9s 对                   166.9s 对                 无
#   常识陷阱    1.7s「5 元」错             91.4s「0.5 元」对          ✅ 抗直觉陷阱、自我纠错
#   另外：think 的 low/medium/high 三档思考量几乎一样（1157 / 1222 / 1254 字）→
#        这个模型**没有可用的"分级思考"**，只能做二元开关。
# 结论：思考链的价值 = 自我纠错，代价 = 30~140 倍延迟 + 挤占输出配额。
#   → 不给聊天开（延迟就是体验）；给"后台车道"和"值得思考的问题"开；
#     开的时候给足配额，并设时间预算，超预算就"带着已想出的思路"强制收敛出答案。
# study 车道实测被推翻过：它主要是"资料提炼/任务拆分/成品评审"这类活，开思考 44.2s、
# 关思考 11.8s，**提炼质量没有差别** → 直接归入"不思考"。
# （若给它 auto，长资料里出现"分析/为什么"等词会误触发——实测确认过。）
# v0.31.3：聊天栏「深度思考」默认开启（用户明确要求能看见思考过程）。
#   把 chat 从 _THINK_OFF 移到 _THINK_AUTO：只在"值得推理"的问题上才深思
#   （数学/排错/因果/方案…），闲聊不思考，避免 30~140 倍延迟。
#   想要"每句话都深思"可在设置「思考链」里选「始终开启」。
_THINK_OFF = ("tool", "warm", "skill", "study")   # 机械提炼/技能/暖场 → 不思考
_THINK_ON = ("selftest",)                        # 学后自测是"诚实自检" → 必须想（后台，延迟不可见）
_THINK_AUTO = ("brain", "filegen", "chat")        # 按问题是否"值得想"决定（chat 默认可见深思）
# 开思考时额外预留的输出配额。实测思考正文 1200~4000 字 ≈ 1000~3000 tok，
# 与正文共用 num_predict —— 不预留就是"想完了没额度说话"（正文 0 字）。
_THINK_RESERVE = 1600
# 单次思考的时间预算（秒）。超预算就中止，把"已想出的思路"当上下文交回去，
# 关思考直接收敛出答案（reasoning budget forcing）。
# 实测（同一道陷阱题）：完整思考 137.7s / 2832 字；25s 掐断+收敛 37.1s 也能答对。
# 预算按车道给：后台提炼/自测被打断还能重来，重活（brain/filegen）多给一点更稳。
_THINK_BUDGET_SEC = 30.0        # study / selftest（后台，可重来）
_THINK_BUDGET_SEC_HEAVY = 45.0  # brain / filegen（重活，用户已在等）
# v0.31.6：慢机（生成 <15 tok/s）的思考上限。8s → 20s ——
#   真机实录（小志 2026-09-24 14:52，本机 9.7 tok/s）：思考刚想 8 秒 / 313 字就被
#   "超预算"掐断，用户观感是"深度思考到一小段就断了"。8s 在慢机上确实太短：
#   想不完的照样要收敛（多等 12s 换来更完整的思路，比反复收敛更值）。
_THINK_BUDGET_SEC_SLOW = 20.0
# v0.31.6：预算覆盖（0 或非正数 = 用自动策略）。设置里的"思考预算(秒)"与
#   chat 命令都写这里；用户在界面上明确设过就**以用户为准**。
_THINK_BUDGET_OVERRIDE = 0.0
#: 上一次调用里"思考被掐断收敛"的事实（供界面上屏，别让用户觉得"莫名断了"）。
#: {"at": 时间戳, "cut": bool, "chars": 思考字数, "budget": 预算秒, "task": 车道}
LAST_THINK_CUT: Dict[str, Any] = {}


def set_think_budget(sec) -> float:
    """设思考预算秒数（0/None/负数 = 恢复自动）。返回生效值（0 表示自动）。"""
    global _THINK_BUDGET_OVERRIDE
    try:
        v = float(sec or 0)
    except Exception:                                        # noqa: BLE001
        v = 0.0
    _THINK_BUDGET_OVERRIDE = v if v > 0 else 0.0
    return _THINK_BUDGET_OVERRIDE


def get_think_budget() -> float:
    """当前显式预算（0 = 自动）。"""
    return _THINK_BUDGET_OVERRIDE


def last_think_cut() -> Dict[str, Any]:
    """上一次调用里思考是否被掐断（含字数/预算），供界面如实播报。"""
    return dict(LAST_THINK_CUT or {})
# 超预算后的"强制收敛"提示。实测关键：要求它**列等式并代入原题检验**，
# 能补回被截断的那部分思考（同一题 25s 预算下，普通提示给出过错误答案「3 元」）。
_CONVERGE_PROMPT = ("（以上是你的分析过程）请结束分析、直接给出最终答案。"
                    "注意：这类问题最容易掉进直觉陷阱，请先列出关键等式并**代入原题复核**，"
                    "确认前后不矛盾后再下结论；只输出结论，不要再展开分析。")
# 判断"这个问题值不值得想"的信号。只在 _THINK_AUTO 车道生效。
# 取舍依据：误开一次思考的代价是 25s + 一次收敛（约 30s），误关一次的代价通常只是
# 答得朴素一点 —— 所以信号要**宁缺毋滥**：只留"确实需要严谨推理"的词，
# 把「方案/设计/计划/步骤/流程/优化」这类日常高频词全部剔除（实测误报）。
_THINK_SIGNALS = re.compile(
    r"为什么|为何|怎么会|为什么不行|分析|推理|推导|证明|论证|计算|算一下|求解|估算"
    r"|比较|对比|权衡|取舍|利弊|优劣|哪个更|根因|排查|调试|报错|bug|错误|不对|冲突"
    r"|隐患|风险|反例|悖论|陷阱|逻辑|因果|前提|假设|验证|复核|自检|评估|判断"
    r"|算法|复杂度|可靠性|正确性|边界|极限|数学|方程|函数|概率|统计|矩阵|积分|导数"
    r"|排列|组合|几何|仔细想|认真想|好好想|想清楚|一步步|分步|推演|double.?check",
    re.I)
# 算术应用题的"陷阱特征"：必须**同时**出现数字才认（"笔多少钱" vs "这个多少钱"）。
_THINK_MATH = re.compile(r"多少|几小时|几天|几倍|几个|几遍")
_THINK_DIGIT = re.compile(r"\d")


# ---------------------------------------------------------------- v0.28.3 流式思考拆分
# 【为什么需要它】DeepSeek 等云端模型有时把思考以内嵌 <think>…</think> 混进 content
# （而不是独立 reasoning_content 字段）。旧链路直接把 content 丢给 UI 的 insertHtml，
# QTextDocument 会把 <think> 当**未知 HTML 标签连同内容一起吞掉**——这就是真机上
# "回复文字部分丢失"的根因；同时独立 reasoning_content 字段的思考一直被静默丢弃。
_THINK_OPEN, _THINK_CLOSE = "<think>", "</think>"


class _ThinkSplitter:
    """流式把 content 中内嵌的 <think>…</think> 剥离到思考通道（跨 chunk 安全）。

    feed(chunk) -> (body_inc, think_inc)：正文增量 / 思考增量。
    标签可能被切碎在两个 chunk 里（如 "<thi" + "nk>"），所以始终扣留
    尾部最多 len(标签)-1 个字符不外发，等下一个 chunk 拼上再判定。
    finish() 收尾：未闭合的 <think> 视为思考到结尾（模型没写 </think> 就断流）。
    """

    def __init__(self):
        self._buf = ""
        self._in = False

    @staticmethod
    def _emit(buf, tag):
        """把 buf 中除"可能是被截断的 tag 前缀"外的部分吐出，返回 (out, 扣留余量)。"""
        keep = 0
        for k in range(min(len(tag) - 1, len(buf)), 0, -1):
            if tag.startswith(buf[-k:]):
                keep = k
                break
        if keep:
            return buf[:-keep], buf[-keep:]
        return buf, ""

    def feed(self, chunk: str):
        self._buf += chunk
        body = think = ""
        while True:
            if not self._in:
                i = self._buf.find(_THINK_OPEN)
                if i >= 0:
                    body += self._buf[:i]
                    self._buf = self._buf[i + len(_THINK_OPEN):]
                    self._in = True
                    continue
                out, self._buf = self._emit(self._buf, _THINK_OPEN)
                return body + out, think
            j = self._buf.find(_THINK_CLOSE)
            if j >= 0:
                think += self._buf[:j]
                self._buf = self._buf[j + len(_THINK_CLOSE):]
                self._in = False
                continue
            out, self._buf = self._emit(self._buf, _THINK_CLOSE)
            return body, think + out

    def finish(self):
        out, self._buf = self._buf, ""
        if self._in:
            self._in = False
            return "", out
        return out, ""


def _strip_think(text: str) -> str:
    """非流式兜底：剥掉 content 里内嵌的思考段（闭合与未闭合都处理）。"""
    t = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S)
    i = t.find(_THINK_OPEN)
    if i >= 0:                       # 未闭合：<think> 之后全是思考
        t = t[:i]
    return t


def _worth_thinking(text: str) -> bool:
    """这段话值不值得开思考链。

    只看**意图窗口**：开头 200 字（指令/问题）+ 结尾 120 字（真正的提问）。
    实测教训：整段扫会误触发——贴一段含"分析"的长资料或长草稿，
    会把一次单纯的"提炼要点"变成 44s 的思考（而质量没有任何提升）。
    """
    t = (text or "").strip()
    if not t:
        return False
    win = t[:200] + (" " + t[-120:] if len(t) > 320 else t[200:])
    if _THINK_SIGNALS.search(win):
        return True
    return bool(_THINK_DIGIT.search(win) and _THINK_MATH.search(win))
# 全局模式："auto"（默认，按上面策略表）/"on"（强制全开）/"off"（强制全关）
_THINK_MODE = "auto"


def _last_user_text(messages) -> str:
    for m in reversed(list(messages or [])):
        role = (m or {}).get("role") if isinstance(m, dict) else getattr(m, "role", None)
        if role == "user":
            c = (m or {}).get("content") if isinstance(m, dict) else getattr(m, "content", "")
            if isinstance(c, str):
                return c
    return ""


def _want_think(task: str, messages) -> bool:
    """这次请求值不值得开思考链（真机数据见上方注释）。"""
    if _THINK_MODE == "off":
        return False
    if _THINK_MODE == "on":
        return True
    if task in _THINK_OFF:
        return False
    if task in _THINK_ON:
        return True
    if task not in _THINK_AUTO:
        return False
    text = _last_user_text(messages)[:600]
    # v0.28.0：能被「符号推理层」确定性求解的问题**一律不思考**。
    # 理由（真机实测）：这类题开思考 33.8s/题且正确率不稳（同一题两次得 3 元/5 元都不对），
    # 而符号求解器 0.1ms、8/8 正确。让 17 tok/s 的模型"硬想"是纯粹的算力浪费。
    try:
        import symbolic as _SY
        if _SY.solve(text) is not None:
            return False
    except Exception:
        pass
    return _worth_thinking(text)
_OLLAMA_PROBE: Dict[str, float] = {}     # origin -> 下次可再探测的时间戳
_OLLAMA_OK: Dict[str, bool] = {}
# v0.27.11：后台请求的读超时片。流式每收一个 chunk 都会重置计时，所以短片只在
# 「卡住」时生效 —— 把后台被前台抢占的最坏延迟从 120s 收到 20s，不影响正常生成。
_HB_SEG_LOCAL_BG = 20.0


def _is_local(base_url: str) -> bool:
    return bool(is_local_url(base_url) or "11434" in (base_url or "")
                or "8080" in (base_url or ""))


def set_local_think(mode):
    """设置本地模型的思考链模式。

    "auto"（默认，推荐）：聊天/工具/技能一律不思考；后台提炼与自学自测开启；
                          brain/filegen 只在问题"值得想"（数学、推理、排错、方案…）时才想。
    "on"  ：强制全开（用户显式要求"更严谨"时用；感知延迟可能 30 倍以上）。
    "off" ：强制全关（最省时间）。
    True/False 同 "on"/"off"（兼容 v0.27.12 的布尔调用）。
    """
    global _THINK_MODE
    if mode is True:
        _THINK_MODE = "on"
    elif mode is False:
        _THINK_MODE = "off"
    else:
        m = str(mode or "auto").strip().lower()
        _THINK_MODE = m if m in ("auto", "on", "off") else "auto"


def get_local_think() -> str:
    return _THINK_MODE


def _think_enabled(base_url: str, task: str = "chat", messages=None) -> bool:
    """本地端点这次是否真的走思考链（非本地端点恒 False——云端模型自带策略）。"""
    if not _is_local(base_url):
        return False
    return _want_think(task, messages)


def _ollama_origin(base_url: str) -> str:
    """把 base_url 收敛成 Ollama 服务根：http://127.0.0.1:11434/v1 → http://127.0.0.1:11434"""
    b = (base_url or "").rstrip("/")
    for suf in ("/v1", "/api"):
        if b.endswith(suf):
            b = b[: -len(suf)]
    return b.rstrip("/")


def _is_ollama(base_url: str, ttl: float = 30.0) -> bool:
    """这个本地端点是不是 Ollama（决定能否走原生 /api/chat）。

    首次探测 GET {origin}/api/tags，结果按 TTL 缓存——失败也缓存（避免每次请求都白探）。
    """
    if not _is_local(base_url):
        return False
    origin = _ollama_origin(base_url)
    now = time.time()
    if origin in _OLLAMA_OK and now < _OLLAMA_PROBE.get(origin, 0.0):
        return _OLLAMA_OK[origin]
    ok = False
    try:
        with urllib.request.urlopen(origin + "/api/tags", timeout=3) as r:
            ok = r.status == 200 and b"models" in r.read(4096)
    except Exception:
        ok = False
    _OLLAMA_OK[origin] = ok
    _OLLAMA_PROBE[origin] = now + (ttl if ok else 10.0)
    return ok


class _ErrResp:
    """给 SDK 错误类用的最小响应替身（它只读 status_code / request / headers）。"""
    __slots__ = ("status_code", "request", "headers")

    def __init__(self, status_code: int):
        self.status_code = status_code
        self.request = None
        self.headers: Dict[str, str] = {}


def _jsonable(obj):
    """把 SimpleNamespace / 任意对象递归转成可 json 序列化的结构（原生端点自组请求体用）。"""
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    if hasattr(obj, "__dict__"):
        return {k: _jsonable(v) for k, v in vars(obj).items() if not k.startswith("_")}
    return str(obj)


def _native_msgs(messages: List[dict]) -> List[dict]:
    """把消息规整成 Ollama 原生格式。

    两处必要转换：
      · assistant 里的 tool_calls 可能是 SimpleNamespace（网关自己造的对象）→ 转 dict；
      · role=tool 的消息，原生端点认 tool_name（不认 tool_call_id）→ 从前一条
        assistant 的 tool_calls 里按 id 反查名字补上。
    """
    out: List[dict] = []
    id2name: Dict[str, str] = {}
    for m in messages or []:
        if not isinstance(m, dict):
            m = _jsonable(m)
        role = m.get("role")
        if role == "assistant":
            tcs = m.get("tool_calls")
            if tcs:
                tcs = _jsonable(tcs)
                for c in tcs:
                    fn = (c or {}).get("function") or {}
                    if c.get("id") and fn.get("name"):
                        id2name[str(c["id"])] = str(fn["name"])
                    # 原生端点要求 arguments 是**对象**；而 OpenAI 风格是 JSON 字符串
                    # （网关自己也是按 OpenAI 风格造的）→ 字符串的先解成 dict，否则 400。
                    args = fn.get("arguments")
                    if isinstance(args, str):
                        try:
                            fn["arguments"] = json.loads(args) if args.strip() else {}
                        except Exception:
                            fn["arguments"] = {}
                m = dict(m, tool_calls=tcs)
        elif role == "tool":
            name = m.get("tool_name")
            if not name and m.get("tool_call_id"):
                name = id2name.get(str(m["tool_call_id"]))
            m = {"role": "tool", "content": m.get("content", "")}
            if name:
                m["tool_name"] = name
        out.append(m)
    return out

# 并发上限（host → 允许同时请求数）
_LOCAL_HOSTS = ("127.0.0.1", "localhost", "0.0.0.0", "[::1]", "::1")
_LOCAL_CONCURRENCY = 2                  # 本地 Ollama：允许 2 路并发（Ollama 内部排队，模型只载一份）
_CLOUD_CONCURRENCY = 4                  # 云端 OpenAI 兼容：宽松并发
# v0.27.9：交互任务（用户直聊）独占车道，后台自学/工具不得抢占。
# 否则后台读书/精炼与聊天共用同一条 host 车道 → 聊天静默排队数百秒（真机 400s 无响应根因之一）。
# v0.27.10：把「用户在等的活」全部划入前台——聊天/工具/技能/出文件/双脑都算，
# 后台学习（study/pet/selftest）必须给它们让路。
_INTERACTIVE_TASKS = ("chat", "tool", "skill", "filegen", "brain", "warm")

# v0.27.10 前台优先调度（本地单模型端点尤其重要：Ollama 内部串行，后台不腾手聊天就得干等）
_FG_QUIET_GAP = 6.0      # 前台空闲满这么多秒，后台才允许占用模型（避免刚答完又被学习插队）
_BG_BOOT_WAIT = 20.0     # 后台为前台让路的最长等待；等不到就放弃本轮（稍后自然重试，不堆积）
# 前台抢占时，先等后台"真的收手"再发请求：真机实测后台生成中插入前台，
# 若立刻发请求，Ollama 侧仍在跑旧生成 → 前台多等 ~3.4s；等旧连接断开后可降到 ~2s。
# 等到就放行（一般几十~几百毫秒），等不到最多 _FG_GRACE_MAX 秒也不拖住用户。
_FG_GRACE_MAX = 1.2


class LLMBusy(RuntimeError):
    """端点排队超时（并发闸满且超过等待预算）。"""


class ModelUnavailable(RuntimeError):
    """模型不存在 / 端点不支持（如 Ollama 里没装这个模型）。"""


class Cancelled(RuntimeError):
    """用户点了「⏹ 停止」：本次等待被主动取消（本地 Ollama 会随连接断开真中断生成）。"""


class _AnyStop:
    """把「用户点停止」与「前台抢占（后台让路）」两个事件合成一个 is_set() 接口。

    下层只看 stop.is_set()，用这个适配器就能让同一套取消通路同时服务两种语义。
    """
    __slots__ = ("_evs",)

    def __init__(self, *evs):
        self._evs = [e for e in evs if e is not None]

    def is_set(self) -> bool:
        return any(e.is_set() for e in self._evs)


def is_local_url(base_url: str) -> bool:
    base = (base_url or "").lower().split("//")[-1]
    return any(base.startswith(h) for h in _LOCAL_HOSTS)


def describe_error(ex: Exception) -> str:
    """把异常压缩成一行可读中文说明（不进堆栈刷屏）。"""
    msg = (getattr(ex, "message", None) or str(ex) or ex.__class__.__name__)
    msg = " ".join(str(msg).split())[:160]
    if isinstance(ex, AuthenticationError):
        return "API Key 无效或已失效（401）"
    if isinstance(ex, PermissionDeniedError):
        return "没有权限访问该模型（403），请检查 Key 的模型权限"
    if isinstance(ex, NotFoundError):
        return f"模型或接口不存在（404）：{msg}"
    if isinstance(ex, BadRequestError):
        return f"请求参数被拒绝（400）：{msg}"
    if isinstance(ex, RateLimitError):
        return "触发限流（429），已自动退避重试"
    if isinstance(ex, APITimeoutError):
        return "模型响应超时"
    if isinstance(ex, APIConnectionError):
        return f"连不上模型服务：{msg}"
    return msg


class Gateway:
    def __init__(self):
        self._lock = threading.Lock()
        self._clients: Dict[tuple, Any] = {}
        self._sems: Dict[str, threading.Semaphore] = {}
        self.stats: Dict[str, Dict[str, int]] = {}   # host -> {ok, fail, busy}
        # v0.27.10 前台优先调度：前台在跑 / 刚跑完的静默期内，后台一律不得占用模型
        self._cv = threading.Condition(self._lock)
        self._fg_active = 0                          # 正在跑的前台（用户在等）请求数
        self._fg_last = 0.0                          # 最近一次前台活动结束时间
        self._bg_stops: set = set()                  # 在飞后台请求的「让路」事件

    # ---------- 客户端复用 ----------
    def client_for(self, base_url: str, api_key: str):
        if OpenAI is None:
            raise RuntimeError("openai 库未安装")
        key = (base_url or "", api_key or "local")
        with self._lock:
            c = self._clients.get(key)
            if c is None:
                # max_retries=0：SDK 层的静默重试会整单重发、把等待时间翻倍还刷屏日志
                # （真机日志"Retrying request …"每 25s 一条即此）；重试统一由网关自己管
                c = OpenAI(api_key=api_key or "local", base_url=base_url,
                           max_retries=0)
                self._clients[key] = c
            return c

    # ---------- 本地模型常驻 + 思考策略（v0.27.11 / v0.27.12 / v0.27.13） ----------
    @staticmethod
    def _with_keepalive(base_url: str, kw: dict, think: bool = False) -> dict:
        """本地 Ollama 的兼容端点兜底参数。

        注意（v0.27.12 实测更正）：Ollama 的 /v1/chat/completions 会**丢弃** keep_alive
        与 num_ctx，所以这两个参数在这里只对老版本/别的实现有意义，留着无害；
        真正让模型常驻与压上下文靠的是原生 /api/chat 通路（见 _create_hb）。
        reasoning_effort="none" 是**唯一**在兼容端点上能关掉思考链的写法（实测有效），
        所以兜底路径必须带上它，否则又会掉回"思考 40 秒不吐字"。
        v0.27.13：改为**跟随思考策略** —— 这次该想就不补 none，不该想才补。
        """
        if not _is_ollama(base_url):
            return kw
        eb = dict(kw.get("extra_body") or {})
        eb.setdefault("keep_alive", _LOCAL_KEEP_ALIVE)
        if not think:
            eb.setdefault("reasoning_effort", "none")
        eb.setdefault("num_ctx", _LOCAL_NUM_CTX)
        kw["extra_body"] = eb
        return kw

    # ---------- 并发闸 ----------
    def _semaphore(self, base_url: str, task: str = "chat") -> threading.Semaphore:
        """并发闸：按 **(host, 车道)** 分池——交互与后台各自独立，互不抢占。

        v0.27.9 修复：此前按 host 单池、容量又被 concurrency=1 压成 1 条车道，
        于是后台自学（读书/精炼/上网）与本机聊天抢同一条道，聊天只能静默排队。
        现在 chat 独占 fg 车道；study/brain/pet/skill/... 共用 bg 车道。
        """
        host = ((base_url or "").split("//")[-1].split("/")[0] or "local").lower()
        local = (is_local_url(base_url) or "11434" in (base_url or "")
                 or "8080" in (base_url or ""))
        lane = "fg" if task in _INTERACTIVE_TASKS else "bg"
        key = host + "|" + lane
        cap = _LOCAL_CONCURRENCY if local else _CLOUD_CONCURRENCY
        prof = TASK_PROFILES.get(task, TASK_PROFILES["chat"])
        cap = min(cap, int(prof.get("concurrency", cap) or cap)) or 1
        with self._lock:
            sem = self._sems.get(key)
            if sem is None:
                sem = threading.Semaphore(cap)
                self._sems[key] = sem
            return sem

    # ---------- v0.27.10 前台优先调度 ----------
    @staticmethod
    def _is_fg(task: str) -> bool:
        """是不是「用户正在等结果」的前台任务。"""
        return task in _INTERACTIVE_TASKS

    def _fg_enter(self):
        """前台请求开跑：抢占在飞的后台请求，把模型立刻腾给用户。

        ——这是「保留自动学习、又不拖慢聊天」的关键：后台学习不必关，
        用户在等时它自己让开，用户空闲后它继续。
        """
        with self._cv:
            self._fg_active += 1
            had_bg = bool(self._bg_stops)
            for ev in list(self._bg_stops):
                try:
                    ev.set()
                except Exception:
                    pass
            self._cv.notify_all()
        if had_bg:
            # 只有"真有后台在飞"时才付这点小代价：等它把连接断开（通常很快），
            # 避免 Ollama 同时伺候两条请求而让用户白等。
            end = time.time() + _FG_GRACE_MAX
            with self._cv:
                while self._bg_stops and time.time() < end:
                    self._cv.wait(0.05)

    def _fg_leave(self):
        with self._cv:
            self._fg_active = max(0, self._fg_active - 1)
            self._fg_last = time.time()
            self._cv.notify_all()

    def _bg_enter(self, timeout: float):
        """后台请求入口：前台在跑或刚跑完的静默期内 → 等；等不到就放弃本轮。

        返回本请求的「让路」事件（供 _AnyStop 合并）；放弃时抛 LLMBusy，
        上层（自学/自测）按失败处理即可——下一轮自然会再试，不会堆积。
        """
        limit = time.time() + min(max(1.0, float(timeout or 0)), _BG_BOOT_WAIT)
        with self._cv:
            while True:
                if self._fg_active == 0 and \
                        (time.time() - self._fg_last) >= _FG_QUIET_GAP:
                    ev = threading.Event()
                    self._bg_stops.add(ev)
                    return ev
                remain = limit - time.time()
                if remain <= 0:
                    raise LLMBusy("前台正在用模型，后台学习已让路（稍后自动继续）")
                self._cv.wait(min(remain, 0.5))

    def _bg_leave(self, ev):
        if ev is None:
            return
        with self._cv:
            self._bg_stops.discard(ev)
            self._cv.notify_all()          # 立刻唤醒正在等"后台收手"的前台请求

    @staticmethod
    def _preempted(bg_ev, stop) -> bool:
        """本次取消是不是「前台抢占」造成的（而不是用户点停止）。"""
        return (bg_ev is not None and bg_ev.is_set()
                and (stop is None or not stop.is_set()))

    def _stat(self, base_url: str, kind: str):
        host = (base_url or "").split("//")[-1].split("/")[0] or "?"
        with self._lock:
            d = self.stats.setdefault(host, {"ok": 0, "fail": 0, "busy": 0})
            d[kind] = d.get(kind, 0) + 1

    # ---------- 心跳式可取消调用（v0.17.5） ----------
    @staticmethod
    def _heartbeat_timeout(stop, deadline: float, seg: float = 25.0) -> float:
        """计算本次 create 的 socket 超时：不超过总预算、可被 stop 打断的分片。"""
        if stop is not None and stop.is_set():
            raise Cancelled("已停止等待这条回复")
        remain = deadline - time.time()
        if remain <= 0:
            raise APITimeoutError("等待模型响应超过预算（可点停止或换更小的模型）")
        return min(remain, seg)

    def _create_hb(self, client, model, messages, temperature, max_tokens, kw,
                   deadline, stop, on_delta=None, bg: bool = False,
                   task: str = "chat", on_think=None):
        """单次补全调用，带心跳分片：连接层超时→若用户已点停止则抛 Cancelled
        （底层连接随超时断开，Ollama 会真中断生成），否则继续等至总预算。

        v0.18.1：本地端点改走流式接收——生成期间 token 持续到达即天然心跳，
        读超时只需罩住「首字节前」的冷加载/prompt 评估；云端维持原非流式心跳。
        v0.22.0：调用方传 on_delta 时本地与云端都走流式，token 实时回调 UI，
        「感知响应速度 = 首 token 时间」而不是全文完成时间。
        """
        local = _is_local(str(client.base_url or ""))
        # v0.27.12：本地 Ollama 改走原生 /api/chat —— 只有它能带 keep_alive（模型不被卸载）、
        # num_ctx（小上下文＝更多层上 GPU＝更快）和 think=false（关掉隐藏思考链）。
        # 兼容端点会静默丢弃这三个参数（实测），所以这里不是"优化"，是"唯一可行通路"。
        if local and "response_format" not in kw and _is_ollama(str(client.base_url or "")):
            try:
                return self._native_ollama_hb(
                    str(client.base_url or ""), model, messages, temperature,
                    max_tokens, kw, deadline, stop, on_delta=on_delta, bg=bg,
                    task=task, on_think=on_think)
            except (ModelUnavailable, BadRequestError, Cancelled, APITimeoutError):
                raise
            except Exception as ex:
                log.info("原生 /api/chat 不可用，回退兼容端点：%s", str(ex)[:120])
        want_stream = on_delta is not None or (
            local and "tools" not in kw and "response_format" not in kw)
        # v0.27.11：后台请求用更短的读超时片 → 被前台抢占时最多 20s 就收手（前台 120s）
        _seg = ((_HB_SEG_LOCAL_BG if bg else _HB_SEG_LOCAL) if local
                else _HB_SEG_CLOUD)
        while True:
            seg = self._heartbeat_timeout(stop, deadline, seg=_seg)
            try:
                if want_stream:
                    return self._create_stream_agg(
                        client, model, messages, temperature, max_tokens, kw,
                        seg, stop, on_delta=on_delta, on_think=on_think)
                resp = client.chat.completions.create(
                    model=model, messages=messages,
                    temperature=temperature, max_tokens=max_tokens,
                    timeout=seg, **kw)
                # v0.27.10：非流式调用期间若被「停止/前台抢占」，结果直接作废
                # （否则要等下一段读超时才收手，后台会白占模型更久）
                if stop is not None and stop.is_set():
                    raise Cancelled("已停止等待这条回复")
                return resp
            except (APITimeoutError, TimeoutError) as ex:
                if stop is not None and stop.is_set():
                    raise Cancelled("已停止等待这条回复") from ex
                if time.time() >= deadline:
                    raise APITimeoutError(
                        "模型响应等待超时（可点「⏹ 停止」或换个更小的模型）") from ex
                time.sleep(0.25)              # 心跳间隙，极短不占用总预算

    # ---------- v0.27.12 本地 Ollama 原生通路 ----------
    def _native_ollama_hb(self, base_url, model, messages, temperature,
                          max_tokens, kw, deadline, stop, on_delta=None,
                          bg: bool = False, task: str = "chat", on_think=None):
        """原生 /api/chat 的心跳包装：分片读超时 + 可被 stop/前台抢占打断。"""
        origin = _ollama_origin(base_url)
        _local_guard_note(origin, model)          # v0.29.1 驻留守卫
        _seg = (_HB_SEG_LOCAL_BG if bg else _HB_SEG_LOCAL)
        while True:
            seg = self._heartbeat_timeout(stop, deadline, seg=_seg)
            try:
                return self._native_ollama_once(
                    origin, model, messages, temperature, max_tokens, kw, seg,
                    stop, on_delta=on_delta, task=task, on_think=on_think)
            except (Cancelled, ModelUnavailable, BadRequestError, APITimeoutError):
                raise
            except urllib.error.HTTPError as ex:
                status = getattr(ex, "code", 0)
                detail = ""
                try:
                    detail = ex.read(300).decode("utf-8", "ignore")
                except Exception:
                    pass
                if status == 404:
                    raise ModelUnavailable(
                        f"本地模型 {model or ''} 未安装或名称不对，请换一个已装的模型") from ex
                if status in (400, 422):
                    # 注意：不能把 urllib 的 HTTPError 塞给 SDK 的错误类（它要 httpx 响应，
                    # 会炸出 AttributeError 把真实原因盖掉），这里造一个最小替身。
                    raise BadRequestError(
                        f"{status} {detail[:200]}",
                        response=_ErrResp(status), body=detail) from ex
                raise
            except urllib.error.URLError as ex:
                if not isinstance(getattr(ex, "reason", None), socket.timeout):
                    raise
                if stop is not None and stop.is_set():
                    raise Cancelled("已停止等待这条回复") from ex
                if time.time() >= deadline:
                    raise APITimeoutError(
                        "模型响应等待超时（可点「⏹ 停止」或换个更小的模型）") from ex
                time.sleep(0.2)
            except (TimeoutError, socket.timeout) as ex:
                if stop is not None and stop.is_set():
                    raise Cancelled("已停止等待这条回复") from ex
                if time.time() >= deadline:
                    raise APITimeoutError(
                        "模型响应等待超时（可点「⏹ 停止」或换个更小的模型）") from ex
                time.sleep(0.2)

    @staticmethod
    def _native_ollama_once(origin, model, messages, temperature, max_tokens,
                            kw, seg, stop, on_delta=None, task: str = "chat",
                            on_think=None):
        """一次原生 /api/chat 调用（流式聚合；带 tools 时非流式）。

        返回与 OpenAI SDK 同形的对象：resp.choices[0].message.content / .tool_calls，
        resp.choices[0].finish_reason —— 上层代码无需感知底层换了通路。

        v0.27.13「按价值分配思考链」：
          · think 由 _want_think(task, messages) 决定（聊天/工具恒 False；后台提炼/自测
            恒 True；brain/filegen 命中"值得想"的信号才 True）；
          · 开思考时自动多给 _THINK_RESERVE 输出配额 —— 否则思考会把 num_predict 吃光，
            表现就是"跑了一分多钟、正文 0 字"（实测 165s / 0 字）；
          · 思考超过 _THINK_BUDGET_SEC 就断开，带着已想出的思路**关思考强制收敛**
            （reasoning budget forcing）—— 保证"想得完想不完，都一定有答复"。
        """
        # v0.29.1：冷加载体检 —— 模型不在显存时**先显式预热**，再发正式请求。
        # 于是"装载 40s"不再被算进正式请求的"首字"，也不会伪装成模型卡死/读超时；
        # 日志里的 `装载Xs` 从此只用于回答"这次到底有没有白等"。
        try:
            if not local_model_resident(origin, model):
                log.warning("本地模型 %s 当前未驻留 → 先预热"
                            "（本机实测冷加载约 40s，只这一次）", model)
                _t_warm = time.time()
                if prewarm_local(origin, model):
                    log.info("预热完成 %.1fs，模型已驻留（后续请求直接命中）",
                             time.time() - _t_warm)
        except Exception:
            log.debug("冷加载体检失败（忽略，继续请求）", exc_info=True)
        tools = kw.get("tools")
        streaming = on_delta is not None or not tools
        think_on = _want_think(task, messages)
        # v0.30.13：思考预算按本机速度收口（慢机上"想满 45 秒"只是让用户白等）
        # ⚠️ v0.30.15 修：这里以前写的是 `_speed_host(base_url)`，而本函数的形参叫
        #    `origin` —— 于是**每次本地请求都在这一行 NameError**，被 `_create_hb`
        #    的 `except Exception` 吞掉后回退兼容端点（日志只留一行 INFO）。
        #    后果是三个参数全被静默丢弃：`keep_alive`（模型被换出→下次冷加载 40s）、
        #    `num_ctx`、`think=false`（思考照开）；而且因为异常在请求**之前**抛出，
        #    连下面"本机实测速度"的记录点都永远跑不到 → 预算一直用保守默认 60 tok/s。
        #    真机日志（2026-09-17 16:57，本机）："你好" → 首字 20s、冷加载 45s。
        think_budget = think_budget_of(task, model, _speed_host(origin))
        if tools:
            # 带工具时是非流式，思考无法中途掐断 → 只有后台车道才允许，免得聊天被拖住
            think_on = think_on and task in _THINK_ON

        def _mk_body(use_think: bool, extra_msgs=None):
            opts: Dict[str, Any] = {"num_ctx": _LOCAL_NUM_CTX}
            if temperature is not None:
                opts["temperature"] = float(temperature)
            if max_tokens:
                # 思考与正文共用同一份 num_predict → 开思考必须预留，否则正文没额度
                opts["num_predict"] = int(max_tokens) + (
                    _THINK_RESERVE if use_think else 0)
            b: Dict[str, Any] = {
                "model": model,
                "messages": _native_msgs(list(messages) + list(extra_msgs or [])),
                "stream": bool(streaming),
                "keep_alive": _LOCAL_KEEP_ALIVE,
                "options": opts,
                "think": bool(use_think),
            }
            if tools:
                b["tools"] = _jsonable(tools)
            return b

        def _open(b):
            req = urllib.request.Request(
                origin + "/api/chat", data=json.dumps(b).encode("utf-8"),
                headers={"Content-Type": "application/json"})
            return urllib.request.urlopen(req, timeout=seg)

        t0 = time.time()

        def _run(use_think: bool, extra_msgs=None):
            """跑一轮原生请求 → (正文, tool_calls, stats, finish, 首字秒, 思考文本, 是否超预算)"""
            body = _mk_body(use_think, extra_msgs)
            try:
                r = _open(body)
            except urllib.error.HTTPError as ex:
                detail = ""
                try:
                    detail = ex.read(300).decode("utf-8", "ignore").lower()
                except Exception:
                    pass
                # 只有"服务器明确说不认 think"时才去掉重试，别把任何 400 都当成 think 问题
                if getattr(ex, "code", 0) == 400 and "think" in detail:
                    body.pop("think", None)
                    r = _open(body)
                else:
                    raise
            t_first: Optional[float] = None
            think_txt: List[str] = []
            parts: List[str] = []
            tcs: List[Any] = []
            finish = ""
            stats: Dict[str, Any] = {}
            cut = False
            with r:
                if not streaming:
                    j = json.loads(r.read().decode("utf-8"))
                    stats = j
                    m = j.get("message") or {}
                    if m.get("thinking"):
                        think_txt.append(str(m["thinking"]))
                        if on_think is not None:
                            try:
                                on_think(str(m["thinking"]),
                                         "".join(think_txt))
                            except Exception:
                                pass
                    parts.append(m.get("content") or "")
                    tcs = list(m.get("tool_calls") or [])
                    finish = j.get("done_reason") or "stop"
                else:
                    for raw in r:                # 每个 chunk 到达都重置读计时
                        if stop is not None and stop.is_set():
                            raise Cancelled("已停止等待这条回复")
                        raw = raw.strip()
                        if not raw:
                            continue
                        try:
                            c = json.loads(raw.decode("utf-8"))
                        except Exception:
                            continue
                        m = c.get("message") or {}
                        if m.get("thinking"):
                            _tk = str(m["thinking"])
                            think_txt.append(_tk)
                            if on_think is not None:
                                try:
                                    on_think(_tk, "".join(think_txt))
                                except Exception:
                                    pass
                            # 想太久还没吐正文 → 断流去收敛，别让用户干等
                            if (use_think and not parts
                                    and (time.time() - t0) > think_budget):
                                cut = True
                                break
                        txt = m.get("content")
                        if txt:
                            if t_first is None:
                                t_first = time.time() - t0
                            parts.append(txt)
                            if on_delta is not None:
                                try:
                                    on_delta(txt, "".join(parts))
                                except Exception:
                                    pass
                        if m.get("tool_calls"):
                            tcs.extend(m.get("tool_calls") or [])
                        if c.get("done"):
                            stats = c
                            finish = c.get("done_reason") or "stop"
            return ("".join(parts), tcs, stats, finish, t_first,
                    "".join(think_txt), cut)

        text, tcs, stats, finish, t_first, think_txt, cut = _run(think_on)
        think_chars = len(think_txt.strip())
        converged = False
        # 开了思考却没吐正文（思考吃光配额 / 超预算被掐断）→ 强制收敛：
        # 把已经想出来的思路当上下文塞回去，关思考直接要答案。这是"核心不能掉"的保险。
        if think_on and not text.strip() and not tcs:
            hint = think_txt.strip()
            extra = []
            if hint:
                extra = [
                    {"role": "assistant", "content": hint[-2500:]},
                    {"role": "user", "content": _CONVERGE_PROMPT}]
            log.info("思考链未吐正文（%s，已想 %d 字）→ 关思考强制收敛",
                     "超预算" if cut else "配额被吃光", think_chars)
            # v0.31.6：把"被掐断"这件事记下来 —— 界面据此如实播报
            # 「💭 思考超预算（20s/313字）→ 已带着思路收敛作答」，
            # 别再让用户以为"深度思考莫名断了一小段"。
            try:
                LAST_THINK_CUT.clear()
                LAST_THINK_CUT.update({"at": time.time(), "cut": bool(cut),
                                       "chars": int(think_chars),
                                       "budget": float(think_budget),
                                       "task": str(task or "")})
            except Exception:                                # noqa: BLE001
                pass
            text, tcs, stats, finish, t_first, _tk, _c = _run(False, extra)
            think_txt = think_txt + _tk
            converged = True
        if stop is not None and stop.is_set():
            raise Cancelled("已停止等待这条回复")
        # —— 诊断日志：这一行的几个数就是"为什么慢"的全部答案 ——
        ns = 1e9
        pt = stats.get("prompt_eval_count", 0) or 0
        et = stats.get("eval_count", 0) or 0
        ed = (stats.get("eval_duration", 0) or 0) / ns
        ld = (stats.get("load_duration", 0) or 0) / ns
        ped = (stats.get("prompt_eval_duration", 0) or 0) / ns
        # v0.30.7：生成速率算不出来就写「—」，别再打 1000000.0tok/s 这种假数
        # （ed≈0 时 et/ed 会飙到 1e6，日志上一眼看去像"快得离谱"，其实是没数）
        gtps = (et / ed) if ed > 0.001 else 0.0
        log.info("local-native %s | 提示%d tok | 首字%s | 总%.2fs | 生成%d tok@%s"
                 " | 装载%.1fs | 思考%s%s",
                 model, pt,
                 ("%.2fs" % t_first) if t_first is not None else "—",
                 time.time() - t0, et,
                 ("%.1ftok/s" % gtps) if gtps > 0 else "—", ld,
                 ("开(%d字)" % think_chars) if think_on else "关",
                 " | 已强制收敛" if converged else "")
        # —— v0.30.7：把本机实测速度记下来，提示词预算与超时都按它算 ——
        # 优先用 Ollama 自报的 prompt_eval_duration（最准，不含网络与排队）；
        # 拿不到再退回「首字 - 装载」，且只在提示足够长时采信（短提示测不准）。
        _pf = 0.0
        _span = max(0.001, (t_first or 0.0) - ld)      # 首字里去掉装载
        if ped > 0.05 and pt >= 32:
            _pf = pt / ped
            # v0.30.13：识别「KV 前缀缓存命中」样本 —— 命中时 prompt_eval_count 仍是全量、
            # duration 只算新增部分 → 速度虚高（朋友机实测 3977.7 tok/s，真实约 17.8）。
            # 两条判据：① 超过物理合理上限；② 预填充只占首字的极小部分（说明时间花在别处）。
            if not prefill_sample_ok(_pf, ped, _span):
                log.info("预填充样本疑似缓存命中（%.0f tok/s；ped %.2fs / 首字 %.2fs）"
                         "→ 不采信，沿用上次实测", _pf, ped, _span)
                _pf = 0.0
        elif t_first is not None and pt >= 128:
            _pf = pt / _span
        if _pf > 0:
            record_speed(model, _ollama_origin(origin) or origin,
                         prefill_tps=_pf, gen_tps=gtps, prompt_tokens=pt)
            log.info("本机实测 %s：预填充 %.1f tok/s、生成 %.1f tok/s —— "
                     "系统提示预算 %d 字（首字目标 10s）",
                     model, _pf, gtps, budget(model, _ollama_origin(origin) or origin))
        if ld > 3.0:
            LAST_COLD_LOAD.update({"model": model, "secs": ld, "ts": time.time()})
            log.warning("本地模型发生冷加载（%.1fs）——keep_alive 未生效或模型刚被换出",
                        ld)
        tool_calls = None
        if tcs:
            tool_calls = []
            for i, c in enumerate(tcs):
                fn = (c or {}).get("function") or {}
                args = fn.get("arguments")
                if not isinstance(args, str):
                    try:
                        args = json.dumps(args or {}, ensure_ascii=False)
                    except Exception:
                        args = "{}"
                tool_calls.append(SimpleNamespace(
                    id=(c or {}).get("id") or f"call_{i}",
                    type="function",
                    function=SimpleNamespace(name=fn.get("name") or "",
                                             arguments=args)))
        msg = SimpleNamespace(role="assistant", content=text, tool_calls=tool_calls)
        choice = SimpleNamespace(message=msg,
                                 finish_reason=("length" if finish == "length"
                                                else "stop"))
        return SimpleNamespace(choices=[choice])

    @staticmethod
    def _create_stream_agg(client, model, messages, temperature, max_tokens,
                           kw, seg, stop, on_delta=None, on_think=None):
        """流式补全：边收边聚合为与非流式一致的结构（choices[0].message.content）。

        - 冷加载/prompt 评估阶段没有任何字节，读超时（seg）罩住它；
        - 一旦开始出 token，每个 chunk 到达都重置读计时 → 长回答不再被掐；
        - 点「⏹ 停止」→ 关流断连，Ollama 真中断生成；
        - on_delta：每个增量 token 实时回调（UI 真"打字机"效果）。
        - v0.28.3：① delta.reasoning_content（DeepSeek 思考字段）→ on_think 实时回调，
          不再静默丢弃；② content 里内嵌的 <think>…</think> 由 _ThinkSplitter
          剥离到思考通道 —— 否则 UI 的 insertHtml 会把它当未知 HTML 标签
          **连同内容一起吞掉**，表现就是"回复文字部分丢失"。
        """
        stream = client.chat.completions.create(
            model=model, messages=messages, temperature=temperature,
            max_tokens=max_tokens, timeout=seg, stream=True, **kw)
        parts: List[str] = []
        think_parts: List[str] = []
        finish = ""
        sp = _ThinkSplitter()
        try:
            for chunk in stream:
                if stop is not None and stop.is_set():
                    raise Cancelled("已停止等待这条回复")
                if not getattr(chunk, "choices", None):
                    continue                     # 心跳/统计 chunk，无正文
                ch = chunk.choices[0]
                delta = getattr(ch, "delta", None)
                if delta is None:
                    continue
                rk = getattr(delta, "reasoning_content", None)
                if rk:
                    think_parts.append(rk)
                    if on_think is not None:
                        try:
                            on_think(rk, "".join(think_parts))
                        except Exception:
                            pass                 # 回调绝不影响生成本身
                if getattr(delta, "content", None):
                    b_inc, t_inc = sp.feed(delta.content)
                    if t_inc:
                        think_parts.append(t_inc)
                        if on_think is not None:
                            try:
                                on_think(t_inc, "".join(think_parts))
                            except Exception:
                                pass
                    if b_inc:
                        parts.append(b_inc)
                        if on_delta is not None:
                            try:
                                on_delta(b_inc, "".join(parts))
                            except Exception:
                                pass                 # 回调绝不影响生成本身
                fr = getattr(ch, "finish_reason", None)
                if fr:
                    finish = fr
        finally:
            b_tail, t_tail = sp.finish()
            if t_tail:
                think_parts.append(t_tail)
                if on_think is not None:
                    try:
                        on_think(t_tail, "".join(think_parts))
                    except Exception:
                        pass
            if b_tail:
                parts.append(b_tail)
                if on_delta is not None:
                    try:
                        on_delta(b_tail, "".join(parts))
                    except Exception:
                        pass
            try:
                stream.close()
            except Exception:
                pass
        if stop is not None and stop.is_set():
            raise Cancelled("已停止等待这条回复")
        msg = SimpleNamespace(role="assistant", content="".join(parts),
                              tool_calls=None)
        choice = SimpleNamespace(message=msg, finish_reason=finish or "stop")
        return SimpleNamespace(choices=[choice])

    # ---------- 低层：单次 create（工具轮等需要原生响应的场景） ----------
    def create(self, base_url: str, model: str, api_key: str, *,
               messages: List[dict], temperature: float = 0.7,
               max_tokens: int = 900, timeout: Optional[int] = None,
               task: str = "chat", attempts: int = 1,
               extra: Optional[dict] = None,
               stop: Optional[threading.Event] = None):
        """带并发闸与有限重试的单次补全调用，返回原生 response（可读 tool_calls）。

        stop（threading.Event）：用户点了「⏹ 停止」时置位 → 心跳分片内抛 Cancelled。
        """
        prof = TASK_PROFILES.get(task, TASK_PROFILES["chat"])
        timeout = timeout or prof.get("timeout", 120)
        if _is_local(base_url):                  # v0.18.1：本地预算下限
            timeout = max(timeout, _LOCAL_TIMEOUT_FLOOR.get(task, timeout))
            # v0.30.7：再按本机实测速度抬到够用（保险丝）。慢机上"提示词读完"
            # 本身就要几十秒，不该被当成"模型卡死"直接判失败。
            timeout = adapt_timeout(timeout, base_url, model, messages, max_tokens)
        # v0.27.10：前台（用户在等）抢占模型；后台（自学/自测）先让路再跑
        fg = self._is_fg(task)
        bg_ev = None
        if fg:
            self._fg_enter()
        else:
            try:
                bg_ev = self._bg_enter(timeout)
            except LLMBusy:
                self._stat(base_url, "busy")
                raise
        stop_eff = _AnyStop(stop, bg_ev) if bg_ev is not None else stop
        try:
            if stop_eff is not None and stop_eff.is_set():
                raise Cancelled("已停止等待这条回复")
            if not self._semaphore(base_url, task).acquire(timeout=timeout):
                self._stat(base_url, "busy")
                raise LLMBusy(f"模型服务正忙（排队超过 {timeout}s），请稍后再试")
            client = self.client_for(base_url, api_key)
            try:
                kw = self._with_keepalive(base_url, dict(extra or {}),
                                          _think_enabled(base_url, task, messages))
                last: Optional[Exception] = None
                deadline = time.time() + timeout
                for attempt in range(max(1, attempts)):
                    try:
                        resp = self._create_hb(client, model, messages,
                                               temperature, max_tokens, kw,
                                               deadline, stop_eff,
                                               bg=(bg_ev is not None), task=task)
                        self._stat(base_url, "ok")
                        return resp
                    except Cancelled:
                        self._stat(base_url, "fail")
                        if self._preempted(bg_ev, stop):
                            raise LLMBusy(
                                "前台正在用模型，后台任务已让路（稍后自动继续）") from None
                        raise
                    except NotFoundError as ex:
                        self._stat(base_url, "fail")
                        if is_local_url(base_url) and "not found" in str(ex).lower():
                            raise ModelUnavailable(
                                f"本地模型 {model or ''} 未安装或名称不对，请换一个已装的模型") from ex
                        raise
                    except (AuthenticationError, PermissionDeniedError,
                            BadRequestError):
                        raise
                    except APITimeoutError:
                        raise                    # 心跳已耗尽总预算，直接交给上层翻译
                    except Exception as ex:                      # 网络/限流/5xx
                        last = ex
                        self._stat(base_url, "fail")
                        if attempt < max(1, attempts) - 1:
                            delay = min(0.5 * (2 ** attempt), 2.0) + random.random() * 0.4
                            time.sleep(delay)
                raise last if last else RuntimeError("create failed")
            finally:
                self._semaphore(base_url, task).release()
        finally:
            if fg:
                self._fg_leave()
            else:
                self._bg_leave(bg_ev)

    # ---------- 高层：complete（自动续写 length 截断） ----------
    def complete(self, base_url: str, model: str, api_key: str,
                 messages: List[dict], *,
                 task: str = "chat",
                 max_tokens: Optional[int] = None,
                 temperature: Optional[float] = None,
                 timeout: Optional[int] = None,
                 retries: Optional[int] = None,
                 continue_cuts: Optional[int] = None,
                 stop: Optional[threading.Event] = None,
                 on_delta: Optional[Any] = None,
                 on_think: Optional[Any] = None) -> str:
        """高层补全：on_delta(delta, full_text) 传出实时增量（UI 流式展示用）；
        on_think(inc, full_think) 传出思考增量（UI 灰度思考显示用）。"""
        prof = TASK_PROFILES.get(task, TASK_PROFILES["chat"])
        max_tokens = max_tokens if max_tokens is not None else prof.get("max_tokens", 1500)
        temperature = temperature if temperature is not None else prof.get("temperature", 0.7)
        timeout = timeout or prof.get("timeout", 180)
        if _is_local(base_url):
            # v0.30.13：本地按实测生成速度**收口单轮长度**。
            # 朋友机（生成 6.8 tok/s）用 chat 默认 1600 → 单轮 235s，加续写最坏 12 分钟；
            # 日志里的 `总247.31s | 生成1626 tok@6.8tok/s` 就是这一条 —— 用户会以为卡死。
            _want = max_tokens
            max_tokens = clamp_max_tokens(
                model, _speed_host(base_url), _want,
                target_secs=_TASK_TARGET_SECS.get(task, 45.0),
                cap=prof.get("max_tokens", 1500))
            if max_tokens < _want:
                log.info("按本机生成速度收口单轮长度：%d → %d tok（task=%s，目标 %.0fs）",
                         _want, max_tokens, task, _TASK_TARGET_SECS.get(task, 45.0))
            # v0.18.1：本地预算下限
            timeout = max(timeout, _LOCAL_TIMEOUT_FLOOR.get(task, timeout))
            timeout = adapt_timeout(timeout, base_url, model, messages, max_tokens)
        retries = retries if retries is not None else prof.get("retries", 1)
        cuts_left = continue_cuts if continue_cuts is not None else prof.get("continue_cuts", 2)
        # v0.27.10：前台（用户在等）抢占模型；后台（自学/自测）先让路再跑
        fg = self._is_fg(task)
        bg_ev = None
        if fg:
            self._fg_enter()
        else:
            try:
                bg_ev = self._bg_enter(timeout)
            except LLMBusy:
                self._stat(base_url, "busy")
                raise
        stop_eff = _AnyStop(stop, bg_ev) if bg_ev is not None else stop
        try:
            if stop_eff is not None and stop_eff.is_set():
                raise Cancelled("已停止等待这条回复")
            if not self._semaphore(base_url, task).acquire(timeout=timeout):
                self._stat(base_url, "busy")
                raise LLMBusy(f"模型服务正忙（排队超过 {timeout}s），请稍后再试")
            client = self.client_for(base_url, api_key)
            try:
                last: Optional[Exception] = None
                deadline = time.time() + timeout
                kw0 = self._with_keepalive(base_url, {},
                                           _think_enabled(base_url, task, messages))
                for attempt in range(retries + 1):              # retries=1 → 最多 2 次
                    try:
                        resp = self._create_hb(client, model, messages,
                                               temperature, max_tokens, kw0, deadline,
                                               stop_eff, on_delta=on_delta,
                                               bg=(bg_ev is not None), task=task,
                                               on_think=on_think)
                        text = (resp.choices[0].message.content or "").strip()
                        # v0.28.3 防丢字兜底：非流式路径 content 里若残留内嵌
                        # <think>…</think>（UI 的 HTML 渲染会把它当标签吞掉），就地剥离
                        text = _strip_think(text)
                        finish = getattr(resp.choices[0], "finish_reason", "") or ""
                        # 被 max_tokens 截断 → 自动续写补全，避免"话说一半"
                        while finish == "length" and cuts_left > 0:
                            cuts_left -= 1
                            cont = self._create_hb(
                                client, model,
                                messages + [
                                    {"role": "assistant", "content": text},
                                    {"role": "user",
                                     "content": "继续，从中断处接着输出，不要重复已说过的内容，说完为止。"}],
                                temperature, max_tokens, kw0, deadline, stop_eff,
                                bg=(bg_ev is not None), task=task)
                            piece = (cont.choices[0].message.content or "").strip()
                            finish = getattr(cont.choices[0], "finish_reason", "") or ""
                            if not piece:
                                break
                            text = text + piece
                        self._stat(base_url, "ok")
                        return text
                    except Cancelled:
                        self._stat(base_url, "fail")
                        if self._preempted(bg_ev, stop):
                            raise LLMBusy(
                                "前台正在用模型，后台任务已让路（稍后自动继续）") from None
                        raise
                    except NotFoundError as ex:
                        self._stat(base_url, "fail")
                        if is_local_url(base_url) and "not found" in str(ex).lower():
                            raise ModelUnavailable(
                                f"本地模型 {model or ''} 未安装或名称不对，请在「大脑」下拉里换一个已装的模型")
                        raise
                    except (AuthenticationError, PermissionDeniedError,
                            BadRequestError) as ex:
                        self._stat(base_url, "fail")
                        raise
                    except APITimeoutError as ex:
                        self._stat(base_url, "fail")
                        raise
                    except Exception as ex:                     # 网络/超时/限流/5xx/Ollama加载中
                        last = ex
                        self._stat(base_url, "fail")
                        if attempt < retries:
                            # 指数退避 + 抖动：本地 Ollama 冷加载常要十几秒，避开并发重试风暴
                            delay = min(0.8 * (2 ** attempt), 3.0) + random.random() * 0.6
                            log.info("retry %s attempt %d after %.1fs: %s",
                                     task, attempt + 1, delay, describe_error(ex))
                            time.sleep(delay)
                if last:
                    # Ollama 特有错误 → 翻成可读中文
                    msg = describe_error(last)
                    if "not found" in msg and ("11434" in (base_url or "") or "8080" in (base_url or "")):
                        raise ModelUnavailable(f"本地模型 {model or ''} 未安装或名称不对，请在「大脑」下拉里换一个已装的模型")
                    raise last
                return ""
            finally:
                self._semaphore(base_url, task).release()
        finally:
            if fg:
                self._fg_leave()
            else:
                self._bg_leave(bg_ev)


# 模块级单例：全桌面共享同一客户端池与并发闸
gw = Gateway()
