# 大肥鱼 QQ 机器人 · 云端版

> **使用声明**
>
> 本项目仅供个人学习、研究和个人非商业使用，不提供商业授权。
> 项目中涉及的第三方角色、声音、表情包、模型、代码和素材版权归各自原作者所有。
> 如权利人认为本项目内容侵权，请通过本仓库 Issue 联系，核实后将立即删除相关内容。
> 本项目与腾讯、DeepSeek、火山引擎、AstrBot、SnowLuma 等不存在隶属或官方授权关系。
>
> 详细声明见 [DISCLAIMER.md](DISCLAIMER.md)。

**当前版本：v1.2.0** · [更新日志](CHANGELOG.md) · [发行版](https://github.com/Automaticfly/emotional-dafeiyu-qq-chat-ai/releases) · [发版流程](RELEASING.md)

一只住在 QQ 里的蓝色鲸鱼娘。她可以聊天、发语音、发表情包、记住重要的事，
也可以在 QQ 空间写一条不泄露隐私的日常动态。

这个仓库整理的是当前可运行版本的**功能代码、插件和部署骨架**，不包含任何
个人 API Key、QQ 号、聊天记录、记忆数据库、登录二维码或服务器凭据。

## 功能

### 对话与人格

- DeepSeek 文本对话
- 自定义人格提示词
- 按中文句号分段发送，模拟自然聊天节奏
- 根据当前心情附加 QQ 原生小黄脸
- 表情包按心情、语境和氛围自动匹配
- 群聊 / 私聊 / 唤醒词由 AstrBot 平台配置控制

### 语音

- 火山引擎豆包 Seed-TTS（声音复刻 2.0）
- 自定义克隆音色
- 当前会话默认只回文字，发送 `/语音开` 后文字 + 语音
- `/语音关`、`/语音状态`
- 支持使用 FeiyuVoice 参考音色重新训练云端音色

### 长期记忆

- LivingMemory 长期记忆插件
- Ollama `bge-m3` 1024 维向量检索
- 记忆重要性、时间衰减、反思总结
- 按会话隔离，避免把不同私聊/群聊的记忆混在一起

### 记忆 2.0

- 日记 2.0：模糊化、延长、升档/降档、搁置、回收站
- 主人画像
- 短期日程表
- 定时结算窗口

### 涟漪记忆

- 像日常琐事一样会变淡的记忆池
- 按类似艾宾浩斯曲线衰减：新鲜 → 中部 → 沉底
- 只有落在曲线中部的小事，被关键词或重复 4 个字撞见时才会被模糊地想起
- 沉底的小事被重新提到（重复字数够或语义相近）会勾回中部，下一次才有机会想起
- 想起时自动模糊时间、数字和细节，只保留「前两天 / 前阵子 / 好久以前」的印象
- 当天入池的事当天不触发
- 同一会话 90 分钟冷却，避免一直翻旧账
- 内置一整套屏蔽词（就是、今天、早上、吃饭等），也支持自定义特殊触发词
- 深夜自动摘录，也可以让她在聊天中自己记一件小事

### 心脏模块

- 关系认知
- 心情底色与动态心情
- 心绪手札
- 心情影响记忆召回和说话方式

### 媒体与表情包

- `/发图`
- `/表情`
- `/表情包`
- 100 张 Feiyu 风格 GIF 表情包
- 自动表情包：30% 触发，单次最多 2 条，永不连续 3 条
- 自动原生表情：约 50% 触发，其中一半跟随最后一段文字，一半独立发送
- 表情包/表情/图片独立成消息，不塞进文字气泡

### 相册

- 主人发来的照片自动收进相册
- 每张照片可以顺手补一句备注
- 按备注关键词翻找照片
- 可以让她把某张照片再发回来
- 按会话隔离，群友发图不会自动入库

### QQ 空间动态

- 手动发布文字/多图动态
- 权限支持公开、好友、私密
- `/动态查` 查询最近说说
- `/动态删` 删除最新动态
- `/动态预览` 预览自动生成的动态
- 每日自动动态：读取前一天 LivingMemory 的重要记忆，隐私过滤后发布
- 默认每天 09:30，默认好友可见，必要时可配置为私密

### 部署与安全

- Docker Compose 管理 AstrBot + SnowLuma
- SnowLuma 使用真实 Linux QQ，支持 OneBot v11
- WebUI 和 noVNC 只绑定 `127.0.0.1`，通过 SSH 隧道访问
- Ollama 只监听 Docker 网关，不直接暴露公网
- 日志轮转、非 root 容器运行、seccomp/ptrace 按 SnowLuma 要求配置

## 技术栈

| 层 | 技术 |
|---|---|
| 机器人框架 | AstrBot 4.29.3 |
| QQ 协议端 | SnowLuma（Linux QQ + Xvfb + noVNC + OneBot v11） |
| 对话模型 | DeepSeek |
| 语音合成 | 火山引擎豆包 Seed-TTS / 声音复刻 2.0 |
| 语音音色 | FeiyuVoice-CosyVoice3 参考音色（云端复刻版本） |
| 向量模型 | Ollama `bge-m3` |
| 长期记忆 | LivingMemory |
| 插件语言 | Python 3.10+（运行时 3.12） |
| 数据 | SQLite / aiosqlite |
| 编排 | Docker Compose |
| 部署 | Ubuntu 22.04 / systemd / UFW / SSH 隧道 |

## 架构

```mermaid
flowchart LR
    U[QQ 用户] --> Q[QQ 客户端<br/>SnowLuma Linux QQ]
    Q <-->|OneBot v11<br/>反向 WebSocket| A[AstrBot]
    A --> D[DeepSeek API]
    A --> V[火山引擎 Seed-TTS]
    A --> P[自定义插件]
    P --> L[LivingMemory]
    L --> O[Ollama bge-m3]
    P --> Z[QQ 空间动态]
    Z --> Q
    M[管理员] -->|SSH 隧道| W[AstrBot WebUI]
    M -->|SSH 隧道| N[SnowLuma WebUI / noVNC]
```

更详细的组件边界、数据流和插件职责见 [ARCHITECTURE.md](ARCHITECTURE.md)。

## 仓库结构

```text
.
├── docker-compose.yml            # Linux / 云端部署
├── docker-compose.windows.yml    # Windows 本地部署（去掉 Linux 专用挂载）
├── Dockerfile.astrbot            # 带核心补丁的 AstrBot 镜像
├── patches/
│   ├── volcengine_tts.py         # 豆包 Seed-TTS v3 适配
│   ├── session_llm_manager.py    # 默认关闭 TTS，/语音开 开启
│   └── respond_stage.py          # 分段发送 + 内联原生表情
├── plugins/
│   ├── astrbot_plugin_dafeiyu_heart/
│   ├── astrbot_plugin_dafeiyu_memory2/
│   ├── astrbot_plugin_dafeiyu_ripple/
│   ├── astrbot_plugin_dafeiyu_context/
│   ├── astrbot_plugin_dafeiyu_social/
│   ├── astrbot_plugin_dafeiyu_moodface/
│   ├── astrbot_plugin_face_convert/
│   └── astrbot_plugin_livingmemory/
├── persona/
│   └── dafeiyu.md                # 已移除私人信息的公开人格模板
├── config/
│   ├── snowluma-onebot.example.json
│   ├── social.example.json
│   ├── ripple.example.json
│   └── CONFIGURATION.md
├── assets/stickers/dafeiyu/      # 100 张 GIF 及分类目录
├── docs/
│   └── WINDOWS.md                # Windows 本地部署指南
└── scripts/
```

## 快速开始

### 1. 准备环境

- Linux 服务器、Windows（Docker Desktop + WSL2）或本机 Docker
- Docker Compose
- 可选：Ollama、DeepSeek API、火山引擎 TTS、SnowLuma

### 2. 启动

```bash
bash scripts/install.sh
```

安装脚本会自动生成 `.env`、创建数据目录、构建并启动容器，并把初始账号密码保存到
`data/INITIAL_CREDENTIALS.txt`。权限不足时用 `sudo bash scripts/install.sh`。

首次启动后：

1. 打开 AstrBot WebUI：`http://127.0.0.1:6185`
2. 新建 OneBot v11 平台，建议 ID 使用 `napcat`
3. 反向 WebSocket 地址填 `0.0.0.0:6199`，设置一个 token
4. 配置 DeepSeek、TTS、Ollama 等 provider
5. 按 [CONFIGURATION.md](config/CONFIGURATION.md) 配置插件管理员和 UMO
6. 打开 SnowLuma WebUI：`http://127.0.0.1:5099`
7. 通过 noVNC 扫码登录 QQ
8. 将 SnowLuma 的 `onebot.json` token 改成与 AstrBot 平台 token 一致

### 3. 导入人格

把 [persona/dafeiyu.md](persona/dafeiyu.md) 的正文导入 AstrBot 人格设置。

### 4. 初始化记忆和向量模型

```bash
ollama pull bge-m3
```

并将 AstrBot 的 embedding provider 指向：

```text
http://host.docker.internal:11434
```

具体配置见 [CONFIGURATION.md](config/CONFIGURATION.md)。

## 安装、更新与卸载

### 安装

```bash
bash scripts/install.sh
```

安装脚本会自动：

1. 检查 Docker 和目录权限
2. 创建 `data/` 目录
3. 生成 `.env`；如果没改 VNC 占位密码，会自动生成一个随机密码
4. 复制表情包素材和 SnowLuma OneBot 配置
5. 构建并启动 AstrBot + SnowLuma
6. 把初始账号密码保存到 `data/INITIAL_CREDENTIALS.txt`

初始账号密码：

```text
AstrBot    用户名 astrbot，密码随机生成
SnowLuma   用户名 admin，密码随机生成
noVNC      密码在 .env 的 VNC_PASSWORD
```

三个密码都会写进 `data/INITIAL_CREDENTIALS.txt`（权限 600）。登录后请立即修改密码并删除该文件。

如果出现权限问题：

```text
permission denied
Cannot connect to the Docker daemon
```

用 sudo 重新运行：

```bash
sudo bash scripts/install.sh
```

或者把用户加入 docker 组：

```bash
sudo usermod -aG docker $USER
newgrp docker
```

### Windows 本地部署

Windows 上用 Docker Desktop（WSL2 后端）运行，不需要云服务器：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install.ps1
```

- 使用 `docker-compose.windows.yml`，去掉了 Linux 专用的 `/etc/localtime` 挂载
- PowerShell 脚本覆盖安装、更新、卸载
- 也可以用 Git Bash 运行 `bash scripts/install.sh`
- 初始账号密码同样保存在 `data\INITIAL_CREDENTIALS.txt`
- 详细步骤、Ollama 配置和常见问题见 [docs/WINDOWS.md](docs/WINDOWS.md)

### 更新

```bash
bash scripts/update.sh          # 同步 GitHub 最新代码并重启
bash scripts/update.sh --check  # 只看有没有新版本
```

- `git clone` 的部署：`git fetch` + 快进合并
- 下载 zip 的部署：从 GitHub 拉取压缩包，只替换代码和插件
- 始终保留 `data/` 和 `.env`
- 旧文件备份到 `backups/update-<时间>/`

更新时如果提示权限不足，同样用 `sudo bash scripts/update.sh`，或先加入 docker 组。

每天自动同步：

```cron
30 4 * * * cd /opt/astrbot && bash scripts/update.sh --auto >> update.log 2>&1
```

### 卸载

```bash
bash scripts/uninstall.sh              # 停容器，保留 data/ 和 .env
bash scripts/uninstall.sh --purge      # 连 data/、.env 一起删除
bash scripts/uninstall.sh --remove-images
```

完整选项、cron/systemd 示例和排查方法见 [scripts/README.md](scripts/README.md)。

## 命令

完整命令见 [COMMANDS.md](COMMANDS.md)。常用命令：

```text
/语音开        开启当前会话语音
/语音关        关闭当前会话语音
/心            查看心情与关系
/手札          查看心绪手札
/日记2         查看日记
/画像          查看主人画像
/日程          查看日程
/涟漪          查看涟漪池状态
/涟漪 列表     查看池子里的小事
/涟漪 摘录     立刻摘一次最近聊天
/涟漪 记       手动放一件小事进池子
/相册          查看相册和备注
/相册备注      给最新一张照片加一句备注
/上下文        查看最近上下文
/发图          发送图片
/表情          发送 QQ 原生表情
/表情包        发送表情包
/动态          发布 QQ 空间动态
/动态预览      预览自动动态
/动态自动      查看自动动态配置
```

## 仓库不包含的依赖

- AstrBot 和 SnowLuma 的完整源码：使用官方镜像
- FeiyuVoice-CosyVoice3 的 7.4GB 本地权重：当前部署使用云端声音复刻
- Ollama 模型文件：通过 `ollama pull bge-m3` 获取
- 任何真实账号、密钥、聊天记录或记忆数据库

## 数据与隐私

这个仓库**不包含**：

- DeepSeek / 火山引擎 / 其他 API Key
- QQ 号、管理员账号、UMO、token
- `data_v4.db`、聊天记录、LivingMemory 数据库
- `plugin_data`、日记、画像、日程、心绪手札、涟漪池
- 相册照片和照片备注
- `data/INITIAL_CREDENTIALS.txt`（初始账号密码）
- 登录二维码、语音样本、日志、截图
- 个人服务器 IP、SSH 密钥、密码

运行时数据全部放在 `data/`，该目录已被 `.gitignore` 排除。详见
[PRIVACY.md](PRIVACY.md)。

## 第三方组件与许可

本项目原创代码和文档采用**个人非商业使用许可**，不允许商业使用；具体见
[LICENSE](LICENSE) 和 [DISCLAIMER.md](DISCLAIMER.md)。AstrBot、SnowLuma、
LivingMemory、FeiyuVoice、表情包素材等第三方组件分别遵循各自许可，详见
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

- AstrBot：<https://github.com/AstrBotDevs/AstrBot>
- SnowLuma：<https://github.com/SnowLuma/SnowLuma>
- LivingMemory：<https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory>
- FeiyuVoice：<https://github.com/Esmerubphire/astrbot_plugin_feiyu_voice>
- 表情包：<https://github.com/diwer/ai-biaoqingbao>

表情包与 FeiyuVoice 素材仅建议个人非商业使用；商用前请自行确认授权。
