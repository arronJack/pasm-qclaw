# -*- coding: utf-8 -*-
"""权限三档 —— 参考 WorkBuddy 的「默认权限」设计。

三档语义（每一格都是可证伪的契约，见 selftest）：

    档位        只读查询   常规写入   危险操作        不可逆操作
    安全        ✓直接      ⚠确认     ⚠确认          ⚠确认      ← 默认
    标准        ✓直接      ✓直接     ⚠确认          ⚠确认
    完全访问    ✓直接      ✓直接     ✓直接          ✓直接

两条**与档位无关**的红线（属于安全边界，不叫权限）：
  · 系统目录 / 用户目录这类「禁区」任何档位都不动 —— 由 sysops.is_forbidden 负责
  · **判定不出来时一律按「要确认」处理**：未知档位→安全档，未知风险→高危。
    方向永远是"不动手"，绝不因为参数诡异就自动放行。

为什么单独成模块：确认闸门要在 worker 线程里被频繁调用，逻辑必须纯、可自检、
零 Qt 依赖 —— 这样"三档行为"能被单元测试钉死，而不是靠读代码相信。
"""
from __future__ import annotations

import sys

# —— 档位 ——
LEVEL_SAFE = "safe"
LEVEL_STANDARD = "standard"
LEVEL_FULL = "full"
LEVELS = (LEVEL_SAFE, LEVEL_STANDARD, LEVEL_FULL)

LABELS = {
    LEVEL_SAFE: "安全（推荐）",
    LEVEL_STANDARD: "标准",
    LEVEL_FULL: "完全访问",
}

DESCRIPTIONS = {
    LEVEL_SAFE: "任何写入、删除、执行命令都要先问你。最稳妥，适合日常使用。",
    LEVEL_STANDARD: "常规的整理/改名/写文件自动完成；删除、执行命令、改系统设置仍会问你。",
    LEVEL_FULL: "不再逐次询问，它会自主完成所有操作（含删除与执行命令）。\n"
                "⚠ 请仅在你盯着它干活时开启 —— 不可逆的操作将没有二次确认。",
}

# —— 风险等级 ——
RISK_LOW = "low"      # 只读查询：找文件、读内容、看状态
RISK_MED = "med"      # 可恢复的写入：新建/改名/整理/生成文件
RISK_HIGH = "high"    # 危险：删除、执行命令、改系统设置（默认值）
RISK_CRIT = "crit"    # 不可逆：删非空目录、改注册表、批量覆盖
RISKS = (RISK_LOW, RISK_MED, RISK_HIGH, RISK_CRIT)

ALLOW = "allow"
ASK = "ask"

_MATRIX = {
    LEVEL_SAFE: {RISK_LOW: ALLOW, RISK_MED: ASK, RISK_HIGH: ASK, RISK_CRIT: ASK},
    LEVEL_STANDARD: {RISK_LOW: ALLOW, RISK_MED: ALLOW, RISK_HIGH: ASK, RISK_CRIT: ASK},
    LEVEL_FULL: {RISK_LOW: ALLOW, RISK_MED: ALLOW, RISK_HIGH: ALLOW, RISK_CRIT: ALLOW},
}


def normalize_level(x) -> str:
    """任何输入都归一到合法档位；不认识的一律当「安全」（fail-safe）。"""
    try:
        s = str(x or "").strip().lower()
    except Exception:
        return LEVEL_SAFE
    if s in ("安全", "safe"):
        return LEVEL_SAFE
    if s in ("标准", "standard", "normal"):
        return LEVEL_STANDARD
    if s in ("完全访问", "完全", "full", "all", "unrestricted"):
        return LEVEL_FULL
    return LEVEL_SAFE


def normalize_risk(x) -> str:
    """风险等级归一：不认识的一律当「高危」（fail-safe）。"""
    try:
        s = str(x or "").strip().lower()
    except Exception:
        return RISK_HIGH
    return s if s in RISKS else RISK_HIGH


def decide(level, risk) -> str:
    """返回 ``allow``（直接放行）或 ``ask``（必须问用户）。

    永不返回"拒绝"：真正的拦截边界在 sysops 的禁区判断里，
    权限档位只决定"要不要打扰用户"。
    """
    lv = normalize_level(level)
    rk = normalize_risk(risk)
    try:
        return _MATRIX[lv][rk]
    except Exception:
        return ASK          # 任何意外都回到"要确认"
    return ASK


def needs_confirm(level, risk) -> bool:
    return decide(level, risk) == ASK


