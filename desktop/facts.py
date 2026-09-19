# -*- coding: utf-8 -*-
"""desktop/facts.py —— 再导出薄壳（v0.29.0）。

事实层（双时态 + 矛盾检测 + 时序自更新）的**唯一实现**位于
pasm.cognitive.facts。本文件仅作兼容再导出，保证桌面端可以
`import facts as FA` 而无需关心包路径。改动请直接编辑核心文件。
"""
import sys

from pasm.cognitive import facts as _impl

sys.modules[__name__] = _impl
