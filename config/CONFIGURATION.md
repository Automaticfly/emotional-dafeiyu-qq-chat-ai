# 配置说明

本项目的配置文件都应由使用者自己创建，不要提交到 Git。

## 1. OneBot 平台

在 AstrBot WebUI 新建 OneBot v11 平台：

- ID：`napcat`（建议保持这个 ID，插件默认 UMO 前缀也是它）
- 类型：`aiocqhttp`
- 反向 WebSocket 主机：`0.0.0.0`
- 反向 WebSocket 端口：`6199`
- Token：自己生成一个随机 token

在 SnowLuma 的 `data/config/onebot.json` 中配置客户端：

```json
{
  "networks": {
    "httpServers": [],
    "wsServers": [],
    "httpClients": [],
    "wsClients": [
      {
        "name": "astrbot",
        "enabled": true,
        "url": "ws://astrbot:6199/ws",
        "role": "Universal",
        "accessToken": "与 AstrBot 平台 Token 完全相同",
        "messageFormat": "array",
        "reconnectIntervalMs": 5000
      }
    ]
  }
}
```

两边 token 必须一致，否则 AstrBot 会拒绝连接。

## 2. 模型 Provider

在 AstrBot WebUI 配置：

### DeepSeek

- 类型：OpenAI 兼容 / DeepSeek
- API Key：自己的 key
- 模型：例如 `deepseek-chat` 或当前可用的 DeepSeek 对话模型

### 火山引擎 TTS

- 类型：`volcengine_tts`
- API Key：火山引擎语音 API Key
- Resource ID：`seed-icl-2.0`（声音复刻 2.0）
- 音色 ID：自己的声音复刻 speaker ID
- 默认 TTS 关闭，由 `/语音开` 开启当前会话

### Ollama Embedding

- 类型：`ollama_embedding`
- API Base：`http://host.docker.internal:11434`
- 模型：`bge-m3`
- 维度：1024

宿主机安装 Ollama 后：

```bash
ollama pull bge-m3
```

如果 AstrBot 不在 Docker 中，把 `host.docker.internal` 改成实际 Ollama 地址。

## 3. 插件配置

### 社交 / 动态 / 表情包

`astrbot_plugin_dafeiyu_social`：

```json
{
  "admin_qq": "你的大号QQ",
  "admin_umo": "napcat:FriendMessage:你的大号QQ",
  "qzone_platform_id": "napcat",
  "qzone_auto_umo": "napcat:FriendMessage:你的大号QQ",
  "qzone_auto_enabled": true,
  "qzone_auto_time": "09:30",
  "qzone_auto_privacy": "好友",
  "sticker_auto_enabled": true
}
```

- `admin_qq`：唯一允许使用管理命令的 QQ
- `qzone_auto_privacy`：自动动态默认权限
- 自动动态读取前一天 LivingMemory 重要度 ≥ `qzone_auto_min_importance` 的内容
- 隐私过滤命中即跳过
- 默认不允许群聊使用；需要时自行改 `allow_groups`

### 心脏 / 记忆 2.0

`astrbot_plugin_dafeiyu_heart` 和 `astrbot_plugin_dafeiyu_memory2` 的
`main_umo` 填：

```text
napcat:FriendMessage:你的大号QQ
```

用于结算和查看主人的心脏、日记、画像和日程。

### 心情表情

`astrbot_plugin_dafeiyu_moodface`：

```json
{
  "enabled": true,
  "face_probability": 0.5,
  "inline_probability": 0.5
}
```

- `face_probability`：回复带原生表情的概率
- `inline_probability`：其中跟随最后一段文字的概率；剩余概率独立发送

### LivingMemory

在 AstrBot 的长期记忆设置中选择：

- embedding provider：Ollama `bge-m3`
- LLM provider：DeepSeek
- 记忆作用域：按会话隔离

首次运行会创建自己的 `livingmemory.db`，不要从别人的机器复制数据库。

## 4. 分段回复

AstrBot 的 `platform_settings.segmented_reply` 建议：

```json
{
  "enable": true,
  "only_llm_result": true,
  "interval_method": "random",
  "interval": "0.8,1.8",
  "words_count_threshold": 100000,
  "split_mode": "regex",
  "regex": ".*?。|.+$",
  "content_cleanup_rule": ""
}
```

核心补丁 `patches/respond_stage.py` 额外支持原生表情内联到最后一段文字。

## 5. SnowLuma 登录

- WebUI：`http://127.0.0.1:5099`
- noVNC：`http://127.0.0.1:6081/vnc.html?autoconnect=1`
- 首次启动的 WebUI 密码在 SnowLuma 容器日志里
- noVNC 密码由 `.env` 的 `VNC_PASSWORD` 决定

不要把 5099/6081/3000/3001 直接暴露到公网。

## 6. 数据目录

不要提交以下内容：

```text
data/cmd_config.json
data/data_v4.db*
data/config/
data/plugin_data/
data/logs/
data/snowluma/
data/snowluma-qq-config/
data/snowluma-qq-data/
```

这些文件包含密钥、聊天记录、记忆、日记、画像、登录态和二维码。
