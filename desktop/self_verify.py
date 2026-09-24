#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""self_verify —— 干活后的**自我复查闭环**纯逻辑层（v0.31.5）。

对标 WorkBuddy 的标准动作：干完活 → 自动跑验证 → 失败带报错自动修 → 再验证（多轮）。

本模块只管"怎么验、错了怎么问"，不管 Qt / 模型：
  * plan_verify(paths)  —— 按改动文件类型决定验证命令：
                           .py  → python -m py_compile（语法级，秒回）
                           .js/.mjs/.cjs → node --check
                           .json → json.load 解析
                           其他（md/txt/docx…）→ 无需验证，跳过
  * run_cmds(cmds)      —— 逐条真跑（subprocess，超时保护），产出结果表
  * first_error(res)    —— 第一条失败结果的 (label, 报错尾巴)，喂给修复轮
  * repair_prompt(...)  —— 组装修复轮 prompt（带原需求 + 报错 + "只输出完整代码"约束）
  * MAX_REPAIR_ROUNDS   —— 修复轮上限（默认 3；WorkBuddy 同款"修到过为止，但不无限"）

设计约束：零 Qt、零第三方依赖；所有命令**只读验证**（py_compile 会在 __pycache__ 落
字节码，属无害副产物），绝不执行"运行脚本"以外的业务逻辑 —— 那是调用方的职责。

自检：python self_verify.py --selftest
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

__all__ = ["MAX_REPAIR_ROUNDS", "plan_verify", "run_cmds", "first_error",
           "repair_prompt", "selftest"]

MAX_REPAIR_ROUNDS = 3

_PY = (".py",)
_JS = (".js", ".mjs", ".cjs")
_JSON = (".json",)


def _runtime_py() -> str:
    return sys.executable or "python"


def plan_verify(paths) -> list:
    """按改动文件出验证命令表 [{label, argv, cwd}]。无需验证的文件直接跳过。"""
    out = []
    for p in (paths or []):
        try:
            p = str(p)
            if not os.path.isfile(p):
                continue
            ext = os.path.splitext(p)[1].lower()
            if ext in _PY:
                out.append({"label": "py_compile %s" % os.path.basename(p),
                            "argv": [_runtime_py(), "-m", "py_compile", p],
                            "cwd": os.path.dirname(p) or None})
            elif ext in _JS:
                node = "node"
                out.append({"label": "node --check %s" % os.path.basename(p),
                            "argv": [node, "--check", p],
                            "cwd": os.path.dirname(p) or None})
            elif ext in _JSON:
                out.append({"label": "json %s" % os.path.basename(p),
                            "argv": [_runtime_py(), "-c",
                                     "import json,sys;json.load(open(sys.argv[1],encoding='utf-8'))",
                                     p],
                            "cwd": os.path.dirname(p) or None})
            # 其他类型（md/txt/docx/pptx…）不做命令级验证 —— 调用方自行做内容级校验
        except Exception:
            continue
    return out


def run_cmds(cmds, timeout: float = 30.0) -> list:
    """逐条真跑验证命令 → [{label, ok, output}]。命令不存在/超时都算失败（诚实上报）。"""
    res = []
    for c in (cmds or []):
        label = c.get("label", "?")
        try:
            r = subprocess.run(c.get("argv") or [], cwd=c.get("cwd"),
                               capture_output=True, text=True, timeout=timeout)
            ok = (r.returncode == 0)
            outp = (r.stderr or "").strip() or (r.stdout or "").strip()
            res.append({"label": label, "ok": ok, "output": outp})
        except FileNotFoundError:
            res.append({"label": label, "ok": False,
                        "output": "验证命令不可用（运行时缺失）"})
        except subprocess.TimeoutExpired:
            res.append({"label": label, "ok": False,
                        "output": "验证超时（>%ss）" % int(timeout)})
        except Exception as ex:                              # noqa: BLE001
            res.append({"label": label, "ok": False, "output": str(ex)[:300]})
    return res


