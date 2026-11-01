# 第三方组件与素材

本项目原创的插件、部署脚本和文档使用**个人非商业使用许可**，不允许商业使用。
以下第三方组件不适用本仓库的许可，使用和分发时请遵守各自条款。

## AstrBot

- 项目：<https://github.com/AstrBotDevs/AstrBot>
- 用途：机器人框架、WebUI、消息调度、插件运行时
- 本仓库不包含 AstrBot 完整源码；通过官方镜像 `soulter/astrbot:latest` 使用
- `patches/volcengine_tts.py`、`patches/session_llm_manager.py`、
  `patches/respond_stage.py` 是对 AstrBot 核心文件的修改片段/派生文件，
  同样按 AstrBot 的 AGPL-3.0 条款使用，不适用本仓库根目录的个人非商业许可
- 许可：AGPL-3.0（以项目仓库为准）

## SnowLuma

- 项目：<https://github.com/SnowLuma/SnowLuma>
- 镜像：`motricseven7/snowluma:latest`
- 用途：Linux QQ 客户端、OneBot v11、noVNC
- 许可：源码可见非商业许可，具体以项目 `LICENSE` / `EULA.md` 为准
- 本仓库不包含 SnowLuma 源码或 QQ 客户端

## 在线音乐插件（可选）

- 项目：<https://github.com/57Darling02/astrbot_plugin_bili_player>
- 旧名/旧目录：`astrbot_plugin_listen_music`（「我想听歌！」0.1.3）
- 用途：Bilibili 搜索、语音/文件发送、视频与音频下载
- 本仓库**不包含**该插件源码，只提供安装说明和
  `scripts/patch_music_plugin.py` 兼容补丁
- 该仓库未声明明确许可证；使用、修改和再分发前请自行确认作者条款
- 补丁只修改部署者本地已安装的 `core/bilibili.py`，不改变插件归属

## LivingMemory

- 项目：<https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory>
- 用途：长期记忆、反思、向量检索
- 本仓库包含其运行所需源码副本；许可见
  `plugins/astrbot_plugin_livingmemory/LICENSE`

## FeiyuVoice

- 项目：<https://github.com/Esmerubphire/astrbot_plugin_feiyu_voice>
- 用途：提供 FeiyuVoice-CosyVoice3 声线参考与音色训练思路
- 本仓库不包含其本地 CosyVoice3 权重、训练音频、RVC 权重或云端 voice ID
- 原始声音素材来自 B 站 UP 主「星落落_oi」，项目方声明已取得训练授权
- 仅建议个人非商业使用；商用前自行确认授权

## 表情包

- 项目：<https://github.com/diwer/ai-biaoqingbao>
- 目录：`assets/stickers/dafeiyu/`
- 原始说明与许可：`assets/stickers/dafeiyu/NOTICE.txt` 和 `README.md`
- 仅建议个人非商业使用；图片、GIF 和角色素材的授权以原项目说明为准

## 服务与模型

- DeepSeek API：<https://www.deepseek.com/>
- 火山引擎豆包语音：<https://www.volcengine.com/>
- Ollama：<https://ollama.com/>
- 这些服务的使用受各自账号协议和计费规则约束。

## 使用边界

- 不要用本项目冒充官方角色或官方产品
- 不要将他人声音、聊天记录、照片或个人数据用于未经授权的训练或发布
- 使用真实 QQ 账号时请遵守 QQ 用户协议，并优先使用小号
