# -*- coding: utf-8 -*-
"""desktop/memory_layers.py —— 再导出薄壳（v0.29.0）。

分层记忆的**唯一实现**位于 pasm.cognitive.memory_layers（核心包）。
本文件仅作兼容再导出，保证 `import memory_layers as ML` 的既有引用不变。

为什么用 sys.modules 别名而不是拷贝属性：本模块有**模块级可变状态**
（DATA_DIR / EPI_FILE / PROC_FILE / WORK_FILE / SEM_FILE / 写盘去抖队列），
`set_data_dir()` 会重绑这些全局量。若用属性拷贝，壳里的 DATA_DIR 会变成
陈旧副本，导致"换了记忆目录但读不到"这类极度隐蔽的 bug。别名让
`ML` 就是核心模块本体，全局态永远一致。

历史背景：本文件曾与核心版**双向分叉**（核心只有重要度淘汰，桌面只有
遗忘曲线/巩固层），结果桌面端长期在犯核心已修复的"里程碑记忆被日常琐事
挤掉"的 bug。改动请只在这里指向的核心文件里做。
"""
import sys

from pasm.cognitive import memory_layers as _impl

sys.modules[__name__] = _impl