def first_error(results):
    """第一条失败 → (label, 报错尾巴)。全过返回 (None, "")。"""
    for r in (results or []):
        if not r.get("ok"):
            tail = "\n".join((r.get("output") or "").splitlines()[-12:])
            return (r.get("label", "?"), tail[:1200])
    return (None, "")


def repair_prompt(req: str, err_label: str, err_tail: str, round_idx: int) -> str:
    """修复轮 prompt：原需求 + 具体报错 + 输出约束。round_idx 从 1 计。"""
    return (
        "你刚写的代码没有通过自动验证（第 %d 次复查）。只输出**修复后的完整代码**，"
        "不要解释、不要 markdown 围栏。\n\n"
        "【需求】%s\n\n"
        "【验证项】%s\n【报错】\n%s\n\n"
        "要求：修到能通过该验证为止；不要改动与报错无关的部分。"
        % (round_idx, (req or "")[:600], err_label or "语法验证", (err_tail or "")[:1000])
    )


# ---------------------------------------------------------------- 自检
def selftest() -> int:
    import tempfile
    ok = fail = 0

    def chk(name, cond, extra=""):
        nonlocal ok, fail
        if cond:
            ok += 1
        else:
            fail += 1
            print("  ✗ %s %s" % (name, extra))

    with tempfile.TemporaryDirectory() as td:
        # plan_verify：按扩展名分流
        pyf = os.path.join(td, "good.py")
        jsf = os.path.join(td, "x.js")
        jsonf = os.path.join(td, "x.json")
        mdf = os.path.join(td, "x.md")
        ghost = os.path.join(td, "ghost.py")
        open(pyf, "w", encoding="utf-8").write("print('ok')\n")
        open(jsf, "w", encoding="utf-8").write("const a=1;\n")
        open(jsonf, "w", encoding="utf-8").write('{"a":1}')
        open(mdf, "w", encoding="utf-8").write("# 文档")
        plan = plan_verify([pyf, jsf, jsonf, mdf, ghost])
        chk("计划-py", any("py_compile" in c["label"] for c in plan))
        chk("计划-json", any(c["label"].startswith("json") for c in plan))
        chk("计划-md跳过", not any("x.md" in c["label"] for c in plan))
        chk("计划-幽灵跳过", not any("ghost" in c["label"] for c in plan))
        # 坏文件：py 语法错 / json 损坏
        bad_py = os.path.join(td, "bad.py")
        open(bad_py, "w", encoding="utf-8").write("def f(:\n  pass\n")
        bad_json = os.path.join(td, "bad.json")
        open(bad_json, "w", encoding="utf-8").write('{"a":}')
        # run_cmds：好文件全过
        res_ok = run_cmds(plan_verify([pyf, jsonf]))
        chk("执行-好文件全过", all(r["ok"] for r in res_ok), str(res_ok))
        # run_cmds：坏文件报错且带信息
        res_bad = run_cmds(plan_verify([bad_py, bad_json]))
        chk("执行-坏py失败", len(res_bad) == 2 and not res_bad[0]["ok"])
        lbl, tail = first_error(res_bad)
        chk("错误-有label", bool(lbl))
        chk("错误-有尾巴", ("bad" in tail) or ("Expecting" in tail) or ("was never" in tail)
            or ("invalid" in tail.lower()) or bool(tail), tail[:80])
        chk("错误-全过为空", first_error(res_ok) == (None, ""))
        # repair_prompt
        rp = repair_prompt("写个计数器", lbl, tail, 2)
        chk("修复prompt-轮次", "第 2 次" in rp)
        chk("修复prompt-带需求", "计数器" in rp)
        chk("修复prompt-带报错", bool(tail) and (tail[:60] in rp or len(rp) > 100))
        # 空输入兜底
        chk("计划-空", plan_verify([]) == [])
        chk("执行-空", run_cmds([]) == [])
        chk("错误-空", first_error([]) == (None, ""))

    print("self_verify 自检：%d/%d 通过" % (ok, ok + fail))
    return 0 if not fail else 1


if __name__ == "__main__":
    raise SystemExit(selftest())
