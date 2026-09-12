# PASM 核心知识总览

> **PASM = Predictive Agent System with Memory**（带记忆的预测式智能体系统）
> 一个"非 token 内部循环"的仿生认知智能体框架 · 核心版本 **0.7.0** · 引擎契约 **api 1.1**
>
> 本文是 PASM 的**核心知识单一入口**：讲清它是什么、由哪几层组成、每层解决什么问题、
> 代码落在哪个文件、上层怎么调用。看完本文即可理解整个 PASM 引擎。

---

## 一、一句话讲清 PASM

**今天的大模型靠"预测下一个 token"思考——内部滚动千万个词的概率，才给出一个回答。
而人脑里没有 token：你骑车躲坑、伸手接杯子，全程没有一个"词语"在脑内滚动。**

PASM 用工程实现这条直觉：

```
感知 → 工作记忆 → 情景记忆检索 → 世界模型"想象" → 规划 → 行动
      （全程连续潜在向量，零 token）
```

只有当需要**跟人说话**时，才由 `pasm/narrator.py` 这一层把内部状态翻译成自然语言。
**文字是输出接口，不是思考载体。**

### 1.1 与 LLM 的分工（诚实定位）

PASM **不是**聊天大模型，不跟 DeepSeek/GPT 抢知识问答与写作。

| | 语言脑（LLM） | 认知脑（PASM） |
|---|---|---|
| 擅长 | 知识、常识、组织语言、写代码 | 记忆、情绪、性格、习惯、持续成长 |
| 系统 | System 2（慢、深思） | System 1（快、状态驱动） |
| 载体 | token 序列 | 连续潜在向量 |
| 单体性 | 每次对话都是"重新开始" | **会累积、会漂移、会记得你** |

二者组合起来才是完整智能体：**LLM 管嘴，PASM 管"人"**。

---

## 二、三层大脑体系

PASM 提供**三套可互换的"大脑"实现**，接口一致，按环境择优选用：

