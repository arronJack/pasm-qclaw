# 杀软加白申诉提交材料包（0.31.10 起用此包提交）

> 0.31.10 已注入完整 VERSIONINFO + 停用 UPX，误报概率显著下降；
> 本材料包各表单内容可直接复制粘贴，提交前先跑 VirusTotal 摸底。

## 一、产品信息（各平台通用）

| 字段 | 内容 |
|---|---|
| 软件名称 | PASM Studio（智能助手"小U"） |
| 版本号 | 0.31.10 |
| 开发者/版权 | arronZheng（Copyright (C) 2026 arronzheng. MIT License.） |
| 软件类型 | 本地优先的 AI 晌面工作台（桌面宠物 + AI 工具集） |
| 官网/说明页 | https://gitee.com/arronzheng/pasm-qclaw |
| 下载渠道 | Gitee / GitHub / GitCode 三平台 Release（见下） |
| 是否开源 | 核心引擎开源（MIT），可提供源码仓库链接 |
| 是否联网 | 本地优先；可选联网能力（用户显式开启），无静默上传 |
| 高危行为 | 无：无开机自启注册表写入、无浏览器劫持、无驱动加载；自动更新走 Inno 标准通道且经用户确认 |

### 产物指纹（提交时附上，便于厂商精准加白）

| 文件 | 大小 | SHA256 |
|---|---|---|
| `PASMStudio-Setup-0.31.10.exe`（安装包） | 200,376,452 B（191.1 MB） | `359273CBBFDCFDB0577E2C071BEDB0CA00C0E7AEC7E33906C7C5CB974D3ECEE1` |
| `PASMStudio.exe`（主程序，解包于安装目录） | 15,977,352 B | 发版后用 `sha256sum dist_v03110/PASMStudio/PASMStudio.exe` 补填 |

## 二、第一步：VirusTotal 摸底（提交申诉前必做）

1. 打开 https://www.virustotal.com → 上传 `PASMStudio-Setup-0.31.10.exe`
2. 记录：报毒引擎名单、报毒名目（Trojan.Generic / Heur / Wacatac 等）
3. **只对报毒的厂商逐家申诉**，没报的不用管
4. 申诉材料里主动附上 VT 链接，展示"仅 N/70 家启发式误报、主流引擎全绿"对申诉有利

## 三、各平台提交入口与表单内容

### 1. 360（优先级最高，用户基数最大）
- 入口：360 软件开放平台 https://open.soft.360.cn （注册开发者账号 → 提交软件认证/误报申诉）
- 免费审核 1~3 个工作日；通过后加入 360 白名单库，基本不再报
- 表单粘贴模板：
  > 软件名：PASM Studio v0.31.10；开发者：arronZheng（个人开源开发者）。
  > 本软件为开源（MIT）本地 AI 桌面工具，使用 PyInstaller 打包 + Inno Setup 安装程序，
  > 主程序已携带完整 VERSIONINFO（公司/产品/版本），未加壳、无自启、无静默联网。
  > 疑似误报，申请加入白名单。SHA256：<上表值>。VT 链接：<填>。下载：https://gitee.com/arronzheng/pasm-qclaw/releases

### 2. 火绒（对独立开发者最友好，邮件申诉）
- 邮箱：`submit@huorong.cn`
- 邮件模板：
  > 主题：【误报申诉】PASM Studio v0.31.10 安装包被误报
  > 正文：您好，我是 PASM Studio（开源桌面 AI 工具，MIT 协议）的独立开发者 arronZheng。
  > 最新版 0.31.10（附件/下载链接）被贵司引擎误报为 <填报毒名>。
  > 软件使用 PyInstaller + Inno Setup 打包，已注入完整版本信息、未加壳、无高危行为，
  > 源码仓库：https://gitee.com/arronzheng/pasm-qclaw 。烦请复核加白，谢谢！
  > SHA256：<上表值>
- 火绒通常 1 个工作日内回复，响应最快

### 3. 微软 Defender / SmartScreen
- 误报提交：https://www.microsoft.com/wdsi/filesubmission （选"Software developer"，可写开发者备注）
- SmartScreen 信誉需签名证书或下载量积累，短期靠用户"仍要运行"

### 4. 腾讯电脑管家
- 哈勃分析：https://habo.qq.com （先自测）
- 管家误报申诉：https://guanjia.qq.com → 帮助与反馈 → 误报申诉

## 四、提交节奏建议

1. **今天**：VirusTotal 摸底 → 火绒邮件（最快见效）+ 360 平台提交
2. **发版后**：0.31.10 Release 上线、公开下载链接可用 → 把链接补进 360/管家的表单重提一次（有公开下载渠道信誉更高）
3. **长期**：等证书预算到位（Certum ~€69/年性价比首选）签上 Authenticode，一劳永逸——签好后把证书指纹补进本文件存档

## 五、发版自查清单（每次发版过一遍）

- [ ] `appinfo.py` 与 `desktop/version_info.txt` 版本号一致（本次均为 0.31.10）
- [ ] 全新 dist 目录构建（避开本机删除守卫）
- [ ] 主 exe VERSIONINFO 五字段回读非空
- [ ] 冻结冒烟 12s 存活
- [ ] Inno 编译成功且安装包带版本信息
- [ ] 两仓同频 + 六端推送
- [ ] Release 附件按渠道：GitHub/GitCode 整包 exe；Gitee >100MB 走 DiskSpanning 分卷（`-gitee` 切片）
