"""growth —— 统一成长模型（v0.13）。

问题背景：聊天窗用 PASM 引擎内部的 development stage，桌面小人用
pet_state.json 的经验值，两套数据源各自增长、互不同步 → 出现
"聊天框婴儿期、桌面青年期" 的精分现场。

现在：经验值（exp）与阶段（STAGES/THRESH）唯一定义在这里，
聊天窗、桌面小人、语音、系统提示词全部引用这一份。
"""
from __future__ import annotations

import json
import os

import logsetup

# 与 logsetup.data_dir() 同一来源（项目铁律：数据目录单一来源，勿各自实现）。
# 关键修复：原写法用 `os.environ.get("APPDATA") or ~`，当 APPDATA 取不到时静默退回主目录，
# 导致"双击启动写 AppData、其他启动方式写 ~"的数据分裂；现统一走 logsetup 的
# Win32 权威解析（环境变量仅作兜底）。
DATA_DIR = logsetup.data_dir()
PET_FILE = os.path.join(DATA_DIR, "pet_state.json")

# 与旧版 pet_state.json 阈值完全兼容（老用户不会"降级"）
STAGES = ["幼儿期", "童年期", "少年期", "青年期", "成年期"]
THRESH = [0, 30, 90, 200, 400]


def pet_state() -> dict:
    try:
        if os.path.exists(PET_FILE):
            return json.load(open(PET_FILE, "r", encoding="utf-8"))
    except Exception:
        pass
    return {"exp": 0}


def save_state(state: dict):
    try:
        json.dump(state, open(PET_FILE, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
    except Exception:
        pass


def level_of(exp) -> int:
    try:
        exp = int(exp)
    except Exception:
        exp = 0
    lv = 0
    for i, t in enumerate(THRESH):
        if exp >= t:
            lv = i
    return lv


def current_growth() -> int:
    return level_of(pet_state().get("exp", 0))


def stage_name(exp=None) -> str:
    if exp is None:
        exp = pet_state().get("exp", 0)
    return STAGES[level_of(exp)]


def add_exp(v: int) -> tuple:
    """加经验，返回 (新exp, 旧阶段, 新阶段)。供各处统一加经验。"""
    st = pet_state()
    old = level_of(st.get("exp", 0))
    exp = int(st.get("exp", 0)) + int(v)
    st["exp"] = exp
    save_state(st)
    new = level_of(exp)
    return exp, old, new
