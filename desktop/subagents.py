# -*- coding: utf-8 -*-
"""subagents.py —— 真并行的子智能体（P1-4）。

真问题：全仓此前**零** `ThreadPoolExecutor`/`concurrent.futures`/`multiprocessing` ——
所谓"团队/多智能体"只是串行角色扮演：一个个说台词，谁也没在同时干活。

本模块给三样能真正提升效率/质量的东西（都**真并行**，且各自上下文独立、互不污染）：

1. `run_parallel()` —— 通用并行执行器（保序、限并发、单任务超时、异常隔离）。
   一次任务炸掉不影响其它任务，结果里如实带 `error`。
2. `cross_check()` —— **交叉验证**：同一个问题让两个"脑子"（本地 + 云端，或同模型两次）
   各自独立回答，再由第三个"裁判"调用来判定/合并。这是"多智能体"里**真能提升正确率**
   的那种用法（互相纠错），而不是让它演剧本。
3. `fanout_research()` —— 只读子任务并行（检索/阅读/分析），最后汇总。

安全边界：并行**只用于只读**（读文件/检索/静态检查/多个模型的独立推理）。
写盘、删改、执行命令一律**串行**由主链路负责 —— 并行写同一目录必然冲突。
"""
from __future__ import annotations

import concurrent.futures as _fut
import time

#: 并发上限：本机是桌面应用，别把机器压死（4 是实测比较稳的值）
DEFAULT_WORKERS = 4


def run_parallel(jobs: list, *, max_workers: int = DEFAULT_WORKERS,
                 timeout: float = 120.0, on_result=None) -> list:
    """并行执行 jobs（`[{"id":..., "fn": callable}]`），**保序返回**结果。

    每个结果：`{"id", "ok", "value"|"error", "elapsed"}`。
    单任务异常/超时都被隔离：其余任务照常完成（旧写法一个炸全炸）。
    """
    if not jobs:
        return []
    out = [None] * len(jobs)
    workers = max(1, min(int(max_workers or DEFAULT_WORKERS), 8))

    def _one(idx, job):
        t0 = time.time()
        try:
            v = job["fn"]()
            return idx, {"id": job.get("id", idx), "ok": True, "value": v,
                         "elapsed": time.time() - t0}
        except Exception as ex:                                  # noqa: BLE001
            return idx, {"id": job.get("id", idx), "ok": False,
                         "error": "%s: %s" % (type(ex).__name__, str(ex)[:200]),
                         "elapsed": time.time() - t0}

    with _fut.ThreadPoolExecutor(max_workers=workers) as pool:
        futs = [pool.submit(_one, i, j) for i, j in enumerate(jobs)]
        for f in _fut.as_completed(futs, timeout=max(5.0, float(timeout))):
            try:
                idx, res = f.result(timeout=1)
            except Exception as ex:                              # noqa: BLE001
                continue
            out[idx] = res
            if on_result:
                try:
                    on_result(res)
                except Exception:                                # noqa: BLE001
                    pass
    for i, r in enumerate(out):                                  # 超时/未回的补空
        if r is None:
            out[i] = {"id": jobs[i].get("id", i), "ok": False,
                      "error": "超时 %.0fs 未返回" % timeout, "elapsed": float(timeout)}
    return out


def parallel_static_check(paths: list, checker, *, max_workers: int = DEFAULT_WORKERS,
                          timeout: float = 90.0) -> list:
    """并行跑静态检查（`checker(path) -> (ok, why)`）→ 失败项列表 [(path, why)]。

    只读、无副作用 —— 项目文件一多（几十个）时，串行检查会明显拖慢每一步验证。
    """
    jobs = [{"id": p, "fn": (lambda p=p: checker(p))} for p in paths]
    bad = []
    for r in run_parallel(jobs, max_workers=max_workers, timeout=timeout):
        if not r["ok"]:
            bad.append((r["id"], r.get("error") or "检查器异常"))
            continue
        v = r.get("value")
        if isinstance(v, tuple) and v and (v[0] is False):
            bad.append((r["id"], (v[1] if len(v) > 1 else "")))
    return bad


