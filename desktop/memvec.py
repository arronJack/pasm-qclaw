# -*- coding: utf-8 -*-
"""desktop/memvec.py —— 再导出薄壳（v0.28.5）。

轻量向量检索的**唯一实现**位于 pasm.cognitive.memvec（核心包）。
本文件仅作兼容再导出。改动请直接编辑 pasm.cognitive.memvec。
"""
from pasm.cognitive import memvec as _mv
import sys
sys.modules[__name__] = _mv
