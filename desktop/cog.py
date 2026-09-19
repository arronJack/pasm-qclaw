# -*- coding: utf-8 -*-
"""desktop/cog.py —— 再导出薄壳（v0.29.0）。

认知皮层（会话级认知状态机）的**唯一实现**位于 pasm.cognitive.cog。
本文件仅作兼容再导出，保证 `import cog as COG` 的既有引用不变。

历史背景：本文件曾是核心文件的**字节级重复副本**——虽然当时内容一致，
但这正是"分叉的温床"（memory_layers 就是这样分叉出去的）。改成薄壳后
物理上不可能再分叉。改动请直接编辑 pasm.cognitive.cog。
"""
import sys

from pasm.cognitive import cog as _impl

sys.modules[__name__] = _impl
