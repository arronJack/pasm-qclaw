# -*- coding: utf-8 -*-
"""模拟 frozen 包环境：pasm.agent / pasm.config / pasm.envs 被 PyInstaller excludes 掉。

用途：打包产物的冒烟验证——源码环境里 torch 可用、走的是 `pasm` 完整引擎，
而安装包不含 torch、实际走的是降级路径。这条路径必须在打包前验证到，
否则"桌面端打开就崩"会在用户机器上才暴露。
"""
import os
import sys

# 自动定位仓库根：向上找 pyproject.toml，**绝不写死绝对路径**。
# 2026-09-15 实际踩到：这里原本写死旧仓库的绝对路径，目录迁移后
# `code/PASM` 之后脚本就静默指向一个不存在的路径 —— 打包前冒烟会直接失败，
# 而失败原因看起来像"引擎不可用"，很容易误判成产品缺陷。
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = _HERE
while True:
    if os.path.isfile(os.path.join(_ROOT, "pyproject.toml")):
        break
    _up = os.path.dirname(_ROOT)
    if _up == _ROOT:
        _ROOT = _HERE
        break
    _ROOT = _up
sys.path.insert(0, _ROOT)
sys.path.insert(0, _HERE)


class _Blocker:
    """真实模拟"模块不存在"：连 find_spec 都返回 None。"""
    BLOCK = {"pasm.agent", "pasm.config", "pasm.envs"}

    def find_spec(self, name, path=None, target=None):
        if name in self.BLOCK:
            raise ModuleNotFoundError("No module named %r (frozen)" % name)
        return None


sys.meta_path.insert(0, _Blocker())
for _m in ("pasm.agent", "pasm.config", "pasm.envs"):
    sys.modules.pop(_m, None)

import engine_factory as EF  # noqa: E402

print("=" * 66)
print("① frozen 模拟下引擎可用性")
print("   full_available():", EF.full_available())
print("   summary():", EF.summary())

print("\n② 创建引擎（应降级到桌面轻量体）")
eng = EF.make_engine(personality_seed=[0.8, -0.2, 0.6], seed=1)
print("   引擎类型:", type(eng).__name__)
print("   选择报告:", EF.LAST_REPORT)

print("\n③ 数学脑（mathlab / numpy）验收 —— 打包清单漏了 numpy 会让它变成死功能")
# v0.30.8 全生态核查发现：spec 的 _PASM_HEAVY 原本把 numpy 跟 torch 一起排掉了，
# 而 `pasm/cognitive/mathlab.py` 自己 import numpy（polyfit / lstsq / corrcoef）。
# 后果：桌面侧 `MLAB = None` → 表格分析意图不路由、真走到只回"重启再试试"。
# 这一段就是防它再犯：**打包前后都必须看到 numpy 与 mathlab 都可用**。
try:
    import numpy as _np
    print("   numpy:", _np.__version__)
    _np_ok = True
except Exception as _ex:                       # noqa: BLE001
    print("   ✗ numpy 不可用:", _ex)
    _np_ok = False
try:
    from pasm.cognitive import mathlab as _ML
    _res = _ML.analyze_table(["月份", "销量"],
                             [[1, 100], [2, 120], [3, 140], [4, 160], [5, 180]])
    _k = sorted(_res) if isinstance(_res, dict) else type(_res).__name__
    print("   mathlab 可用 | 分析结果键:", _k)
    print("   结论:", str(_res)[:160])
    _ml_ok = True
except Exception as _ex:                       # noqa: BLE001
    print("   ✗ mathlab 不可用:", _ex)
    _ml_ok = False
print("   数学脑判定：", "✓ 可用于产品" if (_np_ok and _ml_ok)
      else "✗ 打包会变成死功能（检查 spec 的 _PASM_HEAVY 是否又排掉了 numpy）")

print("\n④ 契约校验 + 冒烟")
good, probs = EF.EA.conforms(eng, strict=True)
print("   契约校验:", good, probs or "无问题")
eng.reset_episode()
a, rep = eng.act([0.0] * 27)
print("   act() ->", a, "| report 键:", sorted(rep)[:6])
snap = eng.snapshot()
print("   snapshot 区块:", sorted(snap)[:8])

print("\n⑤ 创建环境（pasm.envs 不可用 -> 应回落 pasm_light）")
env = EF.make_env(seed=1)
print("   环境类型:", type(env).__name__,
      "| 有 reset/step:", hasattr(env, "reset"), hasattr(env, "step"))

print("\n⑥ 自检")
print("   selftest:", EF.selftest())
