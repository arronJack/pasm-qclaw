"""任务调度器 —— 优先级队列 + 抢占 + 看门狗 + 空闲自主 tick。

这是"让智能体真正跑起来"的那一层：认知层决定**做什么**，
调度层决定**什么时候做、被打断怎么办、卡住怎么办**。

为什么不用 asyncio / 现成队列
----------------------------
桌面端是 PySide6 主线程 + 工作线程模型，事件循环已经由 Qt 提供，
再叠一个 asyncio 循环只会增加理解成本。这里用最小可用的线程池实现，
行为可预测、出问题容易定位。

核心行为
--------
- **优先级**：``USER > REACTIVE > BACKGROUND > IDLE``，高优先级先跑
- **抢占**：来了用户消息，把当前低优先级任务标记为已抢占并中止等待
  （协作式：任务自己在 ``ctx.is_cancelled()`` 处退出，不做强杀）
- **看门狗**：任务超过 ``stuck_after`` 秒未完成 → 记入 ``stuck`` 并继续
  （不停掉它，因为无法安全中断线程；但会留痕，便于定位）
- **空闲自主**：队列空且空闲超 ``idle_after`` → 触发 idle 回调
  （这就是"用户不说话时智能体自己整理记忆/自检"的入口）
"""
from __future__ import annotations

import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field
from enum import IntEnum
from queue import Empty, PriorityQueue
from typing import Any, Callable, Dict, List, Optional

__all__ = ["Priority", "TaskStatus", "Task", "TaskResult", "RuntimeScheduler"]


class Priority(IntEnum):
    """数值越小越先执行。"""

    USER = 0        # 用户刚发来的消息：必须最快响应
    REACTIVE = 10   # 由用户消息引发的连锁动作（检索、工具调用）
    BACKGROUND = 20  # 后台任务（记忆巩固、落盘、自检）
    IDLE = 30       # 空闲自主行为（主动开口、整理）


class TaskStatus:
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    PREEMPTED = "preempted"
    STUCK = "stuck"


@dataclass
class Task:
    name: str
    fn: Callable[[Dict[str, Any]], Any]
    priority: int = Priority.BACKGROUND
    context: Dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])

    def __lt__(self, other: "Task") -> bool:
        # 同优先级按创建时间（FIFO）
        if self.priority == other.priority:
            return self.created_at < other.created_at
        return self.priority < other.priority


@dataclass
class TaskResult:
    task_id: str
    name: str
    ok: bool
    status: str
    value: Any = None
    error: str = ""
    ms: float = 0.0
    started_at: float = 0.0

    def as_dict(self) -> Dict[str, Any]:
        return {
            "id": self.task_id, "name": self.name, "ok": self.ok,
            "status": self.status, "error": self.error,
            "ms": round(self.ms, 1),
        }


