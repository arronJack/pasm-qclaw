# -*- coding: utf-8 -*-
"""desktop/agent_team.py —— 再导出薄壳（v0.29.0）。

多智能体协作层的**唯一实现**位于 pasm.cognitive.agent_team（核心包）。
本文件仅作兼容再导出，保证 `import agent_team` 的既有引用不变。

v0.29.0 修正：原先用"属性拷贝"式再导出，会把模块级可变状态（DATA_DIR /
文件路径等）在导入时冻成一份**陈旧副本**——核心侧 `set_data_dir()` 一改，
壳这边还指着老目录，属于极隐蔽的"半失忆"bug。改用 sys.modules 别名后
`import agent_team` 拿到的就是核心模块本体，状态永远一致。
"""
import sys

import pasm.cognitive.agent_team as _impl

sys.modules[__name__] = _impl
