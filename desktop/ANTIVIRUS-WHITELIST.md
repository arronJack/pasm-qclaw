# PASM Studio 杀毒软件误报处理指南

> 适用对象：下载或使用 PASM Studio 时被 360 安全卫士、腾讯电脑管家、火绒、
> Windows Defender（Microsoft Defender）等安全软件拦截或报"木马/风险程序"的用户，
> 以及发版时需要提交加白申诉的开发者。

---

## 一、为什么会被误报？（先了解，不慌）

PASM Studio 安装包**未做数字签名**时，杀软无法确认"发布者身份"，只能靠行为
启发式判断。以下特征会被加权怀疑（并非真的木马行为，但长得像）：

| 特征 | 原因 |
|---|---|
| 无数字签名、无版本信息 | 免费独立软件常态，但属于"来路不明"加分项 |
| Python 打包程序（PyInstaller） | 运行时解包加载，行为模式与"释放器"相似 |
| 监听麦克风 | 语音对话/唤醒词功能需要 |
| 修改本机文件 | 文件整理、清理等功能需要 |
| 自动检查更新 | 走官方升级通道，非恶意行为 |

**0.31.10 起**已注入完整版本信息并停用 UPX 压缩；加入代码签名证书后误报率
会降到接近零。在此之前若被拦截，按下面步骤处理即可。

## 二、普通用户：三步放行（以 360 为例）

1. **弹窗时**：选择「信任此文件」/「允许运行」，勾选"不再提醒"。
2. **已被隔离**：打开 360 →「木马查杀」→「恢复区」→ 找到 PASMStudio 相关条目
   →「恢复并信任」。
3. **加入白名单**：360 →「设置」→「文件白名单」→ 添加
   `PASMStudio.exe` 与 `PASMStudio-Setup-*.exe`。

**火绒**：拦截弹窗点「信任」；或「信任区」→ 添加文件。
**腾讯电脑管家**：「病毒查杀」→「信任区」→ 添加文件。
**Windows Defender**：「病毒和威胁防护」→「保护历史记录」→「允许」；
或「排除项」→ 添加安装目录。

⚠️ 请务必只从官方渠道下载：**Gitee / GitHub / GitCode 的 Releases 页面**。
来路不明的"破解版""绿色版"不在此列。

## 三、开发者：误报申诉加白（免费，治标）

提交申诉前先跑 [virustotal.com](https://www.virustotal.com) 看清是哪家引擎报、
报什么名目（Trojan.Generic / Heur / 行为引擎），对症申诉：

| 引擎 | 申诉入口 | 说明 |
|---|---|---|
| 360 | open.soft.360.cn（软件开放平台） | 软件认证/加白，免费，1~3 天 |
| 火绒 | 邮件 submit@huorong.cn | 附安装包 + 说明，响应快、对独立开发者友好 |
| 微软 Defender | microsoft.com/wdsi/filesubmission | 说明为误报，附文件 |
| 腾讯电脑管家 | 哈勃分析 habo.qq.com 检测 + 管家申诉入口 | 先拿行为报告再申诉 |
| SmartScreen | 签名后自动积累；无签名靠下载量积累信誉 | EV 证书可立即解决 |

## 四、根治：代码签名证书（选型速查）

| 选项 | 价位 | 效果 |
|---|---|---|
| Certum 开源开发者证书 | ~€69/年 | OV 级，个人开发者性价比首选 |
| SSL.com OV / EV | OV ~$179/年、EV ~$300+/年 | EV 让 SmartScreen 立即有信誉 |
| 阿里云/沃通/CFCA | 数百~数千元/年 | 国内购买实名方便，可开票 |
| GlobalSign/DigiCert EV | 数千元/年 | 顶级信誉，适合公司化 |

**本仓库已接线**（有证书后两条命令即可生效）：

1. **主 exe**：版本信息已由 `desktop/build_common.py` 自动注入
   （`--version-file desktop/version_info.txt`，发版时与 `appinfo.py` 同步改版本号）。
2. **安装包**：`desktop/installer.iss` 已内置签名开关——注册签名工具后：
   ```
   ISCC /DAppVersion=x.y.z /DSignToolName=signtool desktop/installer.iss
   ```
   即对安装包与卸载器做 Authenticode 签名；不传 `/DSignToolName` 时与旧流程完全一致。

## 五、发版自查清单（新增）

- [ ] `appinfo.py` 与 `version_info.txt` 版本号一致
- [ ] 主 exe 有 VERSIONINFO（右键属性→详细信息非空）
- [ ] 未启用 UPX（`--noupx`）
- [ ] virustotal 检测，记录误报引擎与名目
- [ ] 各杀软申诉/加白已提交
- [ ] （有证书后）安装包签名验证通过：`Get-AuthenticodeSignature` 返回 Valid
