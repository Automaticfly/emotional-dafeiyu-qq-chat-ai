# 隐私与数据说明

这个仓库是**功能发布版**，不是个人数据的备份包。

## 仓库中不会出现的内容

- DeepSeek、火山引擎、Ollama 或其他服务的 API Key
- QQ 号、管理员 QQ、会话 UMO、平台 token
- AstrBot 的 `cmd_config.json` 和密码哈希
- `data_v4.db`、聊天记录、会话历史
- LivingMemory 数据库、记忆向量、反思记录
- `plugin_data` 中的心脏、日记、画像、日程
- 涟漪池数据库和其中摘录的日常碎事
- 相册照片、照片备注和图片索引
- 插件实际配置（可能包含 QQ 号/UMO）
- 登录二维码、截图、语音样本、试听音频
- 服务器 IP、SSH 私钥、面板密码、VNC 密码
- 日志、临时文件、上传附件

## 运行时数据

所有运行时数据都在：

```text
data/
```

包括：

```text
data/cmd_config.json
data/data_v4.db
data/config/
data/plugin_data/
data/plugin_data/astrbot_plugin_dafeiyu_ripple/ripple.db
data/plugin_data/astrbot_plugin_dafeiyu_social/album.db
data/plugin_data/astrbot_plugin_dafeiyu_social/album/
data/logs/
data/snowluma/
data/snowluma-qq-config/
data/snowluma-qq-data/
```

`.gitignore` 已排除这些路径。即使本地已经运行过，`git status` 也不应该出现聊天
记录或数据库。

## 第一次运行

1. 复制 `.env.example` 为 `.env`，填写自己的 VNC 密码
2. 在 AstrBot WebUI 创建自己的 provider、平台和 token
3. 在插件配置里填写自己的管理员 QQ / UMO
4. 用自己的 QQ 扫码登录 SnowLuma
5. 用 `/lmem` 和插件命令确认记忆和媒体正常

## 提交前检查

```bash
git status --short
git grep -n -E 'api[_-]?key|access[_-]?token|password|passwd|Bearer|BEGIN .*PRIVATE KEY' || true
git grep -n -E '[0-9]{6,}' || true
```

确认没有把 `.env`、`data/`、`*.db`、二维码或音频加入暂存区。

## 第三方数据

- 表情包来自公开项目 `diwer/ai-biaoqingbao`，仅建议个人非商业使用
- FeiyuVoice 只作为音色来源说明；仓库不包含训练音频或云端 voice ID
- AstrBot、SnowLuma、LivingMemory 分别遵循各自许可

如果你要在自己的部署中使用真实人物声音、聊天记录或他人素材，请自行确认授权。
