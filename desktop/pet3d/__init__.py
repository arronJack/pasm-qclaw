# -*- coding: utf-8 -*-
"""PASM 3D 小人引擎。

对外只需要认识这几个名字：

    from pet3d import Scene, available
    ok, info = available()          # 本机 GPU 3D 可用性
    sc = Scene(growth=2, skin="tech")
    sc.advance(dt, act="wave", mode="", expr="joy")
    img = sc.image(152)             # 带 alpha 的 QImage

设计要点（详见各子模块 docstring）：
  · 零新增依赖 —— 全部基于 PySide6 自带的 QtOpenGL 封装类，不引 PyOpenGL / numpy
  · 程序化几何 —— 形体由参数算出，因此能随成长档位连续变形，且不依赖外部模型资产
  · 离屏渲染 —— 输出带 alpha 的位图交给 QPainter 合成，桌面宠物的透明背景不受影响
  · 失败即降级 —— 任何环节不可用都只返回 None，由调用方回退到 2D 绘制
"""

from .scene import Scene, available, composite, shutdown  # noqa: F401
from .rig import PROFILES, profile_for  # noqa: F401
from .anim import acts  # noqa: F401

__all__ = ["Scene", "available", "composite", "shutdown",
           "PROFILES", "profile_for", "acts"]
