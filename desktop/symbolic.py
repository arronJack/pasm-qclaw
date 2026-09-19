# -*- coding: utf-8 -*-
"""desktop/symbolic.py —— 再导出薄壳（v0.28.5）。

符号推理层的**唯一实现**位于 pasm.cognitive.symbolic（核心包）。
本文件仅作兼容再导出，保证旧 import 路径（import symbolic as SY）不变。
改动请直接编辑 pasm/cognitive/symbolic.py。
"""
from pasm.cognitive import symbolic as _sym
import sys
sys.modules[__name__] = _sym
