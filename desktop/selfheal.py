# -*- coding: utf-8 -*-
"""desktop/selfheal.py —— 再导出薄壳（v0.29.0）。

自检/自修复的**唯一实现**位于 pasm.cognitive.selfheal（核心包）。
本文件仅作兼容再导出，保证 `import selfheal` 的既有引用与冒烟脚本不变。

v0.29.0 修正：原先用"属性拷贝"式再导出，会把模块级可变状态（DATA_DIR /
日志路径等）在导入时冻成一份**陈旧副本**——核心侧一改目录，壳这边还指着
老路径。改用 sys.modules 别名后拿到的就是核心模块本体。
改动请编辑 pasm/cognitive/selfheal.py。
"""
import sys

import pasm.cognitive.selfheal as _impl

sys.modules[__name__] = _impl
