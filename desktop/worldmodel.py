# -*- coding: utf-8 -*-
"""desktop/worldmodel.py —— 再导出薄壳（v0.29.0）。

符号世界模型（W1 动作后果表 + W2 展开预演）的**唯一实现**位于
pasm.cognitive.worldmodel。本文件仅作兼容再导出，保证桌面端可以
`import worldmodel as WM` 而无需关心包路径。改动请直接编辑核心文件。
"""
import sys

from pasm.cognitive import worldmodel as _impl

sys.modules[__name__] = _impl
