# 更新日志

本项目使用[语义化版本](https://semver.org/lang/zh-CN/)：`主版本.次版本.修订号`。
日期为发布当天，所有时间以项目维护者所在时区为准。

## [未发布]

## [1.5.0] - 2026-11-01

### 新增

- 在线音乐接入方案：插件市场「我想听歌！」0.1.3 /「bili播放器」0.2.5
  - 自然语言点歌，或 `搜索歌曲 <关键词>` 后回复序号
  - 语音优先，失败回退音频文件
  - 支持 `1 下载`、BV/AV 号和 B 站视频链接
- B 站 412 兼容补丁 `scripts/patch_music_plugin.py`
  - 自动添加匿名设备 Cookie（`buvid3` / `buvid4` / `b_nut` / `b_lsid` / `_uuid`）
  - 遇到 HTTP 412 自动换 Cookie 重试一次
  - 同时支持 0.1.3 和 0.2.5，幂等、可重复运行
  - 修改前备份原文件，修改后做 Python 语法检查
- 完整的音乐文档 `docs/MUSIC.md`
  - 在线 B 站音乐与本地音乐库的分工
  - Windows、Linux、Docker、原生 AstrBot 的安装和补丁步骤
  - B 站登录、命令、限制、排查、更新和卸载
- README 增加下载、Windows/Linux 安装、更新、卸载的完整步骤和提示
- 命令手册增加在线音乐命令和两套工具名对照

### 变更

- 在线音乐和本地音乐统一为一条稳定链路：
  - 在线：`搜索歌曲` / 自然语言 / 序号选择
  - 本地：`/本地点歌`、`/本地听歌`、`/本地歌单`、`/本地分享`
- 文档明确只保留一个在线音乐插件，避免 0.1.3 和 0.2.5 同时注册 `搜索歌曲`
- README、ARCHITECTURE、THIRD_PARTY_NOTICES、PRIVACY、scripts/README、
  docs/WINDOWS、docs/INCREMENTAL 同步更新

### 修复

- 修复 B 站未登录请求返回 HTTP 412，导致部分歌曲被误判为“找不到”
  - 实测《永世的摇篮》从 0 个候选恢复到 8 个
  - 《晴天》10 个、《夜曲》6 个、《后来》9 个、《Lemon》9 个
- 修复本机残留的 `bili播放器` 0.2.5 仍显示在 WebUI 的问题
  - 目录删除后需要重启 AstrBot 才能卸载内存中的旧插件
- 本地音乐命令继续使用 `/本地*`，不再和在线插件抢 `/点歌`

### 升级注意

- 不需要迁移数据库或本地音乐库
- 已经安装在线音乐插件的部署，升级后请运行一次：

  ```bash
  docker cp scripts/patch_music_plugin.py astrbot:/tmp/patch_music_plugin.py
  docker compose exec astrbot python /tmp/patch_music_plugin.py --path /AstrBot/data
  docker compose restart astrbot
  ```

- Windows 使用 `docker-compose.windows.yml` 和 PowerShell 对应命令
- 只保留一个在线音乐插件；如果同时装了 0.1.3 和 0.2.5，请在插件管理里卸载一个
- 在线插件在 AstrBot 里更新或重装后，补丁可能被覆盖，需要重新运行补丁脚本

### 安全

- 仓库不包含 B 站 Cookie、二维码、账号密码或登录态
- B 站 Cookie 只填在插件配置/登录页，不要发到聊天、Issue 或仓库
- 本地音乐文件不建议提交到公开仓库，版权归各自权利人
- 在线音乐仅用于个人学习、研究和个人非商业使用

## [1.4.1] - 2026-11-01

### 变更

- 本地音乐命令改名，避免和插件市场「我想听歌！」抢 `/点歌`
  - `/点歌` → `/本地点歌`
  - `/听歌` → `/本地听歌`
  - `/歌单` → `/本地歌单`
  - `/分享歌曲` → `/本地分享`
- LLM 工具改名：
  - `ears_play` → `local_music_play`
  - `ears_search` → `local_music_search`
- 文档说明：
  - 在线/B 站点歌用「我想听歌！」插件
  - 本地音乐库用 `/本地*` 命令
  - 两套工具和命令互不冲突

### 升级注意

- 本地音乐库和配置文件不需要迁移
- 更新后旧命令 `/点歌`、`/听歌`、`/歌单`、`/分享歌曲` 不再由耳朵提供
- 如果安装了其他 `/点歌` 插件，可以直接共存

## [1.4.0] - 2026-11-01

### 新增

- 抽屉插件 `astrbot_plugin_dafeiyu_drawer`
  - 自动收藏主人相关表情包：主人私聊、主人在群里发的 GIF/小图
  - 按 sha256 去重，默认单张 1.5MB、最多 300 张
  - 主人说「喜欢/不喜欢/几点睡/习惯/过敏/生日/叫我/记住」时当场记录
  - 主人私聊时注入相关小事；群聊不注入，避免泄露
  - `/抽屉`、`/抽屉 找`、`/抽屉 记`、`/抽屉 删`、`/抽屉 表情包`
  - 工具 `drawer_remember`、`drawer_search`、`drawer_sticker`
- 耳朵插件 `astrbot_plugin_dafeiyu_ears`
  - 本地音乐库：mp3/flac/m4a/wav/ogg/aac/opus
  - `/点歌 <关键词>`、`/听歌`、`/歌单`、`/分享歌曲`
  - 默认发文件，可切换语音条或音乐卡片
  - 可选 Meting 在线搜索，默认关闭
  - 主人分享 QQ 音乐卡片时，注入歌名给模型

### 变更

- README、命令手册、体系结构和隐私说明增加抽屉与耳朵
- 增量注入会自动带上两个新插件

### 升级注意

- 新插件随 `update.sh` / `inject.sh` 自动安装，不需要迁移数据
- 耳朵音乐库默认为空，需要自己把音频放进 `data/plugin_data/astrbot_plugin_dafeiyu_ears/music/`
- 抽屉只收主人相关消息；未配置主人时私聊会被当作主人
- 在线音乐接口和版权由使用者自行确认

## [1.3.0] - 2026-11-01

### 新增

- 增量安装脚本：
  - `scripts/inject.sh`：Linux 注入到已有 AstrBot + SnowLuma
  - `scripts/inject.ps1`：Windows 注入
  - 只补插件、补丁、人格模板和配置示例
  - 不修改 `data/config`、`data/cmd_config.json`、`.env` 和用户 compose
- 注入前自动备份到 `backups/inject-<时间>/`
- 注入清单 `.dafeiyu-layer.json`，记录版本、提交、插件列表
- 注入后健康检查，失败自动恢复插件/补丁/人格/配置并重启
- 默认锁定 AstrBot 和 SnowLuma 镜像摘要，避免 `latest` 更新破坏核心补丁
- 两个容器增加 Docker healthcheck
- 更新脚本增加更新前快照、健康检查和失败自动回滚

### 变更

- `Dockerfile.astrbot` 使用 `ASTRBOT_BASE_IMAGE` 构建参数，默认锁定摘要
- compose 使用 `ASTRBOT_BASE_IMAGE` / `SNOWLUMA_IMAGE`，可在 `.env` 主动升级
- `update.sh` / `update.ps1` 更新前备份 `data/config`、`cmd_config.json`、`.env`、compose、Dockerfile、插件和补丁
- 更新后轮询 AstrBot WebUI，失败自动恢复旧镜像和文件

### 升级注意

- 已有部署可以直接运行 `bash scripts/update.sh` 或 `scripts\update.ps1`
- 只想补大肥鱼层、不重建整套部署时，用 `scripts/inject.sh` / `inject.ps1`
- 锁定镜像后不会自动跟随 `latest`；需要主动升级时在 `.env` 设置新的镜像 tag 或 digest
- 回滚镜像保留为 `dafeiyu-astrbot:rollback-<时间>`，确认稳定后可手动清理

## [1.2.5] - 2026-10-31

### 文档

- 认主排查说明：认主后仍不认识主人时，先用 `/reset` 清除上下文，再问一次
- 如果仍不生效，把 `/认主 状态` 的输出和报错发给 AI 求助
- 提醒不要把密码、API Key、完整 QQ 号或 `data/` 内容发给 AI

## [1.2.4] - 2026-10-31

### 修复

- 认主后 AI 不知道当前主人是谁：认主插件现在会在 LLM 请求时注入主人身份
  - 主人私聊：模型知道当前是已确认主人，可以自然称呼“主人”
  - 主人在群里：模型知道发言者是主人，但被要求不向群友透露身份和私聊内容
  - 非主人：不注入，避免把普通朋友误认成主人
- 注入只写 system prompt，不写入聊天记录或日志

### 升级注意

- 更新后不需要重新认主；只要 `data/owner.json` 存在就会生效
- 如果之前没有认主或 `owner.json` 丢失，重新 `/认主` 并设置密码

## [1.2.3] - 2026-10-31

### 新增

- 认主支持换主密码：
  - `/认主 密码 <密码>` 设置换主密码
  - `/认主 改密码 <旧密码> <新密码>` 修改密码
  - `/认主 更换 <新QQ> <密码>` 验证密码后更换主人
  - `/认主 状态` 查看当前主人和密码状态
- 换主时自动从 `admins_id` 移除旧 QQ，并重写四个插件的主人配置
- 密码使用 PBKDF2-HMAC-SHA256 + 随机盐保存哈希，常量时间比较
- 连续输错 5 次锁定 10 分钟

### 变更

- 安装提示、初始密码文件、README、Windows 指南、命令手册同步换主流程
- `allow_rebind` 标记为废弃，换主统一走密码验证

### 升级注意

- 已有部署更新后，需要当前主人发送 `/认主 密码 你的密码` 设置换主密码
- 未设置密码时不能使用 `/认主 更换`；旧号无法登录时仍走服务器手动重置

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
