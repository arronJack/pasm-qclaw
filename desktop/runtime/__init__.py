"""PASM 桌面运行时 —— 补齐"让智能体真正跑起来"的那层。

``pasm_skills.cognition`` 给的是**认知能力**（记忆怎么检索、怎么遗忘、怎么巩固）；
本包给的是**应用运行时**（什么时候做、被打断了怎么办、卡住了怎么办、
该把什么东西塞进提示词）。两者分层，互不耦合。

三个长期存在的真实问题
----------------------
1. **纯被动**：没有持续主循环，用户不说话就完全没有行为；
   后台任务（记忆巩固、自检）没有触发时机。
2. **无抢占**：一个长任务跑起来后，用户的新消息要等它跑完才被处理 ——
   这就是"回复慢 / 叫不应"的体感来源。
3. **无看门狗**：某个任务卡死（死循环 / 网络挂起）会拖住整个进程，
   且没有任何痕迹，事后无法定位。

本包提供
--------
- :class:`RuntimeScheduler`：优先级队列 + 新消息抢占 + 卡死看门狗 + 空闲自主 tick
- :class:`ContextInjector`：按当前输入预取认知上下文，产出可直接拼进提示词的片段

设计原则
--------
- **纯标准库**，不引入 asyncio / 第三方队列，方便嵌进既有 PySide6 主线程模型
- **异常隔离**：任务炸了只记下来，调度器继续
- **可观测**：:meth:`RuntimeScheduler.stats` 一次给出队列深度、抢占次数、
  卡死次数、各任务耗时
"""
from __future__ import annotations

from .injector import ContextInjector, InjectedContext
from .scheduler import (
    Priority, RuntimeScheduler, Task, TaskResult, TaskStatus,
)

__all__ = [
    "RuntimeScheduler", "Task", "TaskResult", "TaskStatus", "Priority",
    "ContextInjector", "InjectedContext", "selftest",
]


def selftest() -> int:
    """运行时自检。返回失败数。"""
    import time

    from .scheduler import Priority, RuntimeScheduler

    ok = 0
    fail = 0

    def check(name: str, cond: bool, detail: str = "") -> None:
        nonlocal ok, fail
        if cond:
            ok += 1
            print("  v %s" % name)
        else:
            fail += 1
            print("  x %s%s" % (name, ("  <- " + detail) if detail else ""))

    print("PASM runtime 自检")
    print("-" * 56)

    done: list = []

    def slow(ctx):
        time.sleep(ctx.get("sleep", 0.02))
        done.append("slow")
        return "slow-ok"

    def quick(ctx):
        done.append("quick")
        return "quick-ok"

    def boom(ctx):
        raise RuntimeError("故意失败")

    sch = RuntimeScheduler(max_workers=2, stuck_after=1.0)
    sch.submit("boom", boom, priority=Priority.BACKGROUND)
    sch.submit("quick", quick, priority=Priority.USER)
    sch.submit("slow", slow, priority=Priority.BACKGROUND, context={"sleep": 0.05})
    sch.run_until_idle(timeout=5.0)

    check("高优先级先于低优先级完成",
          done and done[0] == "quick", f"实际顺序：{done}")
    check("任务异常被隔离（其余照常完成）",
          "slow-ok" in [r.value for r in sch.results()] or "slow" in done)

    errors = [r for r in sch.results() if not r.ok]
    check("失败任务有记录", bool(errors) and "RuntimeError" in errors[0].error,
          f"errors={[e.error for e in errors]}")

    st = sch.stats()
    check("stats 给出运行统计",
          "completed" in st and "failed" in st and "preempted" in st)
    check("抢占计数存在", st["preempted"] >= 0)

    # 抢占：新消息打断当前任务
    sch2 = RuntimeScheduler(max_workers=1, stuck_after=5.0)
    sch2.submit("long", slow, priority=Priority.BACKGROUND, context={"sleep": 0.3})
    sch2.start()
    time.sleep(0.02)
    interrupted = sch2.preempt("user-message")
    check("可以抢占当前任务", isinstance(interrupted, str))
    sch2.stop()

    # 上下文注入
    inj = ContextInjector()
    ctx = inj.build("我叫什么名字", extra={"mood": 0.3})
    check("注入器产出可拼进提示词的文本", bool(ctx.text) and len(ctx.text) > 0)
    check("注入器报告来源", isinstance(ctx.sources, list))

    print("-" * 56)
    print("结果：%d 项通过，%d 项失败" % (ok, fail))
    return fail
