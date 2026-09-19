# -*- coding: utf-8 -*-
"""media_job.py —— 统一媒体任务抽象（v0.30.5）

对标 BaiLongma 的 `media-modes.js`：图 / 视频 / 漫剧 / 广告 / 配音**同源**，
不再让每条业务线各自拼参数、各自处理降级。

## 解决什么

此前三处各写一遍：
  · `creators.generate_image(conf, prompt, out, size=...)`  出图
  · `videoeng.gen_i2v(cfg, img, motion, out, duration=...)`  图生视频
  · `creators.synth_speech(text, out_mp3, voice=...)`        配音
每个调用点都要自己判断「引擎配了没」「失败了怎么办」「产物记哪儿」。
本模块把这些收敛成**一个任务对象 + 一个执行函数**。

## 三条诚实边界（改之前先读）

1. **没有适配器 = `skipped`，不是 `done`**。没配图像引擎时不许产出空文件、
   也不许把状态写成成功 —— 界面上要能看出「这一步没做」。
2. **失败必须带原因**。`error` 里写清是「没配引擎」「引擎报错」还是「产物不存在」，
   不做"静默成功"。
3. **产物存在性校验**：适配器说成功但文件不在 → 判 `failed`。
   （防止上游函数返回了路径却没真落盘。）

## 依赖注入

真实生成函数不在本模块 import（会拖进 Qt / 网络栈）。由调用方注册：

    media_job.register("image", my_fn)      # my_fn(job, ctx) -> (ok, path_or_msg)
    media_job.install_defaults()            # 或用内置的 creators/videoeng 适配器

`ctx` 里带引擎配置（`image_conf` / `video_conf` / `voice`），因为配置归属 UI 层。
"""
from __future__ import annotations

import json
import os
import time

VALID_KINDS = ("image", "video", "manga", "ad", "audio")

KIND_LABEL = {
    "image": "🖼 出图",
    "video": "🎬 视频",
    "manga": "🎞 漫剧",
    "ad": "📣 广告设计",
    "audio": "🔊 配音",
}

#: 各工种的降级链（引擎名，按序尝试；适配器按 engine 决定怎么做）
#: 空元组 = 交给适配器自己决定，不做引擎级降级。
FALLBACKS = {
    "image": ("cloud", "local"),
    "video": ("seedance", "comfyui"),
    "manga": ("cloud", "local"),
    "ad": ("cloud", "local"),
    "audio": (),
}

#: kind -> fn(job, ctx) -> (ok: bool, path_or_msg: str)
ADAPTERS = {}


def ordered_engines(kind: str, ctx: dict = None) -> list:
    """按「当前配置下哪个引擎真的可用」给降级链排序（v0.30.6，媒体矩阵第一步）。

    为什么需要（真实症状）：`FALLBACKS` 是**写死的顺序**（cloud → local）。
    用户只配了本地引擎时，每一次生成都会先拿"没配的云端"试一遍再降级 ——
    失败原因栏里躺着的其实是"你没配云端"，而真正能干的引擎排在后面，
    用户看到的是"生成失败"而不是"用本地引擎生成成功"。

    规则：可用性分成 0/1/2（明确可用 / 不确定 / 明确不可用），
    **稳定排序**保持原优先级（同样可用时，仍按 FALLBACKS 顺序）。
    拿不到配置信息时**原样返回**（不改行为）。
    """
    ctx = ctx or {}
    base = list(FALLBACKS.get(kind, ()))
    if not base:
        return []
    if kind in ("image", "manga", "ad"):
        conf = ctx.get("image_conf") or {}
    elif kind == "video":
        conf = ctx.get("video_conf") or {}
    else:
        conf = {}
    if not conf:
        return base
    prov = str(conf.get("provider") or "").strip().lower()
    has_ep = bool(str(conf.get("base_url") or "").strip())
    has_key = bool(str(conf.get("api_key") or "").strip())

    def score(e: str) -> int:
        e = (e or "").lower()
        if e in ("local", "comfy", "comfyui", "sdwebui"):
            if kind == "video":
                return 0 if has_ep else 2          # comfyui 只要端点
            return 0 if (has_ep and prov in ("", "sdwebui", "local", "comfy")) else 2
        if e == "cloud":
            if not has_ep or prov in ("sdwebui",):
                return 2
            return 0 if (has_key or has_ep) else 1  # 本地 Ollama 式端点也要 key="local"
        if e == "seedance":
            return 0 if (has_ep and has_key) else 2
        return 1

    return sorted(base, key=score)                 # 稳定排序


