# 更新日志

本项目使用[语义化版本](https://semver.org/lang/zh-CN/)：`主版本.次版本.修订号`。
日期为发布当天，所有时间以项目维护者所在时区为准。

## [未发布]

## [1.2.2] - 2026-10-31

### 新增

- 新增认主插件 `astrbot_plugin_dafeiyu_owner`：
  - 大号私聊机器人发送 `/认主 <QQ>` 绑定主人
  - 校验发送者 QQ 和填写的 QQ 是否一致
  - 自动写入 `social` / `ripple` / `memory2` / `heart` 的主人配置
  - 自动把大号加入 AstrBot 的 `admins_id`
  - 默认自动重启 AstrBot 让配置生效
  - `data/owner.json` 记录绑定，防止其他 QQ 覆盖
- 安装脚本和 `INITIAL_CREDENTIALS.txt` 增加「机器人用小号、大号认主」提示

### 变更

- 插件列表、命令手册、配置文档、隐私说明同步认主流程
- Windows 部署指南增加「绑定主人」步骤

### 升级注意

- 已有部署可以运行 `bash scripts/update.sh` 获取插件
- 更新后不会自动绑定，需要大号私聊机器人发送 `/认主 你的QQ`
- 之前手动配置过 `admin_qq` / `main_umo` 的部署，可以继续手动配置，也可以用 `/认主` 重新同步

## [1.2.1] - 2026-10-31

### 文档

- README、Windows 部署指南和部署脚本说明增加「建议让 AI 协助部署」的提示
- 安装脚本结束时提示可以用 AI 排错，并提醒不要发送真实密钥、QQ 号或聊天记录

### 升级注意

- 无功能、配置或数据结构变更
- 已有部署不需要更新；新用户下载最新版即可

## [1.2.0] - 2026-10-31

Windows 本地部署版本。

### 新增

- Windows 原生 PowerShell 脚本：
  - `scripts/install.ps1`：检查 Docker Desktop、创建目录、生成配置、构建启动、保存初始账号密码
  - `scripts/update.ps1`：git 快进或压缩包同步，支持 `-Check`、`-NoRestart`、`-Stash`
  - `scripts/uninstall.ps1`：停止容器，支持 `-Purge`、`-RemoveImages`
- `docker-compose.windows.yml`：去掉 `/etc/localtime`、`/etc/timezone` 挂载，时区由 `TZ` 控制
- `docs/WINDOWS.md`：Docker Desktop + WSL2 本地部署完整指南
- Git Bash 下 `install.sh` / `update.sh` / `uninstall.sh` 会自动切换到 Windows compose
- PowerShell 安装同样会生成 `data\INITIAL_CREDENTIALS.txt`，并尝试收紧文件权限

### 变更

- README 增加 Windows 本地部署入口和仓库结构说明
- `scripts/README.md` 增加 Windows 脚本说明
- 更新脚本同步 `docs/` 和 `docker-compose.windows.yml`

### 升级注意

- 无破坏性变更
- Windows 用户建议直接运行 `scripts\install.ps1`
- Linux / 云端部署继续使用 `scripts/install.sh`，不受影响

## [1.1.0] - 2026-10-31

安装体验与首次登录引导版本。

### 新增

- 安装脚本增加中文分步提示：创建目录、生成配置、构建、启动、收集账号密码，每一步都有说明
- 安装完成后自动把初始账号密码写入 `data/INITIAL_CREDENTIALS.txt`（权限 600），并在终端提示路径
  - AstrBot 用户名 / 初始密码
  - SnowLuma 用户名 / 初始密码
  - noVNC 密码
  - 忘记密码时的重置命令
  - 服务器部署的 SSH 隧道命令
- 首次安装时如果没改 `.env` 里的 VNC 占位密码，自动生成随机密码，避免漏改
- 安装、更新、卸载脚本增加权限预检：
  - 目录不可写时提示 `sudo bash scripts/...`
  - Docker 无权限时提示 sudo 或加入 docker 组
- 用 sudo 安装后自动把 `data/` 和 `.env` 的所有权交还给 `SUDO_USER`

### 变更

- `.env.example` 改为中文说明
- 安装结束提示改为中文的首次使用顺序
- 更新脚本在构建前检查 Docker 权限，避免构建到一半才报错
- `scripts/README.md` 补充安装流程、初始密码文件和权限问题说明

### 文档

- README 增加首次登录账号说明和权限问题处理
- PRIVACY 补充 `INITIAL_CREDENTIALS.txt` 的处理方式

### 升级注意

- 无破坏性变更
- 已经部署过的用户不会重新生成 `INITIAL_CREDENTIALS.txt`；需要时可以删除该文件后重新运行安装脚本，或按文档手动重置密码

## [1.0.0] - 2026-10-31

首个公开版本，整理自个人云端部署。包含完整可运行的插件、部署骨架、人格模板和文档，
不包含任何个人账号、聊天记录、记忆数据库或密钥。

### 对话与人格

- DeepSeek 文本对话，支持自定义 Provider
- 完整的大肥鱼人格模板，可导入 AstrBot
- 按中文句号分段发送，段间随机间隔，模拟自然聊天节奏
- 群聊 / 私聊 / 唤醒词由 AstrBot 平台配置控制
- 心情影响说话方式与表情选择

### 语音

- 火山引擎豆包 Seed-TTS（声音复刻 2.0）
- 自定义克隆音色
- 默认只回文字，`/语音开` 后文字 + 语音
- `/语音关`、`/语音状态`
- 支持用 FeiyuVoice 参考音色重新训练云端音色

### 记忆系统

- **LivingMemory**：长期记忆、反思总结、向量检索
- **记忆 2.0**：日记 2.0、主人画像、短期日程表、凌晨结算
- **涟漪记忆**：日常碎事的衰减记忆池
  - 按类似艾宾浩斯曲线衰减：新鲜 → 中部 → 沉底
  - 只有中部的小事会被关键词或重复 4 个字勾起
  - 想起时自动模糊时间、数字和细节
  - 沉底的小事重新被提到会勾回中部，下一次才有机会想起
  - 当天入池当天不触发
  - 同一会话 90 分钟冷却
  - 内置约 100 个屏蔽词，支持特殊触发词
  - 每天 03:00-06:00 自动摘录，也支持 `/涟漪 摘录`、`/涟漪 记`
- **上下文折叠**：压缩长上下文，降低 token 消耗

### 心脏模块

- 关系认知
- 心情底色与动态心情
- 心绪手札
- 心情影响记忆召回和回复风格

### 社交与媒体

- 100 张 Feiyu 风格 GIF 表情包，按心情和语境匹配
- 自动表情包：30% 触发，单次最多 2 条，不会连续 3 条
- 自动原生表情：约 50% 触发，可跟随最后一段文字或独立发送
- `/发图`、`/表情`、`/表情包`、`/表情包查`
- **相册**：自动保存主人私聊照片，支持一句话备注、关键词查找、按编号取回
- **QQ 空间**：手动 / 自动动态，公开、好友、私密三种权限
- 自动动态读取前一天 LivingMemory 重要内容，发布前做隐私词过滤

### 部署与运维

- Docker Compose 管理 AstrBot + SnowLuma
- SnowLuma 使用真实 Linux QQ，支持 OneBot v11
- WebUI 和 noVNC 只绑定 `127.0.0.1`，通过 SSH 隧道访问
- Ollama 只监听 Docker 网关，不直接暴露公网
- 日志轮转、非 root 容器、seccomp / ptrace 按 SnowLuma 要求配置
- `scripts/install.sh`：首次安装
- `scripts/update.sh`：从 GitHub 自动同步
  - git clone 走快进合并
  - zip 下载走压缩包同步
  - 支持 `--check`、`--no-restart`、`--stash`、`--auto`
  - 始终保留 `data/` 和 `.env`
  - 旧文件备份到 `backups/update-<时间>/`
- `scripts/uninstall.sh`：一键卸载
  - 默认只删容器，保留数据
  - `--purge` 连数据一起删
  - `--remove-images` 删除项目镜像

### 文档与合规

- `README.md`：功能、技术栈、快速开始
- `ARCHITECTURE.md`：组件边界、消息流、数据流
- `COMMANDS.md`：完整命令手册
- `PRIVACY.md`：数据边界与提交前检查
- `DISCLAIMER.md`：个人非商业使用与侵权处理
- `THIRD_PARTY_NOTICES.md`：第三方组件与素材许可
- `scripts/README.md`：更新、自动同步、卸载说明

### 数据与隐私

发布包明确不包含：

- API Key、token、密码、VNC 密码、SSH 私钥
- QQ 号、管理员账号、UMO
- 聊天记录、`data_v4.db`、LivingMemory 数据库
- 日记、画像、日程、心绪手札、涟漪池
- 相册照片和备注
- 登录二维码、语音样本、服务器 IP、本机路径

### 已知限制

- 需要 Linux + Docker，Windows 请使用 Git Bash 或 WSL 运行脚本
- SnowLuma 首次登录需要扫码，登录态只保存在本地 `data/`
- 语音合成、向量模型、DeepSeek 需要自备账号或本地服务
- 项目与腾讯、DeepSeek、火山引擎、AstrBot、SnowLuma 均无隶属或官方授权关系
- 第三方角色、声音、表情包素材仅建议个人非商业使用

---

首个版本没有破坏性变更。