def cross_check(call_llm, question: str, *, task: str = "brain", n: int = 2,
                styles: tuple = ("precise", "creative"), max_tokens: int = 1200) -> dict:
    """交叉验证：n 个**独立**回答 + 一个裁判合并。

    `call_llm(prompt, prefer=...)` —— prefer 用于强制走本地/云端（两个脑子）。
    返回 {"answers":[...], "verdict": {...}, "merged": str, "elapsed": s}。
    """
    prefs = ("local", "cloud")[:max(1, min(n, 2))]
    jobs = []
    for i in range(n):
        style = styles[i % len(styles)]
        p = ("请%s地回答下面的问题。给出结论 + 关键依据；如不确定就明说不确定。\n\n%s"
             % ("严谨" if style == "precise" else "换一个角度", question))
        pref = prefs[i] if prefs else None
        jobs.append({"id": "a%d" % i,
                     "fn": (lambda p=p, pref=pref: call_llm(p, prefer=pref,
                                                            task=task,
                                                            max_tokens=max_tokens))})
    t0 = time.time()
    res = run_parallel(jobs, max_workers=min(2, n), timeout=300.0)
    answers = [r.get("value") or ("（失败：%s）" % r.get("error"))
               for r in res]
    judge_prompt = ("下面是 %d 个独立回答（可能有错）。请**只输出**：\n"
                    "① 一致结论 ② 分歧点 ③ 你判断哪个更可信及理由 ④ 最终答案。\n\n"
                    % len(answers)
                    + "\n\n".join("【回答%d】\n%s" % (i + 1, a[:2500])
                                  for i, a in enumerate(answers)))
    try:
        merged = call_llm(judge_prompt, task="brain", max_tokens=1200) or ""
    except Exception as ex:                                      # noqa: BLE001
        merged = "（裁判调用失败：%s）" % ex
    return {"answers": answers, "merged": merged,
            "agree": _rough_agree(answers), "elapsed": time.time() - t0}


def _rough_agree(answers: list) -> bool:
    """粗判是否一致（交集词比例）—— 只用于提示"要不要人工看一眼"。"""
    if len(answers) < 2:
        return True
    sets = []
    for a in answers:
        ws = set(w for w in (a or "")[:1500].split() if len(w) > 1)
        sets.append(ws)
    try:
        inter = set.intersection(*sets) if sets else set()
        union = set.union(*sets) if sets else set()
        return (len(inter) / max(1, len(union))) > 0.35
    except Exception:                                            # noqa: BLE001
        return True


def fanout_research(call_llm, topic: str, subquestions: list, *, task: str = "study",
                    max_workers: int = 3, per_max_tokens: int = 900) -> dict:
    """把一个大问题拆成若干**只读子问题**并行问，再汇总（不写盘、不改状态）。"""
    subs = [s for s in (subquestions or []) if str(s).strip()][:6]
    if not subs:
        return {"parts": [], "summary": "（没有可并行的子问题）", "elapsed": 0.0}
    jobs = [{"id": s[:40],
             "fn": (lambda s=s: call_llm(
                 "只回答这一个子问题，给要点与依据，200 字内：\n%s\n\n背景：%s" % (s, topic[:300]),
                 task=task, max_tokens=per_max_tokens))} for s in subs]
    t0 = time.time()
    res = run_parallel(jobs, max_workers=max_workers, timeout=300.0)
    parts = [{"q": r["id"], "a": r.get("value") or ("（失败：%s）" % r.get("error"))}
             for r in res]
    summary_prompt = ("把下面这些子问题的答案汇总成一份**条理清晰**的答复（不要罗列过程）：\n\n"
                      + "\n\n".join("Q: %s\nA: %s" % (p["q"], p["a"][:1200]) for p in parts))
    try:
        summary = call_llm(summary_prompt, task="brain", max_tokens=1500) or ""
    except Exception as ex:                                      # noqa: BLE001
        summary = "（汇总失败：%s）" % ex
    return {"parts": parts, "summary": summary, "elapsed": time.time() - t0}


