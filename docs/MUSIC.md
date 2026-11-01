# 音乐功能：在线 B 站 + 本地音乐库

> 适用版本：v1.5.0
>
> 支持平台：Windows 10/11（Docker Desktop + WSL2）、Linux（Ubuntu 22.04+ / Debian 12+）、已有 AstrBot 的原生部署。

这套音乐功能分成两条互不冲突的链路：

| 场景 | 使用的插件 | 命令 / 工具 | 音源 |
|---|---|---|---|
| 在线点歌、下载 | 插件市场「我想听歌！」或「bili播放器」 | 自然语言、`搜索歌曲`、序号选择 | Bilibili |
| 本地音乐库 | `astrbot_plugin_dafeiyu_ears` | `/本地点歌`、`/本地听歌`、`/本地歌单`、`/本地分享` | 本地文件 |

> **重要**
>
> 在线音乐插件来自 AstrBot 插件市场，不是本仓库自带代码。本仓库只提供安装说明、
> 兼容补丁和本地音乐插件。在线插件的版权、可用性和 B 站账号规则归原作者和
> Bilibili 所有。

---

## 1. 在线音乐（B 站）

### 1.1 能做什么

- 按歌名、歌手、版本偏好搜索 B 站视频
- 选中后优先发送 QQ 语音；适配器不支持时回退音频文件
- 支持指定 BV / AV 号或 B 站视频链接
- 支持下载完整音频文件
- 单首语音最长 15 分钟、最大 25 MiB；下载单文件最大 100 MiB
- 不需要 B 站账号也能搜索和播放；登录 B 站可提高受限内容的可用性

### 1.2 两个版本怎么选

市场里可能看到两个名字，它们是同一作者的前后版本：

| 显示名 | 目录 | 版本 | 特点 |
|---|---|---|---|
| 我想听歌！ | `astrbot_plugin_listen_music` | 0.1.3 | 命令少，只有 `搜索歌曲`；工具 `find_music` / `deliver_music` / `search_music` |
| bili播放器 | `astrbot_plugin_bili_player` | 0.2.5 | 多了 `搜索视频`、`/我要听`、`/我要看`；工具 `find_in_bilibili` / `deliver_media` |

> **建议**
>
> 只安装其中一个。两个都装会同时注册 `搜索歌曲`，容易抢命令。
> 只需要听歌就选 0.1.3「我想听歌！」；需要看视频再选 0.2.5「bili播放器」。
> 本仓库的补丁脚本两个版本都支持。

### 1.3 前置条件

| 项目 | 要求 |
|---|---|
| AstrBot | `>= 4.26, < 5`（当前镜像为 4.29.x） |
| ffmpeg | AstrBot 容器内可执行 `ffmpeg -version` |
| 网络 | 能访问 Bilibili 的 API、网页和音频 CDN |
| Python 依赖 | 插件自带；二维码登录另需 `qrcode` 与 Pillow，官方镜像已包含 |

本项目的官方 AstrBot 镜像和 Dockerfile 基础镜像都带 `ffmpeg`。如果你使用自定义
镜像，请先确认：

```bash
docker exec astrbot ffmpeg -version
```

没有输出时，在镜像里安装：

```dockerfile
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*
```

### 1.4 安装在线插件

1. 打开 AstrBot WebUI：`http://127.0.0.1:6185`
2. 进入「插件市场」
3. 搜索「我想听歌！」或「bili播放器」
4. 选择其中一个安装
5. 安装完成后重启 AstrBot（或在插件页确认已加载）

也可以在「插件管理 → 从仓库安装」填写：

```text
https://github.com/57Darling02/astrbot_plugin_bili_player
```

> **注意**
>
> 仓库地址会安装当前最新的 0.2.5「bili播放器」。如果你只想要 0.1.3 的
> 简洁命令，请从插件市场选择「我想听歌！」0.1.3，不要用仓库地址。

安装后在日志里应看到类似：

```text
Plugin astrbot_plugin_listen_music (0.1.3) by 57Darling02
```

或：

```text
Plugin astrbot_plugin_bili_player (0.2.5) by 57Darling02
```

