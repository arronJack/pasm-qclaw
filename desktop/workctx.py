# -*- coding: utf-8 -*-
"""desktop/workctx.py —— 再导出薄壳（v0.29.0）。

工作上下文（最近读过的文件/要点）的**唯一实现**位于
pasm.cognitive.workctx（核心包）。本文件仅作兼容再导出。

v0.29.0 修正：原属性拷贝式再导出会让模块级可变状态变成陈旧副本
（见 desktop/agent_team.py 的说明），改用 sys.modules 别名。
改动请编辑 pasm/cognitive/workctx.py。
"""
import sys

import pasm.cognitive.workctx as _impl

sys.modules[__name__] = _impl