# --------------------------------------------------------------------------- selftest
def selftest() -> int:
    import tempfile
    import time as _t
    P = F = 0

    def ck(name, ok, detail=""):
        nonlocal P, F
        if ok:
            P += 1
        else:
            F += 1
            print("  [FAIL] %s   %s" % (name, detail))

    print("=== ① 真并行（串行会明显更慢）===")
    t0 = _t.time()
    res = run_parallel([{"id": i, "fn": (lambda: (_t.sleep(0.3), i)[1])} for i in range(4)],
                       max_workers=4)
    el = _t.time() - t0
    ck("4 个 0.3s 任务并行 < 0.8s（串行需 1.2s）", el < 0.8, "elapsed=%.2fs" % el)
    ck("保序返回", [r["id"] for r in res] == [0, 1, 2, 3], [r["id"] for r in res])
    ck("全部成功", all(r["ok"] for r in res))

    print("\n=== ② 异常隔离：一个炸不影响其它 ===")
    def boom():
        raise ValueError("炸了")
    res = run_parallel([{"id": "ok", "fn": (lambda: 1)}, {"id": "bad", "fn": boom}],
                       max_workers=2)
    ck("好任务仍成功", res[0]["ok"] and res[0]["value"] == 1)
    ck("坏任务如实报错", not res[1]["ok"] and "ValueError" in res[1]["error"], res[1])

    print("\n=== ③ 静态检查并行 ===")
    tmp = tempfile.mkdtemp(prefix="subagents_")
    import os
    for i in range(6):
        open(os.path.join(tmp, "f%d.py" % i), "w").write("x=1\n")
    bad_file = os.path.join(tmp, "bad.py")
    open(bad_file, "w").write("def f(:\n")

    def checker(p):
        import py_compile
        try:
            py_compile.compile(p, doraise=True)
            return True, ""
        except Exception as ex:                                  # noqa: BLE001
            return False, os.path.basename(p)

    paths = [os.path.join(tmp, f) for f in os.listdir(tmp)]
    bad = parallel_static_check(paths, checker, max_workers=4)
    ck("并行检查抓出坏文件", len(bad) == 1 and "bad.py" in bad[0][0], bad)

    print("\n=== ④ 交叉验证（两个脑子 + 裁判）===")
    seen = []

    def fake_llm(prompt, prefer=None, task="brain", max_tokens=1200):
        seen.append(prefer)
        if "独立回答" in prompt:                                  # 裁判轮
            return "① 一致：X ② 分歧：无 ③ 更可信：两者一致 ④ 最终：X"
        return "我的答案是 X（%s）" % (prefer or "auto")

    cc = cross_check(fake_llm, "地球是圆的吗", n=2)
    ck("两个独立回答都拿到了", len(cc["answers"]) == 2, cc["answers"])
    ck("确实用了两个不同的脑子（local+cloud）（裁判走 auto）",
      {"local", "cloud"} <= set(seen), seen)
    ck("裁判给出最终答案", "最终" in cc["merged"], cc["merged"][:60])
    ck("一致度粗判可用", isinstance(cc["agree"], bool))

    print("\n=== ⑤ 只读并行检索汇总 ===")
    fr = fanout_research(fake_llm, "PASM 是什么", ["它解决什么问题", "它的核心模块有哪些"])
    ck("子问题都答了", len(fr["parts"]) == 2, fr["parts"])
    ck("有汇总", bool(fr["summary"]))

    print("PASS=%d FAIL=%d" % (P, F))
    print("RESULT: %s" % ("PASS" if F == 0 else "FAILED"))
    return 0 if F == 0 else 1


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "selftest":
        raise SystemExit(selftest())
    print(__doc__)