> **提示**
>
> Windows compose 把仓库的 `plugins\` 挂载为插件目录，市场安装的在线插件会出现在
> `plugins\astrbot_plugin_*`。它是第三方代码，不要提交到 Git 仓库。
> Linux compose 使用 `data/plugins/`，已经在 `.gitignore` 里。

### 1.5 打 412 兼容补丁（重要）

#### 为什么需要

未登录 B 站时，插件请求视频详情可能被 B 站返回 **HTTP 412**。插件会把它当成
“没有可播放版本”，于是出现：

```text
晴天能放，永世的摇篮找不到
```

补丁会做两件事：

1. 给请求加上匿名设备 Cookie（`buvid3` / `buvid4` / `b_nut` / `b_lsid` / `_uuid`）
2. 遇到 412 时换一组 Cookie，自动重试一次

补丁只修改**已安装插件**的 `core/bilibili.py`，不下载、不打包第三方源码。

#### Docker 部署（Linux / Windows 通用）

在项目根目录执行：

```bash
# 把补丁脚本复制进 AstrBot 容器
docker cp scripts/patch_music_plugin.py astrbot:/tmp/patch_music_plugin.py

# 打补丁；/AstrBot/data 是容器里的数据目录
docker compose exec astrbot python /tmp/patch_music_plugin.py --path /AstrBot/data
```

Windows 使用 Windows compose 文件：

```powershell
docker cp scripts\patch_music_plugin.py astrbot:/tmp/patch_music_plugin.py
docker compose -f docker-compose.windows.yml exec astrbot python /tmp/patch_music_plugin.py --path /AstrBot/data
```

然后重启：

```bash
docker compose restart astrbot
```

```powershell
docker compose -f docker-compose.windows.yml restart astrbot
```

#### 原生 AstrBot（本机直接运行）

Windows：

```powershell
python scripts\patch_music_plugin.py --path D:\AstrBot
```

Linux：

```bash
python3 scripts/patch_music_plugin.py --path /opt/astrbot
```

#### 输出示例

第一次运行：

```text
[BACKUP] /opt/astrbot/backups/plugin-patches/astrbot_plugin_listen_music_bilibili.py.orig.bak
[PATCHED] astrbot_plugin_listen_music：/opt/astrbot/data/plugins/astrbot_plugin_listen_music/core/bilibili.py
          重启 AstrBot 后生效。