class RuntimeScheduler:
    """持续运行的任务调度器。

    参数
    ----
    max_workers : 并发工作线程数
    stuck_after : 单任务超过多少秒记入"卡住"
    idle_after  : 队列空且距上次活动多久算空闲
    """

    def __init__(self, max_workers: int = 2, stuck_after: float = 30.0,
                 idle_after: float = 120.0, name: str = "pasm"):
        self.max_workers = max(1, int(max_workers))
        self.stuck_after = float(stuck_after)
        self.idle_after = float(idle_after)
        self.name = name

        self._q: "PriorityQueue[Task]" = PriorityQueue()
        self._workers: List[threading.Thread] = []
        self._stop = threading.Event()
        self._idle_ev = threading.Event()
        self._results: List[TaskResult] = []
        self._lock = threading.RLock()
        self._last_activity = time.time()
        self._preempt_count = 0
        self._stuck_count = 0
        self._completed = 0
        self._failed = 0
        self._idle_handlers: List[Callable[[], Any]] = []
        self._current: Dict[str, float] = {}   # 工作线程名 -> 任务开始时间

    # ------- 提交 -------------------------------------------------

    def submit(self, name: str, fn: Callable[[Dict[str, Any]], Any],
               priority: int = Priority.BACKGROUND,
               context: Optional[Dict[str, Any]] = None) -> Task:
        t = Task(name=name, fn=fn, priority=priority, context=dict(context or {}))
        self._q.put(t)
        self._last_activity = time.time()
        return t

    def on_idle(self, fn: Callable[[], Any]) -> None:
        """注册空闲回调 —— 用户不说话时智能体"自己做事"的入口。"""
        self._idle_handlers.append(fn)

    # ------- 抢占 -------------------------------------------------

    def preempt(self, reason: str = "user-message") -> Optional[str]:
        """抢占：把队列里所有低于 USER 优先级的任务标记为已抢占。

        返回被抢占的任务数说明（字符串），或 None。

        **协作式抢占**：已经在跑的任务不会被强杀（Python 线程无法安全中断），
        但它的 ``ctx['_cancelled']`` 会被置为 True；
        任务自己在合适的检查点退出即可。这是有意的取舍 ——
        强杀线程会留下半写状态，比慢一点危险得多。
        """
        with self._lock:
            self._preempt_count += 1
            self._idle_ev.set()
        pending: List[Task] = []
        while True:
            try:
                pending.append(self._q.get_nowait())
            except Empty:
                break
        dropped = 0
        for t in pending:
            if t.priority > Priority.USER:
                dropped += 1
                self._record(TaskResult(t.id, t.name, False,
                                        TaskStatus.PREEMPTED, error=reason))
            else:
                self._q.put(t)
        for t in pending:
            t.context["_cancelled"] = True
        return "preempted %d task(s): %s" % (dropped, reason)

    # ------- 循环 -------------------------------------------------

    def _record(self, r: TaskResult) -> None:
        with self._lock:
            self._results.append(r)
            if len(self._results) > 200:
                self._results = self._results[-200:]
            if r.ok:
                self._completed += 1
            elif r.status == TaskStatus.FAILED:
                self._failed += 1

    def _worker(self) -> None:  # pragma: no cover - 线程体
        tid = threading.current_thread().name
        while not self._stop.is_set():
            try:
                task = self._q.get(timeout=0.2)
            except Empty:
                self._maybe_idle()
                continue
            if task.context.get("_cancelled"):
                continue
            start = time.time()
            self._current[tid] = start
            try:
                value = task.fn(task.context)
                ms = (time.time() - start) * 1000
                status = TaskStatus.DONE
                if ms / 1000.0 > self.stuck_after:
                    status = TaskStatus.STUCK
                    self._stuck_count += 1
                self._record(TaskResult(task.id, task.name, True, status,
                                        value=value, ms=ms, started_at=start))
            except Exception as ex:
                self._record(TaskResult(
                    task.id, task.name, False, TaskStatus.FAILED,
                    error="%s: %s" % (type(ex).__name__, ex),
                    ms=(time.time() - start) * 1000, started_at=start,
                ))
            finally:
                self._current.pop(tid, None)
                self._last_activity = time.time()

    def _maybe_idle(self) -> None:
        """队列空 → 判断是否触发空闲回调。"""
        if not self._idle_handlers:
            return
        if time.time() - self._last_activity < self.idle_after:
            return
        for h in list(self._idle_handlers):
            try:
                h()
            except Exception:
                traceback.print_exc()
        self._last_activity = time.time()

    def start(self, daemon: bool = True) -> bool:
        if self._workers:
            return False
        self._stop.clear()
        for i in range(self.max_workers):
            t = threading.Thread(target=self._worker,
                                 name="%s-worker-%d" % (self.name, i),
                                 daemon=daemon)
            t.start()
            self._workers.append(t)
        return True

    def stop(self, timeout: float = 2.0) -> bool:
        self._stop.set()
        for t in self._workers:
            t.join(timeout=timeout)
        self._workers = []
        return True

    def run_until_idle(self, timeout: float = 10.0) -> None:
        """把队列排空（单线程执行，测试与同步场景用）。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                task = self._q.get_nowait()
            except Empty:
                return
            if task.context.get("_cancelled"):
                continue
            start = time.time()
            try:
                value = task.fn(task.context)
                ms = (time.time() - start) * 1000
                self._record(TaskResult(task.id, task.name, True,
                                        TaskStatus.DONE, value=value,
                                        ms=ms, started_at=start))
            except Exception as ex:
                self._record(TaskResult(
                    task.id, task.name, False, TaskStatus.FAILED,
                    error="%s: %s" % (type(ex).__name__, ex),
                    ms=(time.time() - start) * 1000, started_at=start,
                ))

    def __enter__(self) -> "RuntimeScheduler":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()

    # ------- 观测 -------------------------------------------------

    def results(self) -> List[TaskResult]:
        with self._lock:
            return list(self._results)

    def stats(self) -> Dict[str, Any]:
        now = time.time()
        return {
            "name": self.name,
            "running": bool(self._workers),
            "queued": self._q.qsize(),
            "completed": self._completed,
            "failed": self._failed,
            "preempted": self._preempt_count,
            "stuck": self._stuck_count,
            "idle_after": self.idle_after,
            "idle_seconds": round(now - self._last_activity, 1),
            "inflight": {k: round(now - v, 1) for k, v in self._current.items()},
            "recent": [r.as_dict() for r in self.results()[-10:]],
        }

    def __repr__(self) -> str:  # pragma: no cover
        return ("<RuntimeScheduler %s workers=%d queued=%d done=%d fail=%d>"
                % (self.name, len(self._workers), self._q.qsize(),
                   self._completed, self._failed))