| 档位 | 实现 | 依赖 | 适用场景 |
|---|---|---|---|
| **完整七层引擎** | `pasm/agent.py` + `pasm/modules/*` | torch | 科研 / 训练 / API 服务 / 完整认知 |
| **轻量认知体** | `pasm/light.py` | 纯 Python（零依赖） | 嵌入式 / 安装包瘦身 / 无 torch 环境 |
| **教学最小版** | 独立仓库 [PASM-Lite](https://github.com/arronJack/PASM-Lite) | torch | 教学 / 阅读理解 / 二次开发 |

> 上层**只依赖接口**（`pasm.engine_api`），不依赖具体实现 —— 换引擎，调用代码一行不改。

---

## 三、七层仿生认知架构

每层都对应一个脑区与已有研究脉络，不是随意堆砌的模块。

| 层 | 名字 | 对应脑区 | 研究对照 | 实现文件 |
|---|---|---|---|---|
| 0 | 感知编码 | 感觉皮层 | World Models / Dreamer（VAE 潜在空间） | `modules/encoder.py` |
| 1 | 工作记忆 | 前额叶-顶叶 | 注意力槽位记忆 | `modules/working_memory.py` |
| 2 | 情景/语义/程序记忆 | 海马体/皮层/基底节 | 情景控制、双系统理论 | `modules/memory.py` |
| 3 | 世界模型 | 皮层柱 / DMN | MuZero / TD-MPC（"想象引擎"） | `modules/world_model.py` |
| 4 | 情绪与神经调节 | 边缘系统 | 奖励预测误差 = 多巴胺 | `modules/emotion.py` |
| 5 | 性格先验 + 发育可塑性 | 基因 / 早期发育 | Meta-RL、课程退火 | `modules/emotion.py` |
| 6 | 元认知 + 全局工作空间 | 前额叶高级功能 | 自适应计算、GWT 理论 | `modules/metacognition.py`、`modules/global_workspace.py` |
| — | 规划器 | 前额叶运动前区 | CEM + MPC（模型预测控制） | `modules/planner.py` |

### 3.1 逐层说明

**层 0 · 感知编码（`encoder.py`）**
输入 27 维观测（网格世界为 5×5 局部视野展平 25 维 + 归一化全局位置 2 维），
VAE 压缩为 **8 维潜在向量 `z`**。所有后续计算都在这个连续空间里完成。

**层 1 · 工作记忆（`working_memory.py`）**
注意力槽位机制，保留最近若干步的上下文 `c`。
两种实现：固定窗口 / 注意力加权。退出时落盘，重启可接续。

**层 2 · 多时间尺度记忆（`memory.py`）**

- `EpisodicMemory`：稀疏键值记忆，**事件驱动写入**（只在"惊讶"或奖励异常时写），KNN 检索 —— 对应海马体
- `SemanticMemory`：抽象后的稳定知识 —— 对应皮层
- `ProceduralMemory`：反复成功的行为固化成**习惯**，下次免规划直出 —— 对应基底节

**层 3 · 世界模型（`world_model.py`）**
短期 GRU 模型：给定 `(z, a, m)` 与隐状态，预测下一潜在状态 `z′` 与奖励 `r`；
另有长期子目标模型。这是"在脑内预演"的物理引擎 —— PASM 的想象能力来源。

**层 4 · 情绪与神经调节（`emotion.py`）**
多巴胺 / 血清素双通道建模，**奖励预测误差驱动**（神经科学经典结论）。
情绪不是装饰：它真实调制规划温度、记忆写入阈值、探索意愿。

**层 5 · 性格先验 + 发育可塑性（`emotion.py`）**
人格参数化（开放性/尽责性/外向性/宜人性/神经质），**随经历漂移**；
发育阶段从"婴儿期"（高可塑性）退火到"成年期"（行为定型）。

**层 6 · 元认知与全局工作空间（`metacognition.py` + `global_workspace.py`）**
门控网络观察各层活动信号，输出调控量（学习率、探索温度、记忆门槛）；
GWT 广播机制在出现"重要事件"（高预测误差/高奖励/久无收获）时向全层广播。

**规划器（`planner.py`）**
CEM（交叉熵方法）+ MPC：不是随机采样 16 条序列了事，而是迭代优化候选动作序列，
在**世界模型里"想象"**，挑累计奖励最高的一条执行。

**整合（`agent.py`）—— `PASMAgent`**
把七层串成单个决策步的信息流，对外暴露统一接口：
`reset_episode / act / learn / snapshot / save / load`。

### 3.2 实测证据（非 PPT）

- **会成长**：从婴儿期发育到成年期，行为随经历定型
- **性格真分化**：同环境同预算，只改先天性格种子 —— 谨慎型比冒险型少撞墙 **31%**
- **内心可解释**：情绪向量（愉悦/唤醒/掌控）、记忆命中、习惯固化、性格漂移全部可实时查询
- **低算力**：CPU 毫秒级单步决策，无需 GPU、无需微调重训

---

## 四、认知执行皮层（Cognitive Cortex）

七层引擎负责**内部计算**（脑干 + 皮层下结构），但它不直接跟用户打交道。
**认知执行皮层**（`pasm/cognitive/`）负责**对外执行**——相当于"前额叶 + 语言区"。

> **这是 PASM 的落地关键层**：它把"类脑能力"翻译成"用户能感知的行为"。
> 全部纯 Python 实现，**无 torch / numpy 重依赖也可降级运行**，桌面产品直接复用。

### 4.1 皮层模块全表

| 模块 | 一句话职责 | 关键机制 |
|---|---|---|
| `cog.py` | **会话级认知状态机** | 意图分类 / 情绪姿态 / 话题惯性 / 目标推进 → 产出"该怎么回话"的决策前缀（姿态、温度） |
| `memory_layers.py` | **分层记忆** | 工作记忆 + 情景记忆 + 程序记忆，纯规则检索，**自动召回进上下文** |
| `workctx.py` | **项目级台账** | 每个项目一本账：需求史 / 改动清单 / 冒烟结果 / 规则；续写时注入摘要 → 精准修改不跑偏 |
| `coder.py` | **多语言真文件开发** | python/vue/js/php/java/go/c#/cpp 脚手架 + 逐文件生成 + 按语言静态冒烟 + 真目录落盘 |
| `agent_team.py` | **多智能体技能团队** | 角色卡 / 流程编排 / 团队执行器 —— 把任务分给多个角色协作完成 |
| `selfheal.py` | **自检·自定位·自修复** | 日志哨兵 → LLM 定位 → 打补丁 → 冒烟验证 → 失败回滚 |
| `symbolic.py` | **神经符号混合推理** | 符号层做确定性求解（约束校验）；`writeback()` 只把**已验证的高置信事实**回写向量记忆，避免污染 |
| `memrouter.py` | **记忆路由器** | 在"关键词记忆 / 向量记忆 / 符号事实"之间路由检索，决定这次该想起什么 |
| `memvec.py` | **轻量向量检索** | 把"关键词命中"升级为"语义相似"，**不引入任何新依赖**（哈希 + 词袋投影） |
| `learning.py` | **可成长内核 + 学习层契约** | 自我设计 + 自我优化：`design / pick / feedback / redesign`；契约 `pasm.learning/1.0` |
| `mathlab.py` | **数学脑（离散与线性分析）** | 纯 numpy：回归(R²) / 描述统计 + 离群 / 相关性 / 方程组 / 拓扑排序 / 最短路 / 连通分量 / 马尔可夫稳态 |
| `percept.py` | **多模态感知（看 + 听）** | 图片 → 本机视觉模型（不上云零成本）；语音 → faster-whisper → Vosk 降级链 |
| `quantum.py` | **量子启发策略决策** | 候选策略 = 叠加态，退火测量选路；同族干涉让经验泛化；情绪作外场偏置 |

### 4.2 神经符号闭环（v0.28.5 接通）

```
用户问题
   ↓
symbolic 符号层求解（约束满足 / 规则推导，确定性、可验证）
   ↓ verify() 通过且带 constraints（高置信）
writeback() 回写 → 向量记忆（形成"经验"）
   ↓
下次同类问题：向量检索命中 → 符号层复用已验证解
```

**关键设计**：`writeback()` 只在**验证一致 + 高置信**时才回写，宁可不写也不污染记忆。
这解决了"向量记忆容易被幻觉污染"的老问题。

### 4.3 分层记忆与召回

| 记忆类型 | 写入时机 | 检索方式 | 用途 |
|---|---|---|---|
| 工作记忆 | 实时 | 直接读 | 正在聊的话题 + 正在推进的目标 |
| 情景记忆 | 每 8 轮 / 新会话 / 退出 | 按话题相似度召回 | "上次那件事是怎么做的" |
| 程序记忆 | 干完活自动记一笔 | 同类任务匹配 | 要什么 → 用了什么 → 成没成 → 教训 |

---

## 五、引擎接口契约（`pasm.engine_api`，api 1.1）

> **零依赖纯标准库**，随包即用，是**整个生态的接缝处**。

### 5.1 为什么需要它

三套大脑（完整引擎 / 轻量认知体 / 教学版）的接口**事实上早已一字排开**，
但只是"约定俗成"——换引擎时上层仍要改代码。

契约层把这套约定**固化、可校验、可发现**，从此**上层依赖接口而非实现**。

### 5.2 契约内容

| 能力 | API |
|---|---|
| **自描述** | `EngineInfo` / `Capabilities` —— 引擎自己说清"我是谁、我会什么" |
| **可校验** | `conforms(engine, strict=...)` → `(bool, problems)` 结构一致性检查 |
| **可适配** | `as_engine(obj)` —— 把任意满足接口的对象包装成标准引擎 |
| **可发现** | `Registry` —— `create("pasm")` / `create("pasm-light")` 按名创建 |
| **择优创建** | `create_best(candidates)` → `(engine, report)`，report 含 `used/degraded/tried/gap` |
| **缺口报告** | `capability_gap(engine, reference)` —— 当前引擎相对参考引擎缺哪些能力 |
| **快照归一化** | `normalize_snapshot()` —— 七区块统一，缺项填 None |
| **扁平参数** | `_adapt_cfg()` —— 支持 `create("pasm", seed=1, plan_samples=8)` 这种直接传参 |

**引擎必需方法**（契约核心）：
```python
reset_episode()          # 开始新一局
act()                    # 决策 → (action, report)
learn(...)               # 吸收经验
snapshot()               # 自省快照（七区块）
save(path) / load(path)  # 持久化
```

### 5.3 环境插件注册表（api 1.1 新增）

引擎不再和某个具体世界绑死——**换环境 = 换注册表里的名字**。

```python
from pasm import engine_api as EA

EA.register_env("my-world", lambda seed: MyWorld(seed), default=False)
engine, report = EA.create_best(("pasm", "pasm-light"), env="my-world")
```

- **必需契约**：`reset() -> obs`、`step(action) -> 任意`
- **可选契约**：`observe()` / `close()` / `spec()` / `obs_dim` / `n_actions`
- **不规定 step 返回形状**：教学版是三元组、完整引擎是四元组 —— 强行统一反而制造新耦合，
  由引擎自己适配它要跑的环境。

内置环境：`grid-10x10`（网格世界）、`toy-vector`（连续向量，非网格）。

---

## 六、学习层契约（`pasm.learning/1.0`）

学习层同样收敛成**"同一接口两档实现"**：

| 档位 | 实现 | 特点 |
|---|---|---|
| **full** | `pasm.cognitive.learning.LearningEngine` | 离散动作标签（wave/ball/dance…），设计 + 反馈微调 + 落盘 |
| **teaching** | PASM-Lite `learning.py` | 连续向量 `(z, action, reward)`，Hebbian 关联式学习 |

**契约五件套**：
```python
info()           # → LearningInfo（api / tier / kind）
capabilities()   # 我会什么
learn(z, a, r)   # 吸收一次经验 ← 统一语义入口
bias(...)        # 决策偏置（学习成果怎么影响行为）
state()          # 导出状态
apply_state(st)  # 恢复状态（可跨档恢复）
```

**核心机制（自我设计 + 自我优化）**：

- `design(persona, stage, actions)` —— 按**性格种子 + 成长阶段**设计初始行为权重
  （0 级只会 wave/hop/peek → 1 级解锁 ball → 2 级 dance/spin → 3 级 think）
- `pick(epsilon)` —— ε-贪心按权重选动作
- `feedback(kind, lr)` —— 用户反馈（praise/scold/poke/hug）微调权重
- `redesign(...)` —— 升级 / 换性格时重新设计，但**保留积累的反馈调整**
- `save() / load()` —— 持久化

> `LiteEngine.attach_learning()` 支持**运行时热插拔**学习层，
> 契约 / 潜维 / 动作数三项校验不过则**拒绝且不动原实现**。

---

## 七、工具链与外围

| 模块 | 职责 |
|---|---|
| `config.py` | 全局配置：所有仿生模块均通过开关控制，**便于消融实验** |
| `training.py` | 训练 / 评估 / **睡眠巩固**全流程（睡前回放情景记忆、固化习惯） |
| `evalkit.py` | 标准化评估框架：让"类脑能力"**可量化、可对比、可复现**（四类指标） |
| `sft.py` | 从交互中提取微调样本 —— 把真实交互变成可复用训练语料 |
| `narrator.py` | 内部状态 → 自然语言（输出接口，不参与思考） |
| `memory_tag.py` | 给情景记忆写"一句话标签"，检索时能回忆成自然语言（RuleTagger 零成本兜底） |
| `cli.py` | 命令行入口：`pasm-train` / `pasm-server` / `pasm-demo` |
| `lockdown`（桌面端） | 系统级安全操作闸门（见 PASM Studio 文档） |

**OpenAI 兼容 API**：完整引擎可提供 `/v1/chat/completions` + SSE 流式，
任何 OpenAI SDK / Dify / LangChain 改一个 `base_url` 即可驱动。

---

## 八、目录地图

```
pasm/
├── __init__.py            包说明与版本
├── agent.py               PASMAgent —— 七层整合
├── config.py              PASMConfig —— 全局配置/消融开关
├── engine_api.py          ★ 引擎接口契约（零依赖，api 1.1）
├── light.py               PASM 轻量认知体（纯 Python）
├── narrator.py            内部状态 → 语言
├── training.py            训练/评估/睡眠巩固
├── evalkit.py             标准化评估
├── sft.py                 微调样本提取
├── memory_tag.py          记忆语义化标签
├── cli.py / __main__.py   CLI 入口
├── modules/               ★ 七层仿生认知架构
│   ├── encoder.py             层 0 感知编码（VAE）
│   ├── working_memory.py      层 1 工作记忆
│   ├── memory.py              层 2 情景/语义/程序记忆
│   ├── world_model.py         层 3 世界模型
│   ├── emotion.py             层 4/5 情绪 + 性格 + 发育
│   ├── metacognition.py       层 6 元认知
│   ├── global_workspace.py    全局工作空间（GWT）
│   └── planner.py             CEM + MPC 规划器
├── cognitive/             ★ 认知执行皮层（纯 Python，桌面复用）
│   ├── cog.py                 会话级认知状态机
│   ├── memory_layers.py       分层记忆
│   ├── workctx.py             项目级台账
│   ├── coder.py               多语言真文件开发
│   ├── agent_team.py          多智能体技能团队
│   ├── selfheal.py            自检·自定位·自修复
│   ├── symbolic.py            神经符号混合推理
│   ├── memrouter.py           记忆路由器
│   ├── memvec.py              轻量向量检索
│   ├── learning.py            ★ 学习层契约 + LearningEngine
│   ├── mathlab.py             数学脑
│   ├── percept.py             多模态感知
│   └── quantum.py             量子启发策略
└── envs/                  环境插件（网格 / 向量）
    ├── gridworld.py
    └── __init__.py            环境注册表转出
```

---

## 九、三种用法

### 9.1 直接当学习算法用

```python
from pasm.agent import PASMAgent
from pasm.config import PASMConfig
from pasm.envs.gridworld import GridWorld

agent = PASMAgent(PASMConfig(seed=1), personality_seed=[0.8, -0.2, 0.6])
env = GridWorld(seed=1)
for ep in range(50):
    agent.reset_episode()
    obs = env.reset()
    while True:
        a, report = agent.act()
        obs, r, done = env.step(a)
        agent.learn(obs, a, r, done)
        if done:
            break
agent.save("checkpoints/demo.pt")
```

### 9.2 经接口层调用（推荐 —— 换引擎不改代码）

```python
from pasm import engine_api as EA

# 择优创建：有 torch 用完整引擎，没有自动降级到轻量认知体
engine, report = EA.create_best(("pasm", "pasm-light"), seed=1, env="grid-10x10")
print(report["used"], report["degraded"], report["gap"])

# 上层只认契约方法
engine.reset_episode()
for _ in range(50):
    a, _r = engine.act()
    engine.learn(obs=None, action=a, reward=1.0)
print(engine.snapshot())
```

### 9.3 桌面产品

见 [PASM Studio](https://github.com/arronJack/pasm-qclaw) —— 把七层引擎 + 认知皮层
包装成"会思考、会干活、会记住你"的 Windows 桌面 AI 伙伴。

---

## 十、生态与版本

| 仓库 | 定位 | 可见性 |
|---|---|---|
| **PASM**（本仓） | 核心引擎 + 认知皮层 + 桌面全源码 + 文档 | 私有（开发主仓） |
| **pasm-qclaw** | PASM Studio 产品发行：Releases + 更新通道 + 文档 | 公开（MIT） |
| **PASM-Lite** | 零 token 认知最小教学实现 | 公开（MIT） |
| **pasm-skills** | PASM 智能体验证工坊（长期一致性守卫） | 公开（MIT） |

**版本线索**：
- 引擎包 `pasm/__init__.py` → `__version__`
- 接口契约 `pasm/engine_api.py` → `API_VERSION`
- 学习契约 `pasm/cognitive/learning.py` → `LEARNING_API_VERSION`
- 桌面产品 `desktop/appinfo.py` → `APP_VERSION`

**契约变更铁律**：任何契约改动必须同步
`pasm/engine_api.py` ↔ `PASM_LITE/engine_api.py`（同源镜像），
用 `tools/sync_engine_api_mirror.py --check` 检测漂移。

---

## 十一、常见问题

**Q：PASM 能替代 DeepSeek 吗？**
不能，也不打算。PASM 不做知识问答与写作——那是 LLM 的主场。PASM 管的是
**记忆、情绪、性格、习惯、持续成长**，是"人"的那部分。

**Q：没有 GPU 能跑吗？**
能。完整引擎 CPU 毫秒级单步；轻量认知体（`pasm.light`）纯 Python 零依赖。

**Q：为什么内部不用 token？**
因为实验目标就是验证"认知可以不依赖离散符号"。一旦内部生成 token，
就退化成了小型 LLM，失去了与 LLM 的互补价值。

**Q：认知皮层和七层架构冲突吗？**
不冲突，分工明确。七层负责**内部计算**（脑干 + 皮层下），
皮层负责**对外执行**（前额叶 + 语言区）。组合起来才是完整神经回路。

**Q：怎么参与 / 提问题？**
公开仓提 issue（主要归口 GitHub）；核心引擎与认知皮层暂未开源，
研究理念与教学代码可先阅读 PASM-Lite。

---

## English Summary

**PASM (Predictive Agent System with Memory)** is a biomimetic cognitive agent
framework whose internal decision loop runs entirely in **continuous latent
vectors — zero tokens**. Language is generated only at the output boundary
(`narrator.py`), which is what makes PASM complementary to, rather than
competitive with, LLMs: **the LLM provides the voice, PASM provides the "self."**

**Architecture**

1. **Seven-layer cognitive engine** (`pasm/modules/`) — perception encoding (VAE),
   working memory, episodic/semantic/procedural memory, world model (MuZero-style
   imagination), emotion & neuromodulation (dopamine/serotonin), personality
   priors with developmental plasticity, metacognition + global workspace, and a
   CEM+MPC planner.
2. **Cognitive Cortex** (`pasm/cognitive/`) — the execution layer that turns
   latent states into user-visible behavior: session state machine, layered
   memory, project ledger, multi-language coder, multi-agent teams,
   self-healing, neuro-symbolic reasoning, memory router, math brain,
   multimodal perception, and quantum-inspired strategy selection.
3. **Engine API** (`pasm/engine_api.py`, api 1.1) — a zero-dependency contract
   (`EngineInfo`, `conforms()`, `as_engine()`, `Registry`, `create_best()`,
   `capability_gap()`) plus an **environment plugin registry**, so that callers
   depend on the *interface*, never on a specific implementation.
4. **Learning contract** (`pasm.learning/1.0`) — one interface, two tier
   implementations (full `LearningEngine` and teaching PASM-Lite), hot-swappable
   at runtime.

Three interchangeable brains ship in the box: the full seven-layer engine
(torch), the lightweight cognitive body (`pasm.light`, pure Python), and the
open teaching edition (PASM-Lite). Swapping engines requires **zero changes** in
calling code.

---

*PASM 核心知识总览 · 与代码同步维护，契约变更请同步更新本文第五节与第六节。*
