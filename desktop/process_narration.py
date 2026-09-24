#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""process_narration —— 聊天栏「过程卡」的 WorkBuddy 式叙述层（v0.31.4）。

对标 WorkBuddy 的过程展示：
    已处理 2m36s
    ├─ 正在执行命令 …
    ├─ 编辑 E:\ai\xxx.py
    └─ 生成回复中 …

PASM Studio 侧对应实现（本模块只管"文字怎么长"，不管 Qt）：
  * fmt_elapsed()   —— 秒数 → "36s" / "2m36s" / "1h02m30s"（WorkBuddy 同款口径）
  * header_suffix() —— 过程卡头部后缀：干活中「⏳ 处理中 36s」，收口「✓ 已处理 2m36s」
  * narrate()       —— 步骤 kind → 自然语言叙述句（"正在执行命令"而非裸工具名）
  * step_suffix()   —— 每步尾部灰字「· 3s」（这步花了多久）

设计约束：
  * 零 Qt、零第三方依赖 —— 可被 pasm_companion 直接 import，也可独立自检；
  * 所有输出都是"纯文本片段"，HTML 转义由调用方（pasm_companion）负责；
  * 头部文本必须保留调用方的卡片标识前缀（"过程 · N 步"），本模块只产**后缀**。

自检：python process_narration.py --selftest
"""
from __future__ import annotations

import time

__all__ = ["fmt_elapsed", "header_suffix", "narrate", "step_suffix", "selftest"]


def fmt_elapsed(sec: float) -> str:
    """秒数 → 人读时长。WorkBuddy 口径：<60s 显示 "36s"，以上 "2m36s"，再以上 "1h02m30s"。"""
    try:
        s = int(max(0, round(float(sec))))
    except Exception:
        return "0s"
    if s < 60:
        return "%ds" % s
    m, sec2 = divmod(s, 60)
    if m < 60:
        return "%dm%02ds" % (m, sec2)
    h, m2 = divmod(m, 60)
    return "%dh%02dm%02ds" % (h, m2, sec2)


def header_suffix(elapsed: float, running: bool, err: bool = False) -> str:
    """过程卡头部后缀（跟在"过程 · N 步"后面）。

    running=True  → "⏳ 处理中 36s"（干活期间由定时器每秒刷新，实时跳动）
    running=False → "✓ 已处理 2m36s"（收口定稿）；err=True 时用 "✕ 处理 2m36s 后中止"
    """
    if running:
        return "⏳ 处理中 %s" % fmt_elapsed(elapsed)
    return ("✕ 处理 %s 后中止" if err else "✓ 已处理 %s") % fmt_elapsed(elapsed)


#: 步骤 kind → 自然语言叙述句（WorkBuddy 式："正在执行命令"而不是裸工具名）
_NARRATIONS = {
    "think": "深度思考",
    "plan":  "规划步骤",
    "cmd":   "执行命令",
    "new":   "新建文件",
    "edit":  "修改文件",
    "write": "写入文件",
    "read":  "读取文件",
    "readfolder": "读取文件夹",
    "openpath":   "打开路径",
    "gen":   "生成回复",
    "ok":    "完成",
    "err":   "出错",
}


def narrate(kind: str, title: str = "") -> str:
    """kind → 叙述句。优先用映射；映射没有就退回原标题；标题为空补"处理中"。"""
    verb = _NARRATIONS.get(str(kind or ""))
    if not verb:
        verb = str(title or "").strip() or "处理中"
    return verb


def step_suffix(t0: float, t: float) -> str:
    """步骤尾部灰字：这步距本轮开工花了多久 → "· 3s"。无有效时间则空串。"""
    try:
        d = float(t) - float(t0)
        if d < 0:
            return ""
        return "· %s" % fmt_elapsed(d)
    except Exception:
        return ""


# ---------------------------------------------------------------- 自检
def selftest() -> int:
    ok = fail = 0

    def chk(name, cond, extra=""):
        nonlocal ok, fail
        if cond:
            ok += 1
        else:
            fail += 1
            print("  ✗ %s %s" % (name, extra))

    # fmt_elapsed
    chk("fmt 0", fmt_elapsed(0) == "0s")
    chk("fmt 36", fmt_elapsed(36) == "36s")
    chk("fmt 59.6", fmt_elapsed(59.6) == "1m00s")
    chk("fmt 156", fmt_elapsed(156) == "2m36s")
    chk("fmt 3750", fmt_elapsed(3750) == "1h02m30s")
    chk("fmt 坏值", fmt_elapsed(None) == "0s" or fmt_elapsed("x") == "0s")
    # header_suffix
    chk("头-进行中", "处理中" in header_suffix(36, True) and "36s" in header_suffix(36, True))
    chk("头-收口", header_suffix(156, False).startswith("✓ 已处理 2m36s"))
    chk("头-中止", header_suffix(156, False, err=True).startswith("✕"))
    # narrate
    chk("叙述-cmd", narrate("cmd") == "执行命令")
    chk("叙述-read", narrate("read") == "读取文件")
    chk("叙述-gen", narrate("gen") == "生成回复")
    chk("叙述-未知回落标题", narrate("mystery", "开工") == "开工")
    chk("叙述-全空兜底", narrate("", "") == "处理中")
    # step_suffix
    chk("步尾", step_suffix(100.0, 103.4) == "· 3s")
    chk("步尾-负值", step_suffix(100.0, 99.0) == "")
    chk("步尾-坏值", step_suffix("a", None) == "")

    print("process_narration 自检：%d/%d 通过" % (ok, ok + fail))
    return 0 if not fail else 1


if __name__ == "__main__":
    import sys
    t0 = time.time()
    raise SystemExit(selftest())