# --------------------------------------------------------------------------
# 存储
# --------------------------------------------------------------------------
def _data_dir() -> str:
    d = os.environ.get("PASM_STUDIO_DIR")
    if d:
        return d
    try:
        import logsetup
        return logsetup.data_dir()
    except Exception:  # noqa: BLE001
        return os.path.join(os.path.expanduser("~"), ".pasmstudio")


def _path() -> str:
    return os.path.join(_data_dir(), "media_jobs.json")


def _load() -> list:
    try:
        with open(_path(), "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, list) else []
    except Exception:  # noqa: BLE001
        return []


def _save(jobs: list) -> None:
    try:
        os.makedirs(_data_dir(), exist_ok=True)
        tmp = _path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(jobs[-400:], f, ensure_ascii=False, indent=1)
        os.replace(tmp, _path())
    except Exception:  # noqa: BLE001  存不下去也不能让出图失败
        pass


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M")


#: v0.30.5：进程内自增序号 —— 保证同一毫秒内连续建的任务 id 也不重复
_ID_SEQ = [0]


def _new_id() -> str:
    """任务 id：秒级时间戳 + 毫秒 + 进程内自增序号。

    v0.30.5 修正：原实现是"秒级时间戳 + 毫秒%1000"，紧密循环里多次调用会落在
    **同一毫秒**（实测连建 6 个任务拿到 6 个相同 id）→ save() 按 id 覆盖，
    批量出图（漫剧一次 8 格）会静默丢掉除最后一个之外的全部任务。
    加自增序号后同进程内绝不重复。
    """
    _ID_SEQ[0] = (_ID_SEQ[0] + 1) % 100000
    return "mj_%s_%03d%05d" % (time.strftime("%y%m%d%H%M%S"),
                               int((time.time() * 1000) % 1000), _ID_SEQ[0])


# --------------------------------------------------------------------------
# 适配器注册
# --------------------------------------------------------------------------
def register(kind: str, fn):
    """注册某工种的执行适配器；新增媒体类型不改本模块调度代码。"""
    ADAPTERS[kind] = fn
    return fn


def adapter_for(kind: str):
    return ADAPTERS.get(kind)


def _exists_any(p: str) -> bool:
    return bool(p) and os.path.exists(p)


# --------------------------------------------------------------------------
# 任务对象
# --------------------------------------------------------------------------
def new(kind: str, prompt: str, out_dir: str = "", dir: str = "",
        wid: str = "", **params) -> dict:
    """建一个媒体任务。`dir` 是归属工作目录（与 worklog 对齐），`wid` 关联工作流任务。"""
    kind = (kind or "").strip() or "image"
    if kind not in VALID_KINDS:
        kind = "image"
    od = out_dir or os.path.join(_data_dir(), "media", kind)
    job = {
        "id": _new_id(),
        "kind": kind,
        "label": KIND_LABEL.get(kind, kind),
        "prompt": (prompt or "").strip(),
        "out_dir": od,
        "dir": dir or "",
        "wid": wid or "",
        "params": dict(params or {}),
        "attempts": list(FALLBACKS.get(kind, ())),
        "status": "pending",        # pending|running|done|failed|skipped
        "engine": "",
        "artifacts": [],
        "error": "",
        "reason": "",
        "created": _now(),
        "updated": _now(),
    }
    return job


def validate(job: dict) -> tuple:
    """能不能跑 —— 跑不了要说清为什么。"""
    if not job:
        return False, "空任务"
    if not (job.get("prompt") or "").strip():
        return False, "没有提示词 / 文本"
    if not adapter_for(job.get("kind") or ""):
        return False, "no_adapter"
    return True, ""


def run(job: dict, ctx: dict = None) -> dict:
    """真跑一个媒体任务（就地更新并返回 job）。

    返回的 job 里 `status` 一定如实：
      done    = 适配器报成功 **且产物存在**
      failed  = 适配器报失败 / 抛异常 / 产物没落盘
      skipped = 没有适配器（没配引擎），**不算完成**
    """
    ctx = dict(ctx or {})
    job["status"] = "running"
    job["updated"] = _now()
    ok, why = validate(job)
    if not ok:
        if why == "no_adapter":
            job["status"] = "skipped"
            job["reason"] = "no_adapter"
            job["error"] = "该工种没有可用适配器（引擎未配置）"
        else:
            job["status"] = "failed"
            job["error"] = why
        job["updated"] = _now()
        return job

    # v0.30.6：先按当前配置把降级链排好（可用引擎排前），再逐档尝试
    if ctx:
        _ord = ordered_engines(job["kind"], ctx)
        if _ord and _ord != list(job.get("attempts") or ()):
            job["chain_from"] = list(job.get("attempts") or ())
            job["attempts"] = _ord
            job["chain_note"] = "按当前配置排序（可用引擎优先）"
    fn = adapter_for(job["kind"])
    engine = ""
    last_err = ""
    for att in (job.get("attempts") or ("",)):
        engine = att
        try:
            r = fn(job, dict(ctx, engine=engine))
        except Exception as e:  # noqa: BLE001  适配器异常 = 这一步失败
            last_err = "适配器异常：%s" % e
            continue
        if isinstance(r, tuple) and len(r) >= 2:
            a_ok, payload = bool(r[0]), r[1]
        else:
            a_ok, payload = bool(r), str(r or "")
        if not a_ok:
            last_err = str(payload or "适配器报失败")
            continue
        # 说成功就要有产物 —— 否则判失败（不静默成功）
        # v0.30.13：适配器可以返回**多个**产物（一条广告 = 方案 .md + 主视觉图）。
        # 以前只收单个 payload，"一个任务多份交付"就只能记一个（广告的方案文件会丢）。
        _cands = ([str(x) for x in payload] if isinstance(payload, (list, tuple))
                  else [str(payload)])
        _arts = [c for c in _cands if _exists_any(c)]
        if _arts:
            job["status"] = "done"
            job["engine"] = engine
            job["artifacts"] = _arts
            job["error"] = ""
            job["reason"] = ""
            job["updated"] = _now()
            return job
        last_err = "适配器声称成功但产物不存在：%s" % (payload,)
    job["status"] = "failed"
    job["engine"] = engine
    _tried = [e for e in (job.get("attempts") or ()) if e]
    job["error"] = last_err or "全部降级链都失败"
    job["tried"] = _tried
    if len(_tried) > 1:
        job["error"] = "%s（已依次尝试：%s）" % (job["error"], " → ".join(_tried))
    job["updated"] = _now()
    return job


def run_and_record(job: dict, ctx: dict = None, link_worklog: bool = True) -> dict:
    """跑 + 落盘 + （可选）把产物挂到关联的工作流任务上。"""
    run(job, ctx)
    save(job)
    if link_worklog and job.get("wid") and job.get("artifacts"):
        try:
            import worklog as WL
            for a in job["artifacts"]:
                WL.add_artifact(job["wid"], a)
        except Exception:  # noqa: BLE001
            pass
    return job


def save(job: dict) -> None:
    """把任务写进 media_jobs.json（同 id 覆盖）。"""
    if not job:
        return
    jobs = _load()
    for i, j in enumerate(jobs):
        if j.get("id") == job.get("id"):
            jobs[i] = job
            break
    else:
        jobs.append(job)
    _save(jobs)


def all_jobs(n: int = 60) -> list:
    return _load()[-n:][::-1]


def by_dir(d: str, n: int = 50) -> list:
    return [j for j in _load() if j.get("dir") == d][-n:][::-1]


def summary() -> dict:
    """按状态统计（面板用）。"""
    jobs = _load()
    st = {}
    for j in jobs:
        st[j.get("status") or "?"] = st.get(j.get("status") or "?", 0) + 1
    return {"total": len(jobs), "by_status": st}


# --------------------------------------------------------------------------
# 内置适配器（惰性 import，失败不影响本模块可用）
# --------------------------------------------------------------------------
def install_defaults() -> dict:
    """装内置适配器：真出图 / 真图生视频 / 真配 mp3。没配引擎时如实 skip。"""
    def _img(job, ctx):
        conf = ctx.get("image_conf") or {}
        if not conf:
            return False, "未配置图像引擎（设置 → 创作引擎）"
        import creators as CR
        if not CR.conf_ok(conf):
            return False, "图像引擎配置不完整"
        os.makedirs(job["out_dir"], exist_ok=True)
        out = os.path.join(job["out_dir"], "%s.png" % job["id"])
        p = CR.generate_image(conf, job["prompt"], out,
                              size=(job.get("params") or {}).get("size", "1024x1024"),
                              init_image=(job.get("params") or {}).get("init_image"),
                              strength=float((job.get("params") or {}).get("strength", 0.62)))
        return True, p or out

    def _vid(job, ctx):
        cfg = ctx.get("video_conf") or {}
        if not cfg:
            return False, "未配置视频引擎（设置 → 视频引擎）"
        import videoeng as VE
        ready, msg = VE.ready(cfg)
        if not ready:
            return False, msg or "视频引擎不可用"
        first = (job.get("params") or {}).get("first_frame") or ""
        if not first or not os.path.exists(first):
            return False, "图生视频需要首帧图（首帧缺失）"
        os.makedirs(job["out_dir"], exist_ok=True)
        out = os.path.join(job["out_dir"], "%s.mp4" % job["id"])
        p = VE.gen_i2v(cfg, first, job["prompt"], out,
                       duration=int((job.get("params") or {}).get("duration", 5)))
        return True, p or out

    def _aud(job, ctx):
        import creators as CR
        os.makedirs(job["out_dir"], exist_ok=True)
        out = os.path.join(job["out_dir"], "%s.mp3" % job["id"])
        p = CR.synth_speech(job["prompt"], out,
                            voice=ctx.get("voice") or CR.DEFAULT_VOICE)
        if not p:
            return False, "语音合成失败（声线或网络问题）"
        return True, p

    def _ad(job, ctx):
        """广告 = **方案 + 主视觉**，不是一张图（v0.30.13）。

        为什么必须与 `_img` 分开：以前这里是 `register("ad", _img)` —— 广告走的是
        **同一个出图函数**，产出同一张图、落在同一个 `image/` 分类。小志 2026-09-17
        真机反馈"广告设计和图像栏目没什么区别"，根因就是这一行。

        现在：先出**广告方案 .md**（创意说明策略层 + 标题/卖点/行动号召 + 主视觉提示词），
        再真出图（有引擎才出；没有就如实写"待生成"，绝不假装）。返回**两个**产物，
        台账里两份都能看到。
        """
        import ad_design as AD
        os.makedirs(job["out_dir"], exist_ok=True)
        conf = ctx.get("image_conf") or {}
        _img_fn = None
        if conf:
            try:
                import creators as CR
                if CR.conf_ok(conf):
                    _out = os.path.join(job["out_dir"], "%s.png" % job["id"])

                    def _gen(prompt):           # materialize 只传 prompt、要返回路径
                        return CR.generate_image(
                            conf, prompt, _out,
                            size=(job.get("params") or {}).get("size", "1024x1024"))
                    _img_fn = _gen
            except Exception:                   # noqa: BLE001
                _img_fn = None
        _pr = job.get("params") or {}
        r = AD.materialize(_pr.get("theme") or job["prompt"],
                           _pr.get("product") or job["prompt"],
                           _pr.get("style") or "现代简约",
                           out_dir=job["out_dir"], image_fn=_img_fn,
                           llm_fn=ctx.get("llm_fn"),
                           audience=_pr.get("audience") or "")
        if not r.get("ok"):
            return False, "广告方案未产出文件（写盘失败）"
        _files = [str(x) for x in (r.get("files") or []) if _exists_any(str(x))]
        if not _files:
            return False, "广告方案文件不存在"
        _files.sort(key=lambda p: (not p.lower().endswith(".md"),))   # 方案排第一
        return True, _files

    register("image", _img)
    register("ad", _ad)           # v0.30.13：广告 = 方案 + 主视觉（不再等同出图）
    register("manga", _img)       # 漫剧的单镜 = 出图
    register("video", _vid)
    register("audio", _aud)
    return dict(ADAPTERS)


# --------------------------------------------------------------------------
# 自检
# --------------------------------------------------------------------------
def _selftest() -> int:
    import shutil
    import tempfile
    fails = []
    stats = {"n": 0}

    def check(name, cond, extra=""):
        stats["n"] += 1
        print("  %s %s%s" % ("[OK]  " if cond else "[FAIL]", name,
                             ("  " + str(extra)) if (extra and not cond) else ""))
        if not cond:
            fails.append(name)

    print("== media_job 自检 ==")
    saved = os.environ.get("PASM_STUDIO_DIR")
    tmp = tempfile.mkdtemp(prefix="mj_")
    os.environ["PASM_STUDIO_DIR"] = tmp
    ADAPTERS.clear()
    try:
        # --- 1. 无适配器 = skipped（绝不谎报完成） ---
        j = new("image", "一只赛博朋克猫")
        check("new 生成合法任务（kind/id/status）",
              j["kind"] == "image" and j["status"] == "pending" and bool(j["id"]))
        run(j)
        check("无适配器 → skipped 而非 done", j["status"] == "skipped",
              j["status"])
        check("skipped 带 reason=no_adapter", j["reason"] == "no_adapter")

        # --- 2. 空提示词 = failed ---
        register("image", lambda job, ctx: (True, "x"))
        j2 = new("image", "   ")
        run(j2)
        check("空提示词 → failed", j2["status"] == "failed", j2["status"])

        # --- 3. 适配器报成功但产物不存在 → failed（诚实性核心） ---
        register("image", lambda job, ctx: (True, os.path.join(tmp, "不存在.png")))
        j3 = new("image", "猫")
        run(j3)
        check("声称成功但产物不存在 → failed", j3["status"] == "failed",
              j3["status"])

        # --- 4. 真产物 → done，且 engine 记下来 ---
        real = os.path.join(tmp, "ok.png")
        open(real, "wb").write(b"x")
        seen = {}

        def _ok(job, ctx):
            seen["engine"] = ctx.get("engine")
            return True, real

        register("image", _ok)
        j4 = new("image", "猫")
        run(j4)
        check("真产物 → done", j4["status"] == "done", j4)
        check("产物路径已记录", j4["artifacts"] == [real], j4["artifacts"])
        check("降级链首项被使用（engine=cloud）", seen.get("engine") == "cloud",
              seen)

        # --- 5. 降级链：第一个 engine 失败，第二个成功 ---
        calls = []

        def _fb(job, ctx):
            calls.append(ctx.get("engine"))
            if ctx.get("engine") == "cloud":
                return False, "云端 404"
            return True, real

        register("image", _fb)
        j5 = new("image", "猫")
        run(j5)
        check("降级链：cloud 失败后 local 接管", j5["status"] == "done"
              and calls == ["cloud", "local"], calls)
        check("成功引擎记为 local", j5["engine"] == "local", j5["engine"])

        # --- 6. 适配器抛异常 = 失败（不是"完成"） ---
        def _boom(job, ctx):
            raise RuntimeError("引擎崩了")

        register("image", _boom)
        j6 = new("image", "猫")
        run(j6)
        check("适配器抛异常 → failed", j6["status"] == "failed", j6["status"])
        check("异常原因写进 error", "引擎崩了" in (j6["error"] or ""), j6["error"])

        # --- 7. 落盘 / 查询 ---
        # 先量基线再比增量 —— 不要用 ">= 2" 这种断言：它在干净目录下（真值 2）
        # 会失败、在有历史残渣的目录下才通过，等于"靠资历通过"。
        n0 = len(all_jobs())
        save(j4)
        save(j5)
        check("落盘后可读回（比基线多 2 条）",
              len(all_jobs()) == n0 + 2, "%d -> %d" % (n0, len(all_jobs())))
        j7 = new("video", "推进", dir="营销/视频")
        save(j7)
        check("by_dir 按目录复盘", len(by_dir("营销/视频")) == 1,
              by_dir("营销/视频"))
        s = summary()
        check("summary 有统计（比基线多 3 条）",
              s["total"] == n0 + 3 and "done" in s["by_status"], s)

        # 反例（v0.30.5 修的真 bug）：id 曾在同一毫秒内重复 → save() 按 id 覆盖，
        # 批量出图（漫剧一次 8 格）会静默只剩最后一格。
        burst = [new("image", "分镜%d" % i)["id"] for i in range(12)]
        check("★同批创建的任务 id 两两不同（反例：id 撞车互相覆盖）",
              len(set(burst)) == len(burst), "%d 唯一 / %d 个" % (len(set(burst)),
                                                                 len(burst)))
        nb = len(all_jobs())
        for i, bid in enumerate(burst):
            bj = new("image", "分镜%d" % i)
            bj["id"] = bid
            save(bj)
        check("★批量保存 12 个任务后一条不少（反例：静默覆盖）",
              len(all_jobs()) == nb + 12, "%d -> %d" % (nb, len(all_jobs())))

        # --- 8. 未知 kind 兜底为 image（不抛） ---
        j8 = new("不存在的类型", "x")
        check("未知 kind 兜底为 image", j8["kind"] == "image", j8["kind"])

        # --- 9. 无 wid 时不碰 worklog（解耦） ---
        j9 = new("image", "猫")
        run_and_record(j9, link_worklog=True)
        check("无 wid 时不报错", j9["status"] in ("done", "failed", "skipped"))

        # --- 10. 内置适配器注册齐全 ---
        ADAPTERS.clear()
        install_defaults()
        miss = [k for k in VALID_KINDS if not adapter_for(k)]
        check("install_defaults 覆盖 5 个工种", not miss, miss)
    finally:
        ADAPTERS.clear()
        if saved is None:
            os.environ.pop("PASM_STUDIO_DIR", None)
        else:
            os.environ["PASM_STUDIO_DIR"] = saved
        shutil.rmtree(tmp, ignore_errors=True)

    print("-" * 46)
    if fails:
        print("自检失败 %d/%d 项：%s" % (len(fails), stats["n"], "；".join(fails)))
        return 1
    print("自检通过：%d 项全绿" % stats["n"])
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_selftest())