def describe(level) -> str:
    lv = normalize_level(level)
    return "%s：%s" % (LABELS[lv], DESCRIPTIONS[lv])


def is_high_risk_level(level) -> bool:
    """完全访问档 —— 切换时界面必须给出显式警告。"""
    return normalize_level(level) == LEVEL_FULL


# ——————————————————————————————————————————————————————
# 自检：把三档的行为钉死成表
# ——————————————————————————————————————————————————————

def selftest() -> int:
    passed = failed = 0

    def check(name, cond, extra=""):
        nonlocal passed, failed
        if cond:
            passed += 1
            print("PASS %s %s" % (name, extra))
        else:
            failed += 1
            print("FAIL %s %s" % (name, extra))

    # 1) 三档 × 四风险的完整行为表
    expect = {
        (LEVEL_SAFE, RISK_LOW): ALLOW,
        (LEVEL_SAFE, RISK_MED): ASK,
        (LEVEL_SAFE, RISK_HIGH): ASK,
        (LEVEL_SAFE, RISK_CRIT): ASK,
        (LEVEL_STANDARD, RISK_LOW): ALLOW,
        (LEVEL_STANDARD, RISK_MED): ALLOW,
        (LEVEL_STANDARD, RISK_HIGH): ASK,
        (LEVEL_STANDARD, RISK_CRIT): ASK,
        (LEVEL_FULL, RISK_LOW): ALLOW,
        (LEVEL_FULL, RISK_MED): ALLOW,
        (LEVEL_FULL, RISK_HIGH): ALLOW,
        (LEVEL_FULL, RISK_CRIT): ALLOW,
    }
    bad = 0
    for (lv, rk), want in expect.items():
        got = decide(lv, rk)
        if got != want:
            bad += 1
            print("   %s/%s 期望 %s 实得 %s" % (lv, rk, want, got))
    check("三档×四风险 12 格全部符合契约", bad == 0, "不符 %d 格" % bad)

    # 2) 默认档必须是"安全"
    check("未知档位归一到安全", normalize_level("") == LEVEL_SAFE)
    check("拼写错误的档位归一到安全", normalize_level("safeish") == LEVEL_SAFE)
    check("None 归一到安全", normalize_level(None) == LEVEL_SAFE)
    check("中文档位可识别", normalize_level("安全") == LEVEL_SAFE
          and normalize_level("完全访问") == LEVEL_FULL)

    # 3) 风险等级 fail-safe：不认识 = 高危
    check("未知风险当高危", normalize_risk("weird") == RISK_HIGH)
    check("未知风险在安全档要确认", decide(LEVEL_SAFE, "weird") == ASK)
    check("未知风险在标准档也要确认", decide(LEVEL_STANDARD, "weird") == ASK)

    # 4) 关键性质：完全访问是唯一能自动放行高危的档位
    auto_high = [lv for lv in LEVELS if decide(lv, RISK_HIGH) == ALLOW]
    check("只有完全访问能自动放行高危", auto_high == [LEVEL_FULL], str(auto_high))

    # 5) 低风险在任何档位都不打扰用户（否则"安全档"会让产品没法用）
    check("低风险三档都放行",
          all(decide(lv, RISK_LOW) == ALLOW for lv in LEVELS))

    # 6) 单调性：档位越高越宽松 —— 序列里一旦出现 allow，后面就不该再有 ask。
    #    （方向别搞反：安全→标准→完全访问，允许应当**只增不减**。）
    mono = True
    for rk in RISKS:
        seq = [_MATRIX[lv][rk] for lv in LEVELS]
        seen_allow = False
        for x in seq:
            if x == ALLOW:
                seen_allow = True
            elif seen_allow:
                mono = False
                print("   非单调（allow 之后又要求确认）:", rk, seq)
    check("档位越高确认越少（单调）", mono)

    # 7) needs_confirm 与 decide 一致
    check("needs_confirm 与 decide 一致",
          all(needs_confirm(lv, rk) == (decide(lv, rk) == ASK)
              for lv in LEVELS for rk in RISKS))

    # 8) is_high_risk_level 只对完全访问为真
    check("只有完全访问被视为高风险档",
          [lv for lv in LEVELS if is_high_risk_level(lv)] == [LEVEL_FULL])

    # 9) describe 不会因脏输入炸
    check("describe 不抛异常", "安全" in describe("bad-input"))

    print("\n%d 项，%s" % (passed + failed, "全部通过" if not failed else "%d 项失败" % failed))
    return 1 if failed else 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        raise SystemExit(selftest())
    print(__doc__)
