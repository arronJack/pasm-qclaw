# PASM Studio · 会思考、会干活、会记住你的 AI 伙伴（Windows 桌面版）

**简体中文**（正文） · [🇬🇧 English（完整英文版见下方）](#english--英文完整版)

📚 **文档导航**：[**全部功能总览**](docs/FEATURES.md) · [**PASM 核心知识总览**](docs/CORE.md) · [更新日志](CHANGELOG.md)

---

## 〇、PASM 生态索引（六仓同频）

| 仓 | 角色 | 可见性 | 版本 |
|---|---|---|---|
| `pasm-skills` | 基座：`BaseAgent` + 认知能力层 | 公开 | 0.5.0 |
| `pasm-agents` | 成品智能体集（NPC / 陪伴 / 教学 / 验证） | 公开 | 0.4.5 |
| `pasm-mcp-server` | MCP 接入层：给任意 AI 客户端装长期记忆 | 公开 | 0.2.0 |
| `PASM-Lite` | 教学版 + 认知引擎接口 | 公开 | — |
| `PASM` | 核心引擎（七层仿生 / 世界模型） | **私有** | 0.7.2 |
| **`pasm-qclaw`（本仓）** | **桌面应用发行通道** | 公开 | **0.30.13** |

本仓是**发行通道**（安装包 + 更新清单 `latest.json`），桌面源码在私有核心仓 `PASM/desktop`。

地址：[Gitee](https://gitee.com/arronzheng/pasm-qclaw) ·
[GitHub](https://github.com/arronJack/pasm-qclaw)

---

> **v0.29.1 修复版 + 记忆与预演：关键事实记得住、改口会留痕，聊天也不再莫名变慢** —— 打开就能聊：它能思考、
> 学过的本事（笑话/知识/技能）被点名时能真用出来；回复像 DeepSeek 一样思考灰度实时显示、随后正文流出；
> 桌面小人的动作由性格自我设计、随你的反馈自我优化，工作提示变成头顶冒泡气泡，
> 四川/河南/东北/山东/北京方言与粤语、台湾腔一样能听会说。每一次相处都会沉淀成它的记忆与性格——
> 你聊得越多，它越"懂你"，越像"只属于你的那个 AI"。

**无需任何 API Key 也能用**：自动接入你本机已装的 Ollama（qwen/llama 等模型），
本机就是你的服务器；填一个 DeepSeek Key 则更强（见下文「设置语言脑」）。

## 最新：v0.30.13（2026-09-17）· 广告设计 ≠ 图像 / 慢机也能好好聊天 / 工作流自动起步

在 **⚙ 设置 → 💳 支付**（第 6 页）直接配置收款渠道，不用再手改配置文件：

- **收款渠道**下拉：沙箱（默认，不真实收款）/ 微信支付 / 支付宝 —— 切换时下方字段自动跟着换；
- **微信支付**：商户号 `mch_id`、应用 `app_id`、**APIv3 密钥**、证书序列号、商户私钥文件（带「…」直接选文件）、回调地址；
- **支付宝**：应用 AppID、**应用私钥文件**、支付宝公钥文件、网关地址、回调地址；
- **🔎 检查就绪状态**：缺哪项就点名哪项（例如「缺 private_key_path」），不静默降级。

安全上做了四件事：

1. **密钥框只显示掩码**（如 `WX_S************34`）—— 不动它就按原值保存，要改就全选重填；密钥不进日志、不回显；
2. **「允许真实收款」默认关闭**，由关到开要**二次确认**；点「否」时连界面上的勾也会拨回去；
3. **填好凭据 ≠ 会收钱** —— 要收钱必须显式打开 live；
4. 配置写 `%APPDATA%\PASMStudio\payment.json`，保存后**回读校验**。

改完**保存即生效，不用重启**。沙箱用的是真 HMAC 签名 + 真状态机，可以把
「下单 → 支付 → 回调验签 → 查单 → 退款」整条链路先跑通，再接真渠道。

### v0.30.11（2026-09-17）· 12 项真机反馈全部落地 + 产物统一到一个工作根

- **🛩️ 飞天变了**：起飞**原地变身**成飞机形态（不再是"人形平移"），落地变回人形；航线改成随机曲线，
  不再是规规矩矩的直线矩形。
- **🔀 左右两栏不再都叫"自动化"**：左导航改「⚡ 自发行为」，右侧固定栏改「🤖 工作流（AI 多步任务引擎）」，
  并互相注明区别。
- **🖥️ 含糊说法也能开文件**：以前只认写全路径的文件，"打开 E 盘里的 txt"完全不理；现在按"盘符 + 类型"
  做有界搜索，而且**疑问句不抢答**（"E 盘的 txt 怎么打开"就老实回答）。
- **📣 广告设计不再只出图**：新增「创意说明（策略层）」—— 传播目标 / 人群 / 主张 / 调性 / 媒介 / 主视觉构思，
  与设计稿同在右侧栏。
- **🧍 桌面小人三连**：① 今日心情**独立成行**（名字+格言 / 今日心情 / 内心 三行分明）；
  ② 动作**补回 4 个被静默丢弃的**（点头 / 伸懒腰 / 抱臂 / 弹跳），再**新增 4 个**
  （欢呼 / 鼓掌 / 指一指 / 比心），**2D 与 3D 双路径都实现**；
  ③ 新增**形象 DIY** —— 配色三旋钮（色相 / 饱和度 / 明度）+ 头饰开关（兽耳 / 角 / 天线），
  刻意做成 **2D 与 3D 同一套变换**（只改 2D 的话，能跑 3D 的机器上等于看不见）。
- **🛠️ 工作真"对标"了**：代码类工作**真跑官方脚手架 + 真装依赖**，落盘就是能跑的项目
  （实测 express 真装 70 包、vite+vue 真装 35 包）；代码 / 支付类工种此前**根本没有执行器**，现已补上。
- **💳 接入支付（沙箱优先）**：沙箱是**真签名、真状态机**（下单 → 支付 → 回调验签 → 查单 → 退款），
  订单落盘重启不丢；微信 / 支付宝**只留接口与配置**，即使凭据填齐也会明确报"尚未接真实 HTTP"，
  **绝不静默假装成功**，密钥永不回显。
- **📎 上传的图 / 文件看得见了**：聊天区内联缩略图，非图片文件显示带大小的卡片，模型与你都看得见。
- **🔌 工作会自己调工具了**：接通 `pasm-mcp-server`，13 个认知工具并入对话工具总线，
  工作流里真能调 `pasm_observe / pasm_context / pasm_feel`；连不上静默降级。
- **🗂 产物统一到一个工作根**（朋友反馈）：改前产物散在 **4 套互不相通的根**（含写死在桌面的 `PASM创作`），
  现在统一到 `桌面\PASM工作`，下面按类型分 `project` / `image` / `video` / `copy` / `doc` / `team` / `script`；
  存量**迁移**走"复制 → 逐项校验（sha256 + 树摘要）→ 通过才删原"，**可回滚**；
  **只搬产物不搬知识** —— 笔记 / 角色配置 / 书籍一律留原地。会话记录里引用的旧路径一并改写。
- **🐞 顺手修掉 9 个真实缺陷**，其中两个值得一提：**飞天高度旋钮形同虚设**（有定义、有滑块、自检也过，
  却**没有任何代码读它**）；**占位符判据把每一份广告方案都判死**（广告模板自带的「策略层」被当成占位符，
  于是工作流步骤永远回"广告方案未过质检"）。

### v0.30.3（2026-09-16）· 小人形象与行为修复

- **📏 长反了 → 现在真长高**：以前小人越长大越"缩水"（体型系数随成长递减），现已改为递增（幼儿 0.98 → 青年 1.16），陪伴越久越高大，符合直觉。
- **🤖 头部放大、细节更清楚**：2D 头像整体放大；3D 模型头部尺寸上调约 33%（青年期），躯干四肢仍随成长长高，不再"看不清脸"。
- **🤸 更灵动、更主动**：自主动作触发间隔缩短近一半、概率提高；睡眠阈值 45s → 150s，你放着它自己也会动、会玩、会歇。
- **🧪 自检不再误报**：行为自检里写死的"满级 7 动作"改成从动作表动态推导（现 12 个动作、0 级解锁 5 个），以后加动作也不会莫名其妙 FAIL。
- **🗂 数据目录彻底统一**：修正 `growth.py` 的数据目录取用方式，统一走系统标准路径（Win32 权威解析），消除"双击启动写 AppData、其他启动写 home"的数据分裂。

### v0.30.2（2026-09-15）· 修「聊天内容会丢」+ 不再编造「已执行」

- **🐞 你自己发的话不会再丢了**：回复是逐字蹦出来的（约 1.2 秒），而这段时间输入框已经可打字；
  抢在它蹦完之前发一条，那条消息会被它自己的动画覆盖掉（消息其实存着，
  换到别的对话再换回来又会冒出来 —— 所以看着像灵异事件）。现在它先确认
  「这一段是不是我自己的」，不是就另起一段、**绝不覆盖你的话**；
  你抢话时它会把没说完的半句**立刻补完整**再停，不会停在半句上。
- **🚫 不再口嗨「已生成 / 已打开」**：以前它偶尔会说「✅ 已生成并打开浏览器」而屏幕上一片空白 ——
  那是编的，并没有真的执行。现在**每件真事都记一笔账**，说了「已生成 / 已打开 / 已运行」
  却查不到记录时，它会自己更正为「我并没有真的做到」。
- **🚶 走路先转身**：不再正面朝前横向平移 —— 先转身、再迈步，走完自动转回正面；
  转身未完成时会收小步幅，读起来是"先转过身再走"。
- **🎭 动作从 7 个增到 16 个**：新增伸懒腰 / 抱臂 / 弹跳撒欢 / 点头致意 / **飞天转圈**；
  动作**跟着性格抽**（九种性格各有偏好：调皮灵动爱颠球跳舞，温和沉稳爱点头思考……），
  超过 90 秒没人理它还会自己飞天转一圈。
- **👕 换形象会同步**：桌宠换了皮肤，聊天区的小人立刻跟着变。
- **🗂 数据目录不再分家**：以前从终端等方式启动可能把数据写到另一个目录（双击启动却不会），
  现在统一用系统标准路径。

### v0.30.0（2026-09-15）· 3D 机甲小人 + 栏目整合 + 六项工作能力

- **🎮 小人立体起来了，而且真的会动**：换成 **GPU 真 3D 机甲造型** —— 外壳分片、金属质感、
  清漆高光、胸口发光徽记；**会呼吸、会摇摆、头顶天线轻轻晃**，还会跟着鼠标方向转头看你。
  走路 / 挥手 / 跳跃 / 跳舞 / 思考 / 抱球这些动作都看得出来；随成长四档从"圆头圆脑"
  长成"修长青年"（身高体态逐级变化，青年期还会长出肩甲与天线）。
  形体**全部程序化生成**，没有引入任何新依赖；万一机器跑不了 3D，会**自动退回原 2D 画法**，绝不崩。
- **🧩 工作栏清爽了**：原来「文案 / 表格 / PPT / Word」四个按钮收成一个 **「📄 文档与演示」**，
  点它在按钮下方弹出**卡片式展示框**（四种格式各带一句说明、当前格式高亮），
  不占常驻布局高度、点面板外部自动关闭、贴边会自动回拉。
  **显式指定依然优先**：直接说「给我个 Word」照样直接走，不弹选择器。
- **📎 聊天框多了「＋」**：图片 / 文档 / 任意文件 / 截屏四个入口。Word、PPT、Excel 会
  **真读正文与表格**（不只是认扩展名），所以可以直接说"照这份报表做个 PPT"；
  单文件截断 6000 字、单次最多 3 个，坏文件只提示不崩。
- **🔐 权限三档（默认「安全」）**：安全（写文件 / 执行命令都先问）·
  标准（低风险放行、高危确认）· 完全访问（全放行，切换有明确风险提示 + 二次确认）。
  **拿不准一律停下来问你**，不自己猜。
- **✨ 页面切换有过渡动效**，不再生硬跳转。
- **🎙 语音唤醒**：直接说「小U」就醒（**默认关闭**，在设置里打开才占用麦克风；
  助手说话时不会自唤醒；**改了名字唤醒词立刻跟着变**，不用重启）。
- **🚀 开机自动启动**：设置里一键开关，写当前用户注册表，**不需要管理员权限**，开机不弹黑框。
- **🏷 名字统一为「小U」**（桌宠 / 设置 / 知识库 / 语音各处；**你自己改过的名字不会被覆盖**）。

### v0.29.1（2026-09-14）· 修「聊天变慢 5 倍 / 莫名背能力清单 / 语音变哑」

- **🩹 修「聊天突然变慢 5 倍」**：同一句话从 10 秒变 52 秒。定位发现 48.92 秒的「首字」里
  **40.2 秒是把模型重新装进显存** —— 推理侧其实没有退化（同长度提示热态实测首字 1.35 秒、总 8.7 秒）。
  根因是 `keep_alive` 逐请求生效，而 **Ollama 服务端默认只保活 5 分钟**，App 侧闲置超时或某条路径
  掉到兼容端点兜底（会静默丢弃 `keep_alive`）模型就被卸载。已加**驻留守卫**：只在发现模型真不在
  显存时才补载，模型驻留时**绝不打扰**（实测任何触碰都会重置前缀缓存，把 0.3 秒的提示评估打回 4.2 秒）；
  并加请求前体检预热、「只装载不推理」的预热方式，补上「从云端切到本地时预热不跑」的漏口。
- **🩹 修「答非所问，突然背一串能力清单」**：问「能否先吃点小吃？」会回一串能力清单。
  根因是能力询问判定**太宽**（「能否 / 会不会」出现在句子任意位置就算问能力），末尾还有
  **无条件兜底**，模型整轮没参与。已收紧为「框架开在句首 + 能力领域词紧跟其后 12 字内」——
  实测误触发 **9/13 → 0/14**（14 条负样本 + 8 条正样本）。
- **🩹 修「语音变哑 / 念出一串英文」**：微软**静默下架**了 7 个在线声线（女童声线、男童声线 +
  四川 / 河南 / 山东方言），而被下架声线会**返回空音频且不报错**；成长档表又把幼儿 / 童年两档
  全指向女童声线 → **每次合成必失败**，只能回落系统声线硬念中英混排。已改用**存活声线 + 音高塑形**
  做年龄感，并新增**声线守卫**（空音频记黑名单自动换替身 + 每小时校验服务端声线表）——
  **以后微软再下架也不会静默变哑**。实测 12 个（性别 × 档位）+ 全部方言声线逐个真合成 **0 处空音频**。

### v0.29.0（2026-09-14）· 记忆层合一 + 新增「事实层 / 世界模型」+ 找回跨表述记忆

- **🩹 修掉一个正在悄悄丢记忆的缺陷**：桌面端原先各留了一份记忆实现，而且是**拷贝**、不是同一份
  ——同一件事在两个副本里各记各的，表现为「聊天时有时记得、有时不记得」（半失忆），**且不报任何错**。
  现已收敛为**唯一真相源**，并加两道自动守门防复发；实测容量 200 的记忆库被 260 条无关闲聊灌满后，
  **三条早期重要记忆一条没丢**
- **📌 新增「事实层」**：专门记住姓名 / 年龄 / 住址 / 用药 / 过敏 / 家人电话这类**关键事实**，
  与闲聊记忆分开存，**保证不被日常对话挤掉**
  - **改口会留痕**：从「北京」改说「上海」，旧事实不是删掉而是标记失效（历史可查），检索只返回当前有效的
  - **可信度保护**：把握不大的新说法**不会**盖掉把握很大的旧事实，而是先「挂起待确认」
  - 问「我住哪儿」「吃什么药」「对啥过敏」都能查到
- **🎯 新增「世界模型」**：同类处境做过几次、成过几次，给出**成功率估计**与**瓶颈提示**
  （如「成功率约 83%」）；**知道就说、不知道就说不知道**，不给虚假确定
- **🔍 记忆检索：换种说法也找得到**：问「我叫什么名字」现在能查到存成「姓名」的事实
  （实测该场景召回率 **50% → 100%**）
- **📦 同时包含 0.28.6 / 0.28.7 / 0.28.8 全部能力**：中文语言锁与干活前确认门、浏览器自动化与实时天气、
  语音稳定性、PPT 插图、定时与周期任务、邮件连接器、日历连接器、跨端远程桥、自进化沉淀技能、多智能体协作层
- 安装包体积 **77.5MB**（含浏览器自动化所需的运行时）

> 含 0.28.5 全部能力：认知核心层落盘 PASM 核心包、引擎接口统一调用（api 1.1）、
> 学习层「同一接口两档实现」可热插拔、三篇能力总览文档。

### v0.28.5（2026-09-12）· 三层契约收敛 + 认知核心层落盘 + 文档补全

- **🧠 认知核心层落盘 PASM 核心包**：符号推理 / 记忆路由 / 向量记忆 / 学习层从桌面端提升为
  `pasm.cognitive.*` 单一真相源，桌面端改为再导出薄壳——跨版本行为更一致，也为多端复用打好地基
- **🔌 桌面端改用引擎接口统一调用**：新增桌面引擎工厂，按契约「择优创建」并给出**降级报告**
  （如实告知当前跑的是完整引擎还是内置轻量链路）；**换引擎 = 换注册表里的名字，调用方一行不改**
- **🧩 引擎接口契约 api 1.1**：新增**环境插件注册表**（可换非网格世界）、`create_best()` 择优创建、
  `capability_gap()` 能力缺口报告；契约自检 12 → 20 项
- **🎓 学习层「同一接口两档实现」**：核心档（离散动作 + 性格设计）与教学档（连续向量关联式）
  同接口可互换，并支持**运行时热插拔**；修正两档 `learn()` 签名不一致导致「学习静默失效」的隐患
- **📚 文档补全**：《PASM 核心知识总览》《PASM Studio 全部功能总览》《PASM-Lite 全部功能总览》
- **📦 安装包瘦身**：清理构建残留，体积 63MB → **51MB**


> 含 0.28.4 全部能力：语音接收方言识别修复、八种方言（粤语/台湾腔/四川/河南/东北/山东/北京/云南）、
> 神经符号混合推理、记忆路由器、小人行为自我设计/自我优化、工作状态显示、灰度思考显示。

### v0.28.4（2026-09-12）· 语音接收修复 + 方言/普通话精准识别

- **🎤 语音接收「听不懂」三处根因修复**：①**语种被永久锁死**——旧逻辑只在检测到粤语/台湾腔时
  切换识别器，**从不复位回普通话**，一旦说过粤语就被锁在 `zh-HK`，之后说普通话或川/豫/京话全部
  听不懂；②**默认识别器不一定是普通话**——装了方言语音包时列表顺序可能把 `zh-HK` 排前面；
  ③**无容错回落**——专业语种识别不到时不会退回普通话再听。现已：启动强制优先 `zh-CN`
  （覆盖普通话 + 川/豫/京/滇/东北/鲁等官话方言），每按 🎤 显式定语种，识别空或低置信度自动回落普通话再听
- **🗣 单句方言即识别、即切音色、即用方言回**：一句话判别方言 → 立刻切对应口音回读 → 用对应口语回复
- **➕ 新增云南方言**：方言支持扩至 8 种（粤语 / 台湾腔 / 四川 / 河南 / 东北 / 山东 / 北京 / 云南）
- **🔍 语音自检增强**：`diagnose()` 增加方言识别包说明，装没装语音包一目了然

> 含 0.28.0 ~ 0.28.3 全部能力：神经符号混合推理、记忆路由器、小人行为自我设计/自我优化、
> 工作状态显示、灰度思考显示、七地方言同权、团队与技能库完整可用。

📖 **全部功能清单见 [docs/FEATURES.md](docs/FEATURES.md)**（16 章 + 完整模块索引）

### v0.28.3（2026-09-11）· 真机四修

- **💭 回复丢字根治 + DeepSeek 式灰度思考**：云端模型把思考混在正文里返回时，聊天窗会把
  `<think>` 当未知 HTML 标签**连同内容一起吞掉**（这就是"部分文字丢失"）——新增流式思考拆分器根治；
  `reasoning_content` 独立思考字段不再被丢弃。现在思考以**灰度小字实时上屏**（💭 前缀），
  正文随后流出，最终态保留"灰度思考 + Markdown 正文"，与 DeepSeek 官方一致
- **🗣 方言串味根治**：soft 词表全是普通话高频单字（整/贼/老/啥/咋/啦/儿…）、河南 markers 有裸"中"——
  普通话聊几句就被偷偷攒分带成东北/河南腔。收紧为方言独有强词 + 切音计数取最大后，
  普通话归普通话、七地方言照常识别与朗读
- **🎈 工作提示 → 头顶冒泡气泡**：小人工作提示（如「与XX聊天」）从头像下方的矩形改到**头顶上方**的
  冒泡气泡：白底圆角 + 青色描边 + 向下小尾巴 + 柔和投影
- **🛠 团队功能无反应 + 技能库为空 根治（历史遗留）**：打包缺陷致 `pasm` 引擎包从未进安装包——
  团队/数学脑/自修复等**自 v0.24 起在安装版静默失效**；0.28.2 起内置技能库数据又丢失。
  本版按修复后的 spec 重建，团队功能首次在安装版全面可用、内置技能 8 篇恢复

### v0.28.2（2026-09-11）· 小人行为自我设计 + 工作状态 + 方言全量升级

- **🧠 小人行为「自我设计 + 自我优化」引擎**：桌面小人的动作**不再预设**——按成长阶段解锁
  （0 级 挥手/蹦跶/探头 → 1 级 踢球 → 2 级 跳舞/转圈 → 3 级 思考），基础偏好由九宫格性格
  （脾气×能量×爱玩）**自我设计**；你夸它、戳它、训它都会让它**自我优化**动作权重（ε-贪心探索防僵化），
  持久化到 `pet_behavior.json`，重启不丢、升级/换性格也不清
- **💼 工作状态呈现**：小人干活时显示真实工作标签（📖 读书自习 / 🌐 上网自学 / 💬 陪你聊天 /
  或任务台账里的真实任务名），工作时一抹"专注"表情、娱乐动作自动让位——不再"一边陪你干活一边自顾自蹦跶"
- **🗣 方言全量升级**：四川 / 河南 / 东北 / 山东 / 北京 提升至与粤语、台湾腔**同权**——各自独立识别正则 +
  特征词 + 真人方言神经语音（edge-tts）；北京无独立方言音优雅回落普通话。你回复里的方言词会驱动 TTS 自动切对口音
- **🧮（v0.28.0）神经符号混合推理层 + 记忆路由器**：数学应用题按语义选脑——百分数/绳长/涨跌链等由符号层精算，
  不再"算错还强行纠正"；记忆按问题语义路由注入，越聊越准
- **🔍（v0.28.1）全面复查修复**：粤语同音字（宜家/既/吾/系）识别修复（答非所问根治）、
  "变笨/方言失效/朗读退化"逐条查清修掉，11 条普通话负样本守零误判

### v0.27.1（2026-09-09）· 真实系统操作 + 能力判定 + 难度自适应分裂

- **🛠 真实系统操作**："删掉桌面上截图文件夹"→ 真删（**移入回收站**可恢复）；
  "清理电脑垃圾"→ 先扫描只读报告 → 弹窗确认 → 只清白名单临时目录里 2 天以上的旧文件，
  正在用的跳过并如实计数。Windows/程序目录/盘根硬拒（怎么问都不删）
- **🧭 能力判定（非预设机制）**：需求先判定——有工具直接做 / 学过就用学到的真做 /
  不会但可学就先上网学再真做 / 真做不了说明真实原因（如"关机需要物理动作"）。
  **绝不再编造"清理完了"这种没执行的话**
- **🧬 难度自适应分裂**：简单任务主体直接干；复杂任务拆子步骤逐个执行+台账+主体复核；
  破坏性操作主体亲自执行且必须用户确认
- **📋 执行台账**：每个真实操作记录 时间/对象/原因/结果；问"你真的删了吗？怎么验证"→ 直接亮台账
- **🛡 防幻觉兜底**：系统提示诚实守则——没有真实执行成功，绝不允许说"已完成"

### v0.27.0（2026-09-09）· 数学脑 + 多模态 + 量子策略 + 四大趋势

- **🧮 数学脑 mathlab**："帮我分析这个表格"→ 真算：回归(R²)/描述统计+离群/相关性/方程组/拓扑排序/最短路/连通分量/马尔可夫稳态，结论一句话中文给出
- **👁 多模态感知（Phase B）**：图片拖进聊天框/直贴截图 → 本机视觉模型看图（不上云零成本），下条消息带着图理解；语音转写 faster-whisper→Vosk 降级链
- **⚛ 量子策略（Phase C）**：候选策略=叠加态，退火测量选路——早期多探索、随经验收敛最优；同族干涉让经验泛化；情绪作外场偏置（急躁→直给）；成败回灌形成决策闭环
- **🔀 多模型路由**：闲聊走本地 Ollama（快）、干活走 DeepSeek（强）、评审走本地交叉校验，本地不可用自动回落
- **🔁 团队自检重试 + 🕸 拓扑校验**：单步异常/空壳/过短 → 自动重试 + 质量自检；流程支持 deps 依赖，环依赖/越界/自环校验不过不准跑
- **🏗 经验沉淀 + 🧠 项目级跨会话记忆**：项目跑完沉淀流程模板一键复用；团队执行自动注入项目档案
- 冒烟：mathlab 29 · phaseA 22 · trends 29 · bc+拓扑 29 · UI 回归 105 = **全绿**

- **📎 @引用真正可视化**：输入框打 `@` 弹出可引用文件快选（最近产物/资料库/本地文件，↑↓+Enter 选择、继续打字过滤），选中后消息里落成 `@路径`、**输入框上方出现 📎引用条**（点 ✕ 移除，输入框里的引用写法同步清掉）；支持 `@“含空格路径”` 引号写法；引用正文只进模型上下文，"你"气泡保持干净
- **🐛 修复 "@小U 被瞎聊"**：找不到的引用不再原样丢给模型（旧版模型见到 `@小U` 就当打招呼），纯引用消息找不到会明确提示并剥除标记；长文本/代码里的 `@` 不受影响
- **📎 @引用补齐**：@完整路径 / @文件名（按名在常用目录搜索兜底）/ @资料词条 → 读进上下文；找不到会提示怎么用
- **🏢 团队=真公司项目组**：新项目=空白（成员与流程亲手组建）；每项目独立编制、可编入/移出成员（人才库保留）；切换项目呈现各自 成员/流程/任务状态✅⚠️○/历次效果档案；跑完状态自动回写；聊天"让团队做X"仍一键开工
- 冒烟：全系回归绿（v0261 26 · v0262 22 · v0260 39 · v0250 33+33 · v0240 57+15 · v0231 26 · v022x/v0190）

## v0.26.2（2026-09-08）· 干活报错修复 / PASM 记忆情感真正调制 / 表格同文件精准续改

- **🐛 修复干活报错**：能力问答 8 个快捷锚点漏包列表导致的 `str.join()` 崩溃（一句话修复）
- **❤️ PASM 记忆情感真正可感知**：情绪词表扩到 40+（口语/抱怨/报错都会拉动心情）；情绪反馈不再被随机步稀释、撞顶；心情翻译成「此刻口吻」注入每条回复（开心→轻快、低落→放轻、急→直给重点）；做表格/方案/PPT/开发时自动带上与你相关的记忆与偏好
- **📄 表格/文档/PPT 同一文件续改**：同会话反复改改的是同一个文件——把上一版完整内容喂回模型（不再整篇重写成不相干内容）→ 覆盖写回；"另存/新做一份"才另开；产物记忆随会话持久
- 冒烟：v0262 新增 18 项全过；v0261/v0260/v0250/v0240/v0231/v0230 全回归绿

## v0.26.1（2026-09-08）· 五连真机修复（体验/打开软件/任务并发）

- **🩺 健康状态不再占屏**：自检自愈收成底部一条小卡，自动动态区回归主位
- **👥 团队页卡片化**：项目条 → 成员/流程两卡 → 开工区；成员行带 ★评分，单击员工卡打分、右键辞退
- **🎓 技能三层落地**：新增 🎯专家2（内容导演/数据分析师）+ 🔌连接器3（联网/本地文件/办公文档）；添加向导可选类型
- **📂 打开软件真启动**：修复"说打开了其实没有"——称谓前缀剥离+品牌安装路径真实探测+启动后进程真验证，绝不口嗨（"小U，帮我打开网易云音乐"现在真能开）
- **⏳ 任务不丢回复**：真机"只有提问没回复"根因修复——重活开工有提示、切走再回来结果当场显示、收尾全程兜底写回原会话
- 冒烟：v0261 新增 25 项全过；v0260/v0250/v0240/v0231/v0230 全回归绿

## v0.26.0（2026-09-08）· 项目台账精准重调 / 真实文件开发 / @引用与/指令

- **📒 项目级台账**：文案/表格/PPT/开发每个项目都有记录（需求史+每轮改了什么+冒烟+你的规则）；
  续写时自动把台账带进上下文精准修改——不再"牛头不对马嘴"
- **🛠 真实文件开发**：`/开发 需求` 或"用php/java/vue/go/c#/c++ 开发…"→ 选目标目录真实落盘
  （8 种语言脚手架 + 逐文件生成 + 按语言静态冒烟），产出自动记台账，"改这个项目"精准续改
- **📎 @引用**：消息里 `@文件路径` / `@资料词条`，自动把内容带进上下文
- **/️⃣ 指令**：`/开发 需求` · `/技能 技能名 需求` · `/项目`（看台账）
- （v0.25.0：公司式项目组·聊天提速·自愈纯后台 · 见 CHANGELOG）

- **👥 公司式项目组**：项目空间保存目标+流程+历次产物；成员=点开看职责与评分的智能体员工卡；
  流程拖拽排序、双击改任务；🌐网上招聘（抓岗位资料自动提炼员工卡）、🎓技能库选才；
  每步表现自动评分——分低就换更好的成员
- **⚡ 聊天提速**：双脑协同只在写代码/写作等重活时启用，闲聊问答单次直出，首字延迟大幅回落
- **🩺 自检自愈纯后台**：静默自动运行不打扰，自动化页只留「查看健康状态」按钮
- **🎓 技能三层化**：🎯专家 / 🎓技能 / 🔌连接器 类型标注与筛选
- **🔧 安装器根除 MoveFile 错误 5**：升级先清空程序目录（保留 data）再全新复制，不再替换旧文件
- **🧬 PASM 核心引擎同频（0.5.0）**：多智能体团队与自检自愈真身移入引擎执行皮层 `pasm.cognitive`，桌面端为兼容门面，二者共用同一实现
- **👥 多智能体技能团队**：内置 PM/策划/调度/开发/审查/调试 六角色卡，可自添加"员工卡"组成自定义流程；
  聊天说「让我的团队做 X」自动开工，审查不过自动返工、失败自动重试，过程全程可视
- **🩺 自检·自定位·自修复**：启动静默巡检日志崩溃栈记入故障案例库；可出文件+行号级诊断报告；
  建议/自动修两档——自动修=补丁→冒烟回归→不过自动回滚（安全阀把守，安装版强制建议档）
- （v0.24.0：方舟404修复·主题加固 / v0.23.0：DeepSeek式闲聊门·排队并发·PASM×LLM真协同——详见 CHANGELOG）

## 它和普通聊天 AI 有什么不同

普通 AI 每轮对话都是"重新开始"，它不记得你、也没有"自己"。
PASM Studio 是 **双脑结构 + 认知执行皮层**：

| 部分 | 负责 | 结果 |
|---|---|---|
| **语言脑**（DeepSeek / 任意 OpenAI 兼容 / 本地 Ollama） | 听懂你、组织回答、反问 | 像真人一样聊得来 |
| **PASM 认知脑**（本地，离线运行） | 每次对话 → 情绪波动、性格漂移、分层记忆 | 真的会长大、真的记得你 |
| **认知执行皮层**（v0.15 新增） | 开口前判断意图/心情 → 决定姿态与语气；按话题自动想起相关经历 | 不再"千人一面"，记得上次怎么干成的事 |

## 从 v0.16 起积累的能力（0.16.0 起步，以上 0.16.1–0.16.7 持续更新）

**聊得更顺手（界面大改）**
- 输入框上方 **[💬 聊天] / [🔧 干活]** 分栏：聊天模式绝不会因为提到
  "做个应用/写个脚本"就擅自动手，想让它干活时一键切到干活模式——动不动手由你说了算
- 多行大输入框（Enter 发送 / Shift+Enter 换行）；顶部可随时**切换大模型**
  （云端 DeepSeek / 本地 Ollama 各模型任选，选择会记住）
- 左侧**会话列表**：每条消息实时保存，聊天内容永不丢；新对话/切换/重启后
  都能点回旧会话继续聊

**学得更真（知识库 v3 + 自主进化）**
- 每条知识 = 全文 + 要点 + 来源；**学到 = 原文入库**——自学会多源搜索并抓全文存库，
  提炼要点只是学完后的速记索引，绝不会因"要点提炼不出"把内容丢掉（v0.16.5 起）
- **资料库可直接读原文**：双击条目/书架打开全文阅读器，翻看它真正学到的整篇内容，
  支持关键词过滤（v0.16.5 起）
- 自动上网自学默认开启，主题由"好奇心选题器"自己挑（避开已学、延伸进阶）；
  学后自测 3 问，答不出如实记入"待弄懂"清单，不假装学会

**本事可扩展（技能系统）**
- 随时给它在数据目录里添加新技能（名称+说明+规程），干活时自动检索调用；
  内置视频脚本创作 / 营销文案 / 漫剧一条龙示例
- "帮我写个短视频脚本"→ 走创作技能产出分镜表，**不再误入写代码**

**会记得（分层记忆）**
- 工作记忆：正在聊的话题 + 正在推进的目标（退出时落盘，重启接得上）
- 情景记忆：每 8 轮 / 新会话 / 退出时自动归档一段经历，跨会话按话题自动召回
- 程序记忆：干完活自动记一笔"要什么→用了什么→成没成→教训"，下次同类任务自动想起

**会干活（上下文与工具打通）**
- 读你电脑上的文件并总结/分析/找关键信息（txt/md/docx/xlsx/pdf/代码…）
- 读文件夹：列出内容并抽取可读文件，"分析一下 D:\某文件夹 里的文件"直接可用
- 打开电脑应用（微信/计算器/记事本/PowerShell…）与聊天里提到的路径
- 上下文关联：读完说"帮我分析刚才那个文件 / 那个文件夹"能定位
- 写并运行脚本；报错会**自动带错修复重跑一次**（计划-执行-验证环）
- 生成 PPT / Word / Excel 文档；全栈项目开发（前端+后端+数据库）
- 上网自学新主题，所学知识反哺聊天专业度

**会成长**
- 情绪随相处变化、性格随经历漂移、发育阶段从婴儿期长到成年期（桌面小人同步变化）

**更稳**
- 移除 Win7/8 与 PyQt5 向下兼容（最低 Windows 10）：旧系统在**安装阶段**即被
  明确提示；启动错误改为可读中文说明并落 crash.log，不再"双击后只闪一个谜之错误"
- 正常退出不再误弹"错误 0"；安装包约 51MB（无 torch 环境自动用内置轻量认知体）

## 下载与安装

最新版见仓库 **Releases**（v0.30.13，单文件约 86.6MB）：

1. 下载 `PASMStudio-Setup-0.30.13.exe`
2. 双击安装 → 打开 PASM Studio → 点右上「设置」填 LLM Key（或留空用本地 Ollama）
3. 开始聊天；要用「图像/视频/漫剧」真出片时，首次使用按提示接入出图引擎（约 1 分钟）

> 系统要求：**Windows 10/11 x64**（v0.16 起不再支持 Win7/8）· CPU 即可 · 首次安装解压约 1 分钟
> 未填任何 Key 也能用（本地演示模式：同样会记忆与成长，只是话术朴素）
> 历史版本与更新记录见 [CHANGELOG.md](CHANGELOG.md)；升级通道检查仓库 `latest.json`。

## 设置语言脑（一句话）

- **DeepSeek**：填官网 API Key，Base URL 保持 `https://api.deepseek.com/v1`，Model `deepseek-chat`
- **本地免费**：装 [Ollama](https://ollama.com) 后 `ollama run qwen2.5:7b`，
  设置里 Base URL 填 `http://127.0.0.1:11434/v1`、Model 填 `qwen2.5:7b`——完全离线

## 数据与隐私

- 记忆/配置/成长存档在 `%APPDATA%\PASMStudio`（卸载不删除，可随时清空记忆）
- 单机运行，不上传你的对话与文件（仅 LLM 调用本身走你配置的 API）

## 路线图（下一步）

- [ ] **自省训练闭环**：把真实交互沉淀成经验样本，反哺人格成长与轻量模型微调
- [ ] 语音对话打磨 / 云端同步 / 性格报告主页

## 生态与说明

| 仓库 | Gitee | GitHub | 定位 |
|---|---|---|---|
| **PASM**（私有） | [gitee.com/arronzheng/PASM](https://gitee.com/arronzheng/PASM) | [github.com/arronJack/PASM](https://github.com/arronJack/PASM) | 核心认知引擎 + 认知皮层 + 桌面全源码 + 文档（开发主仓镜像） |
| **pasm-qclaw**（本仓库，公开） | [gitee.com/arronzheng/pasm-qclaw](https://gitee.com/arronzheng/pasm-qclaw) | [github.com/arronJack/pasm-qclaw](https://github.com/arronJack/pasm-qclaw) | PASM Studio 产品发布：Releases 安装包 + 更新通道 + 文档 |
| **PASM-Lite**（公开） | [gitee.com/arronzheng/PASM-Lite](https://gitee.com/arronzheng/PASM-Lite) | [github.com/arronJack/PASM-Lite](https://github.com/arronJack/PASM-Lite) | 零 token 认知最小实现的教学版（约 260 行单文件，可读可改，MIT） |

产品当前为闭源发行（公开仓库分发编译产物与更新通道）。
开源计划（2026-09 拟定）：**pasm-qclaw 开发主仓后续开源**，供大家一起研究
桌面产品与双脑结合；**PASM 完整引擎 + 认知皮层暂不开源**，待进一步优化扩展后再议。
研究理念与教学代码可先行阅读 [PASM-Lite](https://gitee.com/arronzheng/PASM-Lite)。
问题 / 反馈 / 合作请提 issue——**主要归口 GitHub**（[issues](https://github.com/arronJack/PASM/issues)），Gitee 同步镜像也会看。

> **商标说明 / Trademark**：「PASM Studio」名称与品牌标识归 arronZheng 所有。
> MIT 许可授予的是代码版权而非商标权——Fork / 再分发请更换名称与标识。
> The name and branding of "PASM Studio" belong to arronZheng; the MIT license covers
> the code, not the trademark — please rename your forks.

---

<a id="english--英文完整版"></a>

# English · PASM Studio — A Desktop AI Companion That Thinks, Works, and Remembers You (Windows)

> **It never loses what you typed, and never claims to have done something it didn't** — Chat right out of the box: it thinks,
**Works with zero API keys**: it auto-detects a local [Ollama](https://ollama.com) install (qwen/llama models) — your machine *is* the server. A DeepSeek key unlocks even better conversations (see *LLM setup* below).

## Latest: v0.30.13 (2026-09-17) · Ad design is now a real deliverable / slow machines can chat / workflows auto-start

**Settings → 💳 Payment** (6th tab) now lets you configure the payment channel without editing any config file:

- **Channel** dropdown: Sandbox (default, never charges real money) / WeChat Pay / Alipay — the fields below switch automatically;
- **WeChat Pay**: merchant ID (`mch_id`), app ID, **APIv3 key**, cert serial, merchant private key file (with a file picker), notify URL;
- **Alipay**: App ID, app private key file, Alipay public key file, gateway, notify URL;
- **🔎 Check readiness** names exactly what is missing (e.g. "missing private_key_path") — no silent degradation.

Four safety guarantees:

1. **Key fields are masked** (e.g. `WX_S************34`) — leave them untouched to keep the stored value; keys never appear in logs or in the UI in clear text;
2. **"Allow real payments" (live) is off by default** and requires an explicit confirmation; if you click No, the checkbox snaps back;
3. **Filling in credentials is not the same as charging money** — live must be turned on explicitly;
4. Config lives in `%APPDATA%\PASMStudio\payment.json` and is read back for verification after saving.

Changes take effect on save — no restart needed. The sandbox uses real HMAC signing and a real state machine,
so you can exercise the full order → pay → notify-verify → query → refund flow before connecting a live channel.

### v0.30.11 (2026-09-17) · All 12 field reports fixed · everything under one work root

- **🛩️ Flight reworked**: it now **transforms into a plane in place** on take-off (no more a human
  sliding through the air) and turns back on landing; the route is a random curve, not a tidy
  rectangular loop.
- **🔀 No more two panels both named "Automation"**: the left nav is now "⚡ Spontaneous Behaviour"
  and the right fixed tab is "🤖 Workflow (multi-step AI engine)", each explaining the difference.
- **🖥️ Vague file requests work**: previously only fully-qualified paths were understood, so
  "open the txt on E:" was ignored. It now does a bounded "drive + file type" search — and
  **does not jump in when you are merely asking a question**.
- **📣 Ad design now includes the rationale**: a creative-brief block (objective / audience /
  proposition / tone / media / key visual), shown next to the artwork in the right panel.
- **🧍 Desktop companion, three fixes**: mood is on its own line; **4 silently-dropped actions
  restored** (nod / stretch / arms / bounce) plus **4 new ones** (cheer / clap / point /
  finger-heart), implemented on **both** the 2D and 3D paths; and **appearance DIY** —
  hue / saturation / value knobs plus head-accessory toggles, deliberately using **one shared
  transform for 2D and 3D** (a 2D-only change would be invisible on 3D-capable machines).
- **🛠️ "Work" now really scaffolds**: code tasks run the official scaffolders and install
  dependencies for real (measured: express 70 packages, vite+vue 35), leaving a runnable project
  on disk. Code and payment job types previously had **no executor at all**.
- **💳 Payments integrated (sandbox first)**: the sandbox uses **real HMAC signing and a real state
  machine** (order → pay → signed callback → query → refund), persisted across restarts.
  WeChat Pay / Alipay have interfaces and configuration only; even with full credentials it stops
  with an explicit "real HTTP not implemented yet" rather than **silently pretending to succeed**.
  Secrets are never echoed back.
- **📎 Uploads are visible**: inline thumbnails in chat, size-labelled cards for non-images —
  both you and the model can see them.
- **🔌 Work calls tools by itself**: `pasm-mcp-server` is wired in and 13 cognitive tools joined the
  conversation tool bus; it degrades silently if the server is unavailable.
- **🗂 All output unified under one work root** (a friend's suggestion): output used to be split
  across **four unrelated roots** (one of them hard-coded to the Desktop). It now all lands under
  `Desktop\PASM工作`, categorised as `project` / `image` / `video` / `copy` / `doc` / `team` / `script`.
  Existing output is **migrated** via copy → verify (sha256 + tree digest) → delete-only-if-verified,
  **and is rollback-able**. **Only artefacts move** — notes, role config and books stay where they are.
  Stale absolute paths embedded in saved conversations are rewritten too.
- **🐞 Plus 9 real defects found and fixed along the way**, two worth naming: a flight-height slider
  wired to **nothing at all** (defined, exposed, self-tested — and never read by any flight code),
  and a placeholder check that condemned **every** ad plan (the ad template's own word
  "strategy layer" matched the placeholder pattern).

### v0.30.2 (2026-09-15) · Chat no longer loses your messages · no more fake "done"

- **🐞 Your own messages are no longer swallowed**: replies used to be typed out character by
  character (~1.2s), while the input box was already usable. Send a new message inside that
  window and it got overwritten by the typing animation (the message *was* saved, so it reappeared
  after switching conversations — which is why it looked like a ghost). Now it verifies
  "is this block mine?" before redrawing, starts a new block instead, and **never overwrites your
  words**; if you cut in, it **finishes its half-sentence immediately** instead of freezing.
- **🚫 No more empty promises**: it used to say "✅ generated and opened in browser" with nothing on
  screen — that was fabricated. Now every real action is written to a ledger, and any claim of
  "generated / opened / ran" without a matching record is **corrected to "I did not actually do it"**.
- **🚶 Turns before walking**: no more sideways sliding while facing forward.
- **🎭 16 actions (was 7)**: stretch / arms crossed / bounce / nod / **fly-and-spin**,
  chosen **by personality** (9 personas each with their own taste).
- **👕 Avatar change syncs** to the chat-side character, and the **data folder no longer splits**
  depending on how the app was launched.

### v0.30.0 (2026-09-15) · A 3D mecha companion, a cleaner work bar, six new capabilities

- **🎮 It's 3D now — and it actually moves.** The companion is rendered with real GPU 3D:
  layered armour, metallic sheen, clear-coat highlights, a glowing chest emblem. It **breathes,
  sways, its antenna wobbles**, and it turns its head to follow your cursor. Seven actions are
  visibly distinct (idle / walk / wave / hop / dance / think / hold-ball), and it **grows through
  four stages** from a round-headed toddler to a slender adult — with shoulder plates and an
  antenna appearing in the later stages. Fully procedural, **zero new dependencies**; if your
  machine can't do 3D it silently falls back to the original 2D drawing and never crashes.
- **🧩 Cleaner work bar.** The four document buttons (copy / spreadsheet / PPT / Word) became one
  **「📄 Documents & Decks」** entry that pops a **card picker** — each format with a one-line hint,
  the current one highlighted. It takes no permanent layout space, closes when you click away, and
  repositions itself near screen edges. **Explicit requests still win**: say "give me a Word file"
  and it goes straight there, no picker.
- **📎 A「＋」in the chat box**: images, documents, any file, and screenshot. Word / PPT / Excel are
  **actually read** (body text *and* tables, not just the extension), so you can say "turn this
  report into a deck". 6000 chars per file, up to 3 at a time; bad files warn instead of crashing.
- **🔐 Three permission levels** (safe / standard / full access; default **safe**): safe asks before
  writing files or running commands; switching to full access requires an explicit risk confirmation.
  **When in doubt it stops and asks** rather than guessing.
- **✨ Page transitions** are animated now.
- **🎙 Voice wake word**: say 「小U」 to wake it (**off by default** — the mic is only used once you
  enable it in Settings; it won't wake on its own voice; **renaming the character updates the wake
  word immediately**, no restart needed).
- **🚀 Launch-at-startup** toggle in Settings — writes to your user registry, **no admin rights**,
  no console window.
- **🏷 The default name is now 「小U」** (pet / settings / knowledge base / speech) —
  **a name you customised is never overwritten**.

### v0.29.1 (2026-09-14) · Fixed 5× slower replies, canned capability answers, and a muted / English-reading voice

- **🩹 Fixed "replies suddenly 5× slower"** — one report went from 10 s to 52 s for the same question.
  The 48.92 s "first token" turned out to be **40.2 s of re-loading the model into VRAM**: inference itself
  had not regressed (hot, same-length prompt: 1.35 s first token, 8.7 s total). Root cause: `keep_alive` is
  per-request while the Ollama server default is only **5 minutes**, so an idle gap — or any path falling back
  to the compatibility endpoint (which silently drops `keep_alive`) — unloads the model. Added a **residency
  guard** that reloads only when the model is genuinely gone and otherwise never touches it (measured: any
  touch resets Ollama's prefix cache, turning a 0.3 s prompt eval into 4.2 s), plus a pre-flight prewarm and a
  "load-only, no inference" warm-up.
- **🩹 Fixed "answers with a capability list instead of your question"** — asking 「能否先吃点小吃？」
  returned a canned capability list. The capability-question test was too loose ("能否 / 会不会" anywhere in
  the sentence counted) and ended in an unconditional fallback, so the model never got a turn. The frame must
  now open the sentence and a capability keyword must follow within 12 characters — false triggers dropped
  from **9/13 to 0/14**.
- **🩹 Fixed "voice goes mute / reads out a string of English"** — Microsoft **silently retired** 7 neural
  voices (the child voices plus the Sichuan / Henan / Shandong dialects). Retired voices **return empty audio
  without any error**, and the growth-stage table pointed both the toddler and childhood stages at a retired
  child voice — so every online synthesis failed and fell back to the system voice. Now uses **surviving
  voices + pitch shaping**, plus a **voice guard** (blacklists empty-audio voices and re-checks the server
  voice list hourly). Measured: 12 gender × stage combinations and every dialect voice synthesised for real —
  **0 empty**.

## v0.29.0 (2026-09-14) · Memory layer unified + new Fact Layer & World Model + cross-phrasing recall

- **🩹 Fixed a defect that was silently losing memories** — the desktop app kept its *own copy* of the
  memory implementation instead of sharing one. The same fact was recorded independently in two copies,
  showing up as "sometimes it remembers, sometimes it doesn't" — **with no error at all**. Now there is a
  single source of truth, guarded by two automated fences. Verified: after flooding a 200-slot memory with
  260 unrelated chit-chat lines, **all three early important memories survived**.
- **📌 New "Fact Layer"** — keeps key facts (name / age / address / medication / allergies / family phone)
  **separate from chit-chat memory**, so they can never be crowded out.
  - **Changing your mind leaves a trace** — saying "Shanghai" after "Beijing" doesn't delete the old fact;
    it's marked invalid (history stays queryable) and recall only returns what's currently true.
  - **Confidence protection** — a low-confidence new statement **cannot** overwrite a high-confidence fact;
    it's held as "pending confirmation" instead.
  - Ask "where do I live", "what medicine do I take", "what am I allergic to" — all answered.
- **🎯 New "World Model"** — for similar situations, it estimates the **success rate** and points out the
  **bottleneck** (e.g. "about 83% success"); **it says "I don't know" when it doesn't know** — no fake certainty.
- **🔍 Recall now survives rewording** — "what's my name" now finds the fact stored as "name"
  (measured recall for this case: **50% → 100%**).
- **📦 Also includes everything from 0.28.6 / 0.28.7 / 0.28.8** — Chinese language lock and pre-work
  confirmation gate, browser automation and live weather, speech stability, PPT illustrations, scheduled and
  recurring tasks, mail connector, calendar connector, cross-device remote bridge, self-evolving skills,
  multi-agent collaboration layer.
- Installer size: **77.5 MB** (includes the runtime needed for browser automation).

> Includes everything from 0.28.5: cognitive core persisted into the PASM package, unified engine-interface
> calls (api 1.1), interchangeable/hot-swappable learning tiers, and three capability overview documents.

### v0.28.5 (2026-09-12) · Three-layer contract convergence + cognitive core persisted + docs

- **🧠 Cognitive core now lives in the PASM package** — symbolic reasoning / memory router /
  vector memory / learning layer promoted from the desktop app to `pasm.cognitive.*` as the single
  source of truth (desktop keeps thin re-export shims). More consistent across versions, ready for reuse.
- **🔌 Desktop goes through the engine interface** — a new desktop engine factory uses the contract's
  "pick the best" creation and returns a **degradation report** (honestly telling whether the full engine
  or the built-in lightweight core is running). **Swapping engines = changing a name in the registry;
  call sites stay untouched.**
- **🧩 Engine contract api 1.1** — environment plugin registry (swap in non-grid worlds),
  `create_best()`, `capability_gap()`; contract self-test 12 → 20 checks.
- **🎓 Learning layer: one interface, two tiers** — full tier (discrete actions + persona design) and
  teaching tier (continuous-vector associative) are interchangeable and **hot-swappable at runtime**;
  a silent-failure trap caused by mismatched `learn()` signatures was fixed.
- **📚 Docs added** — PASM Core Overview, PASM Studio Full Feature Guide, PASM-Lite Feature Guide.
- **📦 Smaller installer** — build leftovers cleaned: 63 MB → **51 MB**.

> Includes everything from 0.28.4: speech-input dialect recognition fixes, eight dialects, neuro-symbolic
> hybrid reasoning, memory router, self-designed pet behaviour, work-state display, gray thinking stream.

### v0.28.4 (2026-09-12) · Speech-input fixes + accurate dialect/Mandarin recognition

- **🎤 Three root causes of speech input "not understanding you" fixed**: ① **the recognizer was
  locked forever** — the old logic only switched to Cantonese/Taiwanese when it detected them and
  **never switched back to Mandarin**, so after one Cantonese sentence the recognizer stayed on
  `zh-HK` and all later Mandarin/Sichuan/Henan/Beijing speech failed; ② **the default recognizer
  wasn't necessarily Mandarin** — with dialect language packs installed, `zh-HK` could sort first;
  ③ **no fallback** — when a specialized recognizer failed, it never retried in Mandarin. Now:
  `zh-CN` is forced at startup (covering Mandarin plus the Sichuan/Henan/Beijing/Yunnan/
  Northeastern/Shandong Mandarin dialects), each 🎤 press pins the language explicitly, and empty
  or low-confidence results automatically fall back to Mandarin and listen again
- **🗣 One dialect sentence → instant detection, instant voice switch, instant dialect reply**
- **➕ Yunnan dialect added**: 8 dialects total (Cantonese / Taiwanese / Sichuan / Henan /
  Northeastern / Shandong / Beijing / Yunnan)
- **🔍 Better speech self-check**: `diagnose()` now reports whether dialect recognition packs are
  installed

> Includes all of 0.28.0 ~ 0.28.3: neuro-symbolic hybrid reasoning, memory router, self-designed &
> self-optimizing pet behavior, work status display, grayed-out thinking, equal footing for all
> seven dialects, and fully working team & skill library.

📖 **Full feature list: [docs/FEATURES.md](docs/FEATURES.md)** (16 sections + complete module index)

### v0.28.3 (2026-09-11) · Four real-device fixes

- **💭 Lost-text root-caused + DeepSeek-style grayed-out thinking**: when a cloud model embeds its chain of thought inside the reply, the chat window used to swallow `<think>` as an unknown HTML tag **along with its content** (that was the "missing text"). A new streaming think-splitter fixes it at the source; the separate `reasoning_content` field is no longer dropped. Thinking now streams in as **grayed-out small text (💭 prefix)** followed by the answer — final state keeps "gray thinking + Markdown body", just like DeepSeek
- **🗣 Dialect bleed root-caused**: the soft-word tables were full of everyday Mandarin characters (整/贼/老/啥/咋/啦/儿…) and Henan's marker list had the bare "中" — a few Mandarin sentences were enough to drift the pet into a Northeastern/Henan accent. Tables now hold only dialect-exclusive strong words + accent switching picks the best count, so Mandarin stays Mandarin while all 7 dialects still detect & speak
- **🎈 Work badge → overhead bubble**: the pet's work badge (e.g. "chatting with XX") moved from a rectangle **below** the avatar to a **speech bubble above its head**: white rounded body + teal outline + little tail + soft shadow
- **🛠 Unresponsive team features + empty skill library fixed (long-standing)**: a packaging flaw meant the `pasm` engine package was **never bundled** — team / math brain / self-repair had been **silently dead in every installer since v0.24**; and since 0.28.2 the built-in skill library data went missing too. Rebuilt from the fixed spec: team features fully work in installers for the first time, and all 8 built-in skills are back

### v0.28.2 (2026-09-11) · Self-designed pet behavior + work status + dialects for all

- **🧠 Self-designed, self-optimized pet behavior**: the desktop pet's actions are **no longer hard-coded** — unlocked by growth stage (lvl 0: wave/hop/peek → lvl 1: ball → lvl 2: dance/spin → lvl 3: think), with base preferences **designed from its personality grid** (temper × energy × play). Praise it, poke it, or scold it and it **optimizes its own action weights** (ε-greedy exploration against rigidity), persisted to `pet_behavior.json` — survives restarts, upgrades, and personality changes
- **💼 Live work status**: while busy, the pet shows a real work label (📖 reading / 🌐 web self-study / 💬 chatting with you / or the actual task from the work ledger), with a focused look — fun actions yield to work automatically
- **🗣 Dialects upgraded to full parity**: Sichuan / Henan / Northeastern / Shandong / Beijing now equal to Cantonese & Taiwanese — dedicated detection regexes, signature words, and real neural dialect voices (edge-tts); Beijing falls back gracefully to Mandarin. Dialect words in your replies drive automatic TTS accent switching
- **🧮 (v0.28.0) Neural-symbolic reasoning layer + memory router**: math word problems routed by semantics — percentages / rope folds / rise-fall chains computed exactly by the symbolic layer, no more "wrong corrections"; memories injected by question meaning
- **🔍 (v0.28.1) Full regression review**: Cantonese homophone input (宜家/既/吾/系) recognition fixed (root cause of "it got dumber"); "dumber / dialects broken / TTS degraded" investigated and fixed one by one, guarded by 11 Mandarin negative samples (zero false positives)

### v0.27.1 (2026-09-09) · Real system ops + capability broker + adaptive delegation

- **🛠 Real system operations**: "delete the Screenshots folder on my desktop" → actually deleted (**moved to Recycle Bin**, recoverable); "clean up junk" → scan-only report first → confirm dialog → cleans only whitelisted temp dirs, files older than 2 days; locked files skipped and counted honestly. Windows / Program Files / drive roots are hard-refused
- **🧭 Capability broker (non-prescriptive)**: every request is assessed — direct tool (DO) / learned knowledge applied to really do it (LEARNED) / learn online first then really do it (LEARN) / honestly cannot with the real reason (NO, e.g. shutdown needs physical action). **It never fabricates "done" anymore**
- **🧬 Adaptive delegation**: easy tasks done by the main agent directly; complex ones split into sub-steps with a per-step ledger and main-agent review; destructive actions executed by the main agent only, always with user confirmation
- **📋 Operation ledger**: every real action logged (time / target / why / result); ask "did you really delete it? how do I verify" → it shows the ledger
- **🛡 Anti-hallucination**: honesty rule injected into system prompt — no claiming "done" without a real executed result

### v0.27.0 (2026-09-09) · Math brain + multimodal + quantum strategy + four trend items

- **🧮 Math brain mathlab**: "analyze this table" → real computation: regression (R²) / descriptive stats + outliers / correlation / linear equations / topo sort / shortest path / connected components / Markov steady state — with a one-line Chinese conclusion
- **👁 Multimodal perception (Phase B)**: drop an image or paste a screenshot into the chat → a local Ollama vision model describes it (no cloud, zero cost); the next message understands the image. Speech-to-text chain: faster-whisper → Vosk fallback
- **⚛ Quantum strategy (Phase C)**: candidate strategies form a superposition; Boltzmann-annealed measurement picks the route — exploratory early, converging to the best path with experience; intra-family interference generalizes lessons; mood (PAD) acts as an external field; outcomes feed back into amplitudes
- **🔀 Model router**: chat → local Ollama (fast), work → DeepSeek (strong), review → local cross-check; auto-fallback to cloud when local is down
- **🔁 Team self-check retry + 🕸 topo validation**: a failed/empty/short step auto-retries with a quality self-check; flows support `deps` dependencies — cycles / out-of-range / self-loops are rejected before running
- **🏗 Experience sedimentation + 🧠 project-level memory**: finished projects auto-save flow templates for one-click reuse; team runs auto-inject the project archive
- Smoke: mathlab 29 · phaseA 22 · trends 29 · bc+topo 29 · UI regressions 105 = **all green**

- **📎 Visible @references**: typing `@` pops a file picker (recent artifacts / knowledge entries / local text files; ↑↓+Enter pick, keep typing to filter). Choosing one inserts `@path` and shows a 📎 chip above the input — click ✕ to drop it (also cleans the text). `@“paths with spaces”` supported. File bodies go into the model context only; your bubble stays clean
- **🐛 "@小U got chit-chat" fixed**: unresolvable references are no longer sent verbatim to the model (it used to treat `@小U` as a greeting); pure-reference messages now explain and strip the mark. `@` in pasted code is never touched
- **🏢 Teams = real per-project crews**: a new project starts **blank** (crew & flow are built by you); each project has its own members — hire in (🌐 / 🎓 / ＋ / 🧑‍💼 pool), move out anytime (pool record kept); switching projects shows that project's own members / flow / **step status ✅⚠️○** / **run archive with outcomes**; runs write step status back automatically; "让团队做 X" still boots a built-in crew
- Smoke: full regression green (v0261 26 · v0262 22 · v0260 39 · v0250 33+33 · v0240 57+15 · v0231 26 · v022x/v0190)

## v0.26.2 (2026-09-08) · tool-error fix / PASM emotion & memory really shaping replies / same-file edits

- **🐛 Tool error fixed**: the capability menu's 8 quick-action anchors were missing a list wrapper → `str.join() takes exactly one argument (8 given)` crash is gone
- **❤️ PASM emotion & memory are now felt**: sentiment lexicon widened to 40+ spoken phrases (complaints, errors, "卡死" all move the mood); mood feedback is applied in one decisive step (no more dilution/clamping); mood is translated into a "此刻口吻 / tone-now" instruction injected into every reply (happy → light & playful, down → softer & shorter, urgent → straight to the point); tables/plans/PPT/dev now auto-carry your related memories & preferences
- **📄 Same-file precise re-edits**: "表格再加两行 / 文档补一节 / PPT 换配色" now edits **the same file** — the previous full content is fed back to the model (keep the theme & structure, change only what you asked), then written back in place; "另存/新做一份" opens a new one; product memory persists with the conversation
- Smoke: v0262 adds 18 checks, all green; v0261/v0260/v0250/v0240/v0231/v0230 full regression green

## v0.26.1 (2026-09-08) · five real-machine fixes

- **🩺 Health card compacted** to a small footer strip — the auto-activity feed is the main area again
- **👥 Team page redesigned into cards**: project bar → members / flow cards → run area; member rows show ★ rating, single-click opens the employee card (⭐ manual rating), right-click fires
- **🎓 3-tier skills now real**: built-in 🎯 experts ×2 (content director / data analyst) and 🔌 connectors ×3 (web search / local files & apps / office documents); the add-skill wizard asks for the tier
- **📂 App launching truly works**: "小U，帮我打开网易云音乐" now really opens it — name-prefix stripping + brand install-path probing across all drives + real process verification after launch (never fake "opened")
- **⏳ Long tasks never lose their reply**: real-machine root cause fixed — heavy work shows a "working…" notice, returning to the conversation shows the finished reply immediately, and turn-finalization has a hard fallback that always writes the reply back into its session file
- Smoke: v0261 adds 25 checks (UI offscreen + turn lifecycle + brand probing) all green; v0260/v0250/v0240/v0231/v0230 full regression green

## v0.26.0 (2026-09-08) · project ledger / real file development / @refs & slash commands

- **📒 Project ledger**: every project (copy/table/PPT/dev) keeps a working ledger — request history, what changed each round, smoke results, your standing rules; follow-up edits auto-inject the digest for precise changes, never "lost in the weeds"
- **🛠 Real file development**: `/开发 <spec>` or "用php/java/vue/go/c#/c++ 开发…" → pick a target folder and write a real project (8-language scaffolds, per-file generation, per-language static smoke); every round is recorded, "改这个项目" continues precisely
- **📎 @references**: `@file` or `@knowledge entry` in a message pulls the content into context
- **/️⃣ Commands**: `/开发 <spec>` · `/技能 <skill> <task>` · `/项目` (view ledger)
- (v0.25.0: company-style teams · faster chat · background self-heal — see CHANGELOG)
- **🧬 PASM core engine parity (0.5.0)**: multi-agent team & self-heal moved into the engine executive cortex (`pasm.cognitive`); desktop side is now a thin facade — one shared implementation
- **👥 Multi-agent skill team**: built-in role cards (PM/Planner/Dispatcher/Developer/Reviewer/Debugger), add your own "employee cards" and compose custom flows; say "let my team do X" in chat — review-fail auto-rework, auto-retry, fully visible steps
- **🩺 Self-check · self-diagnose · self-heal**: silent log sentinel records crash cases; file+line level diagnosis reports; report/auto-fix modes (auto-fix = patch → smoke regression → auto-rollback on failure; frozen builds forced to report-only)
- (v0.24.0: Ark 404 fix · theme hardening / v0.23.0: chat-only gate · queued concurrency · true PASM×LLM synergy — see CHANGELOG)

## What makes it different from a regular chatbot

A normal chatbot starts over every turn — no memory of you, no sense of "self".
PASM Studio is a **dual-brain architecture + cognitive execution cortex**:

| Part | Role | Result |
|---|---|---|
| **Language brain** (DeepSeek / any OpenAI-compatible / local Ollama) | Understands you, composes replies, asks back | Conversations that feel human |
| **PASM cognitive brain** (local, fully offline) | Every chat → emotional drift, personality shift, layered memory | It genuinely grows up and genuinely remembers you |
| **Cognitive execution cortex** (since v0.15) | Judges intent & mood before speaking → picks stance and tone; recalls relevant experiences by topic | No more "one-size-fits-all"; it remembers how things got done last time |

## Core capabilities (accumulated since v0.16)

**A chat UI that stays out of the way**
- **[💬 Chat] / [🔧 Work]** mode split: chat mode never starts working just because you *mentioned* "an app" or "a script" — you decide when it acts
- Large multi-line input (Enter to send / Shift+Enter for newline); switch LLMs anytime (cloud DeepSeek / any local Ollama model — remembered)
- **Session list** on the left: every message is saved in real time; switch sessions or restart freely

**Learning that sticks (knowledge base v3 + self-evolution)**
- Each knowledge entry = full text + key points + source; **what it learns, it keeps verbatim** — self-study searches multiple sources and stores full articles (since v0.16.5)
- Double-click any entry to open the full-text reader; keyword filtering supported
- Automatic self-study on by default, topics chosen by a built-in "curiosity selector"; after learning it quizzes itself and honestly tracks what it didn't understand

**Extensible skills**
- Drop new skills (name + description + procedure, standard `SKILL.md` format) into the data directory; they're auto-discovered and invoked when relevant
- Built-in examples: video scripting / marketing copy / manga one-take pipeline

**Layered memory**
- *Working memory*: current topic + goals in flight (persisted on exit)
- *Episodic memory*: experiences archived every 8 turns / per session, recalled by topic across sessions
- *Procedural memory*: after each task it records "asked → used → worked? → lesson", and remembers next time

**It actually works**
- Reads and summarizes files on your computer (txt/md/docx/xlsx/pdf/code…)
- Reads folders ("analyze the files in D:\some-folder"), opens apps (WeChat/Calculator/Notepad/PowerShell…)
- Writes & runs scripts; on errors it **auto-repairs and re-runs once** (plan–execute–verify loop)
- Generates PPT / Word / Excel documents; full-stack project development (frontend + backend + database)
- Generates real images (local Stable Diffusion WebUI or cloud engines), short videos, and manga episodes with subtitles and voice-over (HTML player + real MP4 when ffmpeg is present)

**It grows**
- Emotions shift with interaction, personality drifts with experience, and it develops from infancy to adulthood (the desktop avatar changes with it)

**Reliability**
- Windows 10/11 x64 minimum (Win7/8 dropped); startup errors are readable messages + `crash.log` instead of a mysterious flash
- ~87 MB installer; runs without torch (built-in lightweight cognitive core)

## Download & install

Grab the latest from **[Releases](https://github.com/arronJack/pasm-qclaw/releases)** (v0.30.13, single ~86.6 MB file):

1. Download `PASMStudio-Setup-0.30.13.exe`
2. Install → launch PASM Studio → open ⚙ Settings and enter an LLM key (or leave empty for local Ollama)
3. Start chatting. For real image/video/manga output, connect an image engine on first use (~1 minute)

> Requirements: **Windows 10/11 x64** · CPU is enough · ~1 minute to unpack on first install.
> Works with no API key at all (local demo mode: still remembers and grows, plainer speech).
> Full history in [CHANGELOG.md](CHANGELOG.md); the updater checks `latest.json` in this repo.

## LLM setup (one line each)

- **DeepSeek**: paste your API key; Base URL `https://api.deepseek.com/v1`, Model `deepseek-chat`
- **Free & local**: install [Ollama](https://ollama.com), run `ollama run qwen2.5:7b`; Base URL `http://127.0.0.1:11434/v1`, Model `qwen2.5:7b` — fully offline

## Data & privacy

- Memory / config / growth archives live in `%APPDATA%\PASMStudio` (not removed on uninstall; wipe anytime)
- Runs standalone; your conversations and files never leave your machine (only LLM API calls you configure go online)

## Roadmap

- [ ] **Self-reflection training loop**: distill real interactions into experience samples, feeding personality growth and light model fine-tuning
- [ ] Voice conversation polish / cloud sync / personality report home page

## Ecosystem

| Repo | Visibility | Role |
|---|---|---|
| **PASM** (private) | Private mirror on Gitee & GitHub | Core cognitive engine + cortex + full desktop source (development main repo mirror) |
| **pasm-qclaw** (this repo) | Public | PASM Studio releases: installers, update channel, docs |
| **PASM-Lite** (public, MIT) | Public | Minimal token-free cognitive engine for teaching (~260 lines, readable & hackable) |

The product is currently distributed as compiled binaries (public repos host builds and the update channel).
Open-source plan (drafted 2026-09): the **pasm-qclaw development repo is planned to open up** so everyone can study the desktop product and the dual-brain integration; the **full PASM engine + cortex stay closed for now**, pending further refinement. In the meantime, read the research concepts and teaching code in [PASM-Lite](https://github.com/arronJack/PASM-Lite).
Issues / feedback / collaboration: open an issue on GitHub ([issues](https://github.com/arronJack/PASM/issues) — primary tracker); the Gitee mirror is monitored too.

> **Trademark**: the name and branding of "PASM Studio" belong to arronZheng.
> The MIT license grants code rights, not trademark rights — please rename your forks
> and redistributions.
