# -*- coding: utf-8 -*-
"""desktop/workspace.py —— 再导出薄壳（v0.30.11）。

统一工作根与产物分类的**唯一实现**位于 `pasm.cognitive.workspace`。
本文件仅作兼容再导出，保证 `import workspace` 的既有引用不变。

为什么用 `sys.modules` 别名而不是属性拷贝：属性拷贝会把模块级可变状态在
导入时冻成一份**陈旧副本**（`agent_team.py` 上真踩过这个"半失忆"事故）。

sys.path 引导是为了让 `cd desktop && python -m <模块>` 这种单独跑法也能找到
核心包（应用内由 `pasm_companion` 自己插好了仓库根路径）。
改动请编辑 `pasm/cognitive/workspace.py`。
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import pasm.cognitive.workspace as _impl      # noqa: E402

sys.modules[__name__] = _impl
