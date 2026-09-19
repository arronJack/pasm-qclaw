# PASM 桌面运行时（`desktop/runtime/`）

补齐"让智能体真正跑起来"的那一层。认知层（`pasm_skills.cognition`）决定**做什么**，
运行时决定**什么时候做、被打断怎么办、卡住怎么办、该把什么塞进提示词**。

## 三个长期存在的真实问题

| 问题 | 症状 | 本包给的 |
|---|---|---|
| 纯被动 | 用户不说话就完全没有行为；后台任务没有触发时机 | `RuntimeScheduler` 持续循环 + `on_idle()` 空闲自主 |
| 无抢占 | 长任务跑起来后新消息要等它跑完 —— "回复慢 / 叫不应" | `preempt()` 协作式抢占 |
| 无看门狗 | 某任务卡死拖住整个进程，且无痕迹 | 超时计入 `stuck` 并留痕 |
| 说话时手上没材料 | 模型生成完了才去查记忆 | `ContextInjector` 同步预取注入 |

## 用法

```python
from runtime import RuntimeScheduler, Priority, ContextInjector

sch = RuntimeScheduler(max_workers=2, stuck_after=30.0, idle_after=120.0)
sch.on_idle(lambda: cog.consolidate(apply=True))   # 空闲时自己整理记忆
sch.start()

sch.submit("reply", handle_user_message, priority=Priority.USER, context={"text": text})
sch.submit("consolidate", lambda ctx: cog.consolidate(apply=True), priority=Priority.BACKGROUND)

# 用户又说话了 → 抢占后台任务
if user_typed_again:
    sch.preempt("new user message")
```

```python
inj = ContextInjector(agent=agent, cognition=cog)
ctx = inj.build("我叫什么名字")
system_prompt = base_prompt + "\n" + ctx.text
```

## 设计取舍

- **协作式抢占，不强杀线程**。Python 无法安全中断线程；强杀会留下半写状态，
  比慢一点危险得多。任务自己在 `ctx["_cancelled"]` 检查点退出。
- **看门狗只记录不停掉**。同理：留下证据比粗暴恢复更重要。
- **纯标准库**，不引入 asyncio —— 桌面端已有 Qt 事件循环，再叠一层只会添乱。

## 自检

```bash
cd PASM/desktop
python -m runtime
```