[DONE] 完成。若修改了文件，请重启 AstrBot。
```

已经打过补丁：

```text
[OK] astrbot_plugin_listen_music 已经是补丁版，跳过
```

只检查、不写入：

```bash
docker compose exec astrbot python /tmp/patch_music_plugin.py --path /AstrBot/data --dry-run
```

> **提示**
>
> 补丁是幂等的，可以反复运行。Docker 下原文件备份在
> `data/backups/plugin-patches/`，原生部署在项目根 `backups/plugin-patches/`。
> 需要回滚时把备份复制回 `core/bilibili.py` 即可。

> **注意**
>
> 在 AstrBot 里更新或重装在线音乐插件后，补丁可能被覆盖。更新完插件后重新运行
> 一次补丁脚本，再重启 AstrBot。

### 1.6 使用

#### 自然语言

```text
来一首周杰伦的晴天
我想听永世的摇篮
唱首歌
```

模型会调用在线插件的搜索工具，选择候选并发送。

#### 确定性的命令流程

```text
搜索歌曲 晴天
```

机器人会返回候选列表，例如：

```text
1. 【Hi-Res无损音质】《晴天》- 周杰伦 (4:30)
2. 【4K修复】周杰伦 - 晴天MV (5:17)
...
```

然后回复：

```text
1
```

收到语音。想下载文件：

```text
1 下载
```

指定 BV / AV 号或链接：

```text
下载 BV1ZJ4m1b7Zz
搜索歌曲 https://www.bilibili.com/video/BV1ZJ4m1b7Zz
```

取消当前选择：

```text
取消
```

> **提示**
>
> 如果歌名是中文翻译，B 站可能只收录日文原名。搜不到时换一种写法：
>
> ```text
> 搜索歌曲 永世のクレイドル 鈴華ゆう子
> ```

### 1.7 B 站账号登录（可选）

在线插件支持在插件页扫码登录 B 站：

1. 打开 AstrBot WebUI → 插件 → 在线音乐插件
2. 找到「Bilibili 账号」或「扫码登录」
3. 用 B 站手机客户端扫码
4. 登录后可以访问账号本来就有权限的内容

> **警告**
>
> 不要把 B 站 Cookie、二维码或账号密码发到聊天、Issue 或仓库。
> Cookie 只保存在插件自己的数据目录里，不属于本项目。

### 1.8 已知限制

- B 站是视频站，不是完整音乐库：有些歌只有翻唱、Live、MAD、伴奏或已删除视频
- 搜索只取前 12 个视频，再展开分 P；非常冷门的歌可能不在前 12 个结果里
- 视频被删除时 B 站返回 404，插件会跳过，属于正常现象
- 语音最长 15 分钟、最大 25 MiB；超过时只能下载文件
- 下载单文件最大 100 MiB，适配器本身可能还有更低限制
- 地区限制、会员内容、版权下架由 B 站决定，本项目无法绕过
- 在线插件的搜索排序和候选标题由 B 站决定

### 1.9 排查

| 现象 | 可能原因 | 处理 |
|---|---|---|
| 提示“没有找到…的可播放 Bilibili 音乐” | 412 未打补丁；或前 12 个结果全被删除/限制 | 先运行 1.5 的补丁；再换日文原名或歌手名重搜 |
| 搜索有结果，选序号后发送失败 | ffmpeg 缺失、音频 CDN 超时、适配器限制 | `docker exec astrbot ffmpeg -version`；看 AstrBot 日志；改用 `1 下载` |
| 管理页提示 ffmpeg 不可用 | 自定义镜像没装 ffmpeg | 在镜像里安装 ffmpeg 后重建 |
| 二维码登录打不开 | 缺少 `qrcode` / Pillow，或没有管理员权限 | 在 AstrBot 环境安装 `qrcode[pil]`；用 Dashboard 主账号 |
| 更新插件后又“找不到” | 插件更新覆盖了补丁 | 重新运行 `patch_music_plugin.py`，重启 |
| 两个音乐插件抢命令 | 同时装了 0.1.3 和 0.2.5 | 只保留一个，另一个在插件管理里卸载 |

日志排查：

```bash
docker compose logs --tail 200 astrbot | grep -iE "listen_music|bili_player|412|find_music|deliver_music"
```

Windows：

```powershell
docker compose -f docker-compose.windows.yml logs --tail 200 astrbot | Select-String -Pattern "listen_music|bili_player|412|find_music|deliver_music"
```

### 1.10 更新与卸载在线插件

更新：

1. AstrBot WebUI → 插件管理 → 检查更新
2. 更新后重新运行 412 补丁
3. 重启 AstrBot

卸载：

1. AstrBot WebUI → 插件管理 → 在线音乐插件 → 卸载
2. 如需彻底清理，删除插件数据和 B 站登录态（确认不再需要后）
3. 本仓库的 `astrbot_plugin_dafeiyu_ears` 不受影响

> **注意**
>
> 卸载在线插件不会删除本地音乐库。本地音乐在
> `data/plugin_data/astrbot_plugin_dafeiyu_ears/music/`，需要单独备份或删除。

---

## 2. 本地音乐库

本地音乐由本仓库自带的 `astrbot_plugin_dafeiyu_ears` 提供，和在线插件完全分开。

### 2.1 音乐目录

Linux / Docker：

```text
data/plugin_data/astrbot_plugin_dafeiyu_ears/music/
```

容器内路径：

```text
/AstrBot/data/plugin_data/astrbot_plugin_dafeiyu_ears/music/
```

Windows 原生：

```text
D:\AstrBot\data\plugin_data\astrbot_plugin_dafeiyu_ears\music\
```

Windows Docker：

```text
data\plugin_data\astrbot_plugin_dafeiyu_ears\music\
```

可以建子目录，插件会递归扫描。

### 2.2 支持格式

```text
.mp3  .flac  .m4a  .wav  .ogg  .aac  .opus
```

### 2.3 命令

| 命令 | 说明 |
|---|---|
| `/本地点歌 <关键词>` | 按文件名关键词点歌；配置 Meting 后可在线搜索 |
| `/本地听歌` | 随机放一首本地歌曲 |
| `/本地歌单` | 查看本地歌单和数量 |
| `/本地分享 <关键词>` | 分享本地歌曲；在线时发音乐卡片 |

> **提示**
>
> 旧版命令 `/点歌`、`/听歌`、`/歌单`、`/分享歌曲` 已改名为 `/本地*`，
> 避免和在线音乐插件抢命令。升级后请用新命令。

### 2.4 配置

在 AstrBot WebUI → 插件 → 大肥鱼耳朵：

| 配置 | 默认 | 说明 |
|---|---|---|
| `enabled` | `true` | 关闭后不响应本地音乐命令 |
| `admin_qq` | 空 | 管理员 QQ；留空时私聊可用、群聊默认不可用 |
| `admin_umo` | 空 | 管理员会话 UMO，例如 `napcat:FriendMessage:123456` |
| `allow_groups` | `false` | 是否允许群聊点歌 |
| `music_dir` | `music` | 相对插件数据目录的音乐文件夹 |
| `send_mode` | `file` | `file` 发文件；`record` 发语音条；`card` 在线歌曲卡片 |
| `max_file_mb` | `30` | 单首文件大小上限 |
| `cooldown_seconds` | `10` | 同一会话点歌冷却 |
| `meting_api` | 空 | 可选 Meting 在线搜索接口；留空只用本地 |

> **建议**
>
> 整首歌优先用 `send_mode=file`。`record` 受 QQ 语音时长和大小限制，适合短音频。

### 2.5 验证

1. 放一首 mp3 到音乐目录
2. 发送 `/本地歌单`，确认数量变成 1
3. 发送 `/本地点歌 歌名关键词`
4. 发送 `/本地听歌` 测试随机播放
5. 发送 `/本地分享 歌名关键词` 测试分享

### 2.6 排查

| 现象 | 处理 |
|---|---|
| `/本地歌单` 是 0 首 | 确认文件在 `music/` 下，扩展名受支持 |
| 提示没有权限 | 配置 `admin_qq` 或 `admin_umo`；群聊还要 `allow_groups=true` |
| 文件太大不发送 | 调大 `max_file_mb`，或换小文件 |
| 语音条发不出去 | QQ 对语音时长/格式有限制，改用 `send_mode=file` |
| 中文文件名乱码 | 确认系统/容器使用 UTF-8；Linux 可用 `locale` 检查 |
| Meting 搜索失败 | 第三方接口可能失效；留空 `meting_api` 只用本地 |

### 2.7 备份、卸载与数据

本地音乐库不会被在线插件或仓库更新删除。备份：

```bash
cp -a data/plugin_data/astrbot_plugin_dafeiyu_ears/music ./music-backup
```

Windows：

```powershell
Copy-Item -Recurse data\plugin_data\astrbot_plugin_dafeiyu_ears\music music-backup
```

卸载本地音乐插件：

1. AstrBot WebUI → 插件管理 → 大肥鱼耳朵 → 卸载/禁用
2. 如需删除音乐，手动删除 `data/plugin_data/astrbot_plugin_dafeiyu_ears/`
3. 卸载本仓库时，`scripts/uninstall.sh` 默认保留 `data/`；`--purge` 才会删除

---

## 3. 两套音乐如何共存

| 检查项 | 在线插件 | 本地耳朵 |
|---|---|---|
| 命令 | `搜索歌曲`，自然语言 | `/本地点歌`、`/本地听歌`、`/本地歌单`、`/本地分享` |
| LLM 工具 | `find_music` / `deliver_music` / `search_music`（0.1.3）或 `find_in_bilibili` / `deliver_media`（0.2.5） | `local_music_play` / `local_music_search` |
| 数据 | B 站临时媒体，发送后清理 | 本地 `music/` 长期保留 |
| 冲突 | 不要同时装 0.1.3 和 0.2.5 | 不和在线插件抢 `/点歌` |

如果还装了其他 `/点歌` 插件，请在插件管理里只保留一个主点歌插件，避免命令被抢。

---

## 4. 隐私与版权

- 本仓库不包含 B 站 Cookie、二维码、账号密码或登录态
- B 站 Cookie 只保存在在线插件自己的数据目录，不要发到聊天/Issue/仓库
- 本地音乐文件不建议提交到公开仓库，版权归各自权利人
- 在线音乐只做搜索和播放，不绕过 B 站会员、地区或版权限制
- 本项目仅限个人学习、研究和个人非商业使用
