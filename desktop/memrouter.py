# -*- coding: utf-8 -*-
"""desktop/memrouter.py —— 再导出薄壳（v0.28.5）。

记忆路由器的**唯一实现**位于 pasm.cognitive.memrouter（核心包）。
本文件仅作兼容再导出，保证旧 import 路径（import memrouter as MR）不变。
改动请直接编辑 pasm.cognitive.memrouter。
"""
from pasm.cognitive import memrouter as _mr
import sys
sys.modules[__name__] = _mr
