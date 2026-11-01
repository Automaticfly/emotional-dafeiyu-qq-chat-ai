# Windows 本地部署

这个项目可以在 Windows 上本地运行，不需要云服务器。

> **部署建议**
>
> 第一次部署可以让 AI 协助：把本文件或仓库链接发给 AI，说明你的 Windows 版本、
> Docker Desktop 是否已启动、卡在哪一步，让它按下面的步骤带你排查。
> 不要粘贴真实 API Key、QQ 号、密码、聊天记录或 `data\` 内容。

原理：AstrBot 和 SnowLuma 都是 **Linux 容器**，Windows 上用 Docker Desktop 的 WSL2 后端运行它们。QQ 登录通过浏览器里的 noVNC 扫码，不需要安装 Windows 版 QQ。

## 前置条件

- Windows 10 64 位（2004 及以上）或 Windows 11
- 已开启 CPU 虚拟化（BIOS / 任务管理器 → 性能 → 虚拟化）
- Docker Desktop，使用 **WSL2 后端**
- 建议 8GB 以上内存（SnowLuma 里跑的是完整 Linux QQ）
- 磁盘预留 10GB 以上

## 安装 Docker Desktop

1. 下载并安装 [Docker Desktop](https://www.docker.com/products/docker-desktop/)
2. 安装时勾选 **Use WSL 2 instead of Hyper-V**
3. 启动 Docker Desktop，等右下角鲸鱼图标变绿
4. Settings → Resources → WSL Integration，确认已启用

验证：

```powershell
docker version
docker compose version
```

## 方案 A：PowerShell 原生安装（推荐）

### 1. 准备代码

```powershell
git clone https://github.com/Automaticfly/emotional-dafeiyu-qq-chat-ai.git
cd emotional-dafeiyu-qq-chat-ai
```

没有安装 Git 也可以直接从 GitHub 下载 ZIP，解压后进入目录。

### 2. 运行安装脚本

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install.ps1
```

脚本会自动：

1. 检查 Docker Desktop 和目录权限
2. 创建 `data\` 目录
3. 生成 `.env`，没改 VNC 占位密码时自动生成随机密码
4. 复制表情包和 SnowLuma OneBot 配置
5. 用 `docker-compose.windows.yml` 构建并启动容器
6. 把初始账号密码保存到 `data\INITIAL_CREDENTIALS.txt`

如果提示无法连接 Docker Desktop，先启动 Docker Desktop，等图标变绿再试。

### 3. 登录

打开浏览器：

```text
http://127.0.0.1:6185          AstrBot
http://127.0.0.1:5099          SnowLuma
http://127.0.0.1:6081/vnc.html?autoconnect=1   noVNC
```

初始用户名和密码在：

```text
data\INITIAL_CREDENTIALS.txt
```

文件里包含：

- AstrBot 用户名 / 初始密码
- SnowLuma 用户名 / 初始密码
- noVNC 密码
- 忘记密码时的重置命令

登录后请立即修改两个面板的密码，并删除这个文件。

### 4. 接入 QQ

1. 登录 SnowLuma，确认 QQ 进程在线
2. 打开 noVNC，用手机 QQ 扫码登录
3. AstrBot → 平台 → 新建 OneBot v11（aiocqhttp）
   - ID：`napcat`
   - 反向 WS 端口：`6199`
   - Token：自己生成一个
4. 编辑 `data\snowluma\config\onebot.json`，把 `accessToken` 改成同样的 Token
5. 重启：

```powershell
docker compose -f docker-compose.windows.yml restart snowluma
```

### 5. 安装在线音乐（推荐）

1. 打开 AstrBot WebUI → 插件市场
2. 安装「我想听歌！」0.1.3 或「bili播放器」0.2.5（只装一个）
3. 在项目根目录运行 412 补丁：

```powershell
docker cp scripts\patch_music_plugin.py astrbot:/tmp/patch_music_plugin.py
docker compose -f docker-compose.windows.yml exec astrbot python /tmp/patch_music_plugin.py --path /AstrBot/data
docker compose -f docker-compose.windows.yml restart astrbot
```

4. 在 QQ 里发 `搜索歌曲 晴天`，回复 `1` 测试

> **提示**
>
> Windows compose 把仓库的 `plugins\` 挂到容器的 `/AstrBot/data/plugins`。
> 插件市场安装的在线插件会出现在 `plugins\astrbot_plugin_*`，补丁脚本会同时
> 查找 `plugins\` 和 `data\plugins\`。

完整说明见 [MUSIC.md](MUSIC.md)。

### 6. 绑定主人

机器人建议用 **QQ 小号** 登录；你的大号用来认主。

1. 用大号加机器人小号为好友
2. 用大号私聊机器人，发送：

```text
/认主 你的QQ号
```

3. 设置换主密码：

```text
/认主 密码 你的密码
```

密码要求 8-64 位，至少包含一个字母和一个数字。

4. 机器人会校验「发送者 QQ」和「填写的 QQ」是否一致
5. 成功后自动写入插件配置和 AstrBot 管理员列表，并重启一次

以后换主，用新 QQ 本人私聊：

```text
/认主 更换 新QQ 换主密码
```

验证通过后会清除旧 QQ 的管理权限，再绑定新 QQ。

其他命令：

```text
/认主 改密码 旧密码 新密码
/认主 状态
```

认主会写入：

- `data\config\astrbot_plugin_dafeiyu_social_config.json`：`admin_qq`、`admin_umo`
- `data\config\astrbot_plugin_dafeiyu_ripple_config.json`：`admin_qq`、`admin_umo`
- `data\config\astrbot_plugin_dafeiyu_memory2_config.json`：`main_umo`
- `data\config\astrbot_plugin_dafeiyu_heart_config.json`：`main_umo`
- `data\cmd_config.json`：`admins_id`
- `data\owner.json`：绑定记录和密码哈希

群里发 `/认主` 会被拒绝；已经绑定后，其他 QQ 不能覆盖。
密码只保存哈希，连续输错 5 次会锁定 10 分钟。

如果认主后还是不认识：先用主人号私聊发送 `/reset` 清除上下文，再问一次；还不行就把 `/认主 状态` 的输出和报错发给 AI 求助，不要发密码、API Key 或 `data\` 内容。

## 方案 B：Git Bash

如果已经安装 Git for Windows，也可以用 bash 脚本：

```bash
cd /d/dafeiyu-qq
bash scripts/install.sh
```

`install.sh` 会检测到 `MINGW/MSYS`，自动改用 `docker-compose.windows.yml`。

如果脚本报 `$'\r': command not found`：

```bash
sed -i 's/\r$//' scripts/*.sh
```

## 日常命令

```powershell
# 启动
docker compose -f docker-compose.windows.yml up -d

# 停止
docker compose -f docker-compose.windows.yml stop

# 查看状态
docker compose -f docker-compose.windows.yml ps

# 查看日志
docker compose -f docker-compose.windows.yml logs -f astrbot

# 更新
powershell -ExecutionPolicy Bypass -File scripts\update.ps1

# 只检查更新
powershell -ExecutionPolicy Bypass -File scripts\update.ps1 -Check

# 卸载（保留数据）
powershell -ExecutionPolicy Bypass -File scripts\uninstall.ps1

# 卸载并删除数据
powershell -ExecutionPolicy Bypass -File scripts\uninstall.ps1 -Purge

# 卸载并删除镜像
powershell -ExecutionPolicy Bypass -File scripts\uninstall.ps1 -RemoveImages

# 在线音乐插件补丁（安装/更新插件后运行）
docker cp scripts\patch_music_plugin.py astrbot:/tmp/patch_music_plugin.py
docker compose -f docker-compose.windows.yml exec astrbot python /tmp/patch_music_plugin.py --path /AstrBot/data
docker compose -f docker-compose.windows.yml restart astrbot
```

## 为什么有 `docker-compose.windows.yml`

Linux 版 `docker-compose.yml` 挂载了：

```yaml
- /etc/localtime:/etc/localtime:ro
- /etc/timezone:/etc/timezone:ro
```

Windows 上没有这两个路径，可能报挂载错误。Windows 专用文件去掉了这两个挂载，时区由 `TZ=Asia/Shanghai` 控制，其他配置完全一样。

## Ollama（可选）

如果要用本地向量模型 `bge-m3`：

1. 安装 [Ollama for Windows](https://ollama.com/download/windows)
2. 让 Ollama 监听所有网卡，否则容器访问不到：

```powershell
setx OLLAMA_HOST 0.0.0.0:11434
```

3. 重启 Ollama，然后：

```powershell
ollama pull bge-m3
```

4. AstrBot 的 embedding provider 填：

```text
http://host.docker.internal:11434
```

如果不想在本机跑模型，也可以在 AstrBot 里配置云端 embedding 服务，跳过这一步。

## 数据与备份

所有运行数据在：

```text
data\
├── cmd_config.json              AstrBot 配置
├── data_v4.db                   会话和历史
├── plugin_data\                 记忆、日记、画像、涟漪池、相册
├── snowluma\                    SnowLuma WebUI 配置
├── snowluma-qq-config\          QQ 登录态
└── snowluma-qq-data\            QQ 数据
```

备份：直接复制整个 `data\` 目录。换电脑时把 `data\` 放到新目录即可，不需要重新扫码。

不要把这个目录发给别人，里面有登录态和聊天记录。

## 常见问题

### 无法连接 Docker Desktop

- 确认 Docker Desktop 已启动，鲸鱼图标变绿
- Settings → Resources → WSL Integration 已启用
- 重启 Docker Desktop
- 仍失败时，把当前用户加入 docker-users 组（管理员 PowerShell）：

```powershell
net localgroup docker-users $env:USERNAME /add
```

然后注销并重新登录。

### 端口被占用

```powershell
netstat -ano | findstr :6185
netstat -ano | findstr :5099
netstat -ano | findstr :6081
```

关掉占用端口的程序，或修改 `docker-compose.windows.yml` 左边的端口。

### WSL2 内存占用高

在 `C:\Users\你的用户名\.wslconfig` 里限制：

```ini
[wsl2]
memory=6GB
processors=4
swap=2GB
```

然后 `wsl --shutdown`，重启 Docker Desktop。

### QQ 扫码后没反应

- 确认 noVNC 里的 Linux QQ 已经登录成功
- SnowLuma WebUI 里账号状态是否在线
- 检查 OneBot 的 Token 是否和 AstrBot 平台一致
- 查看日志：

```powershell
docker compose -f docker-compose.windows.yml logs --tail 100 snowluma
```

### 电脑睡眠后机器人掉线

Windows 本地部署依赖电脑保持开机。可以在电源设置里关闭睡眠，或改用云服务器部署。

### `permission denied` / 文件被占用

- 关闭正在读取 `data\` 的程序
- 用管理员身份打开 PowerShell 再运行
- 确认 Docker Desktop 已停止相关容器

### 音乐提示“没有找到可播放版本”

先运行在线音乐 412 补丁（见「5. 安装在线音乐」），重启后重试。仍然找不到时：

- 换日文原名或歌手名，例如 `搜索歌曲 永世のクレイドル 鈴華ゆう子`
- B 站视频可能已被删除（404），换一个版本
- 确认只装了一个在线音乐插件

### 音乐搜索有结果但发不出

```powershell
docker compose -f docker-compose.windows.yml exec astrbot ffmpeg -version
docker compose -f docker-compose.windows.yml logs --tail 200 astrbot
```

- ffmpeg 不存在时，需要换带 ffmpeg 的镜像或重新构建
- 语音失败时可以回复 `1 下载` 改发文件
- 文件太大时检查适配器和插件的大小限制

## 和云端部署的区别

| | Windows 本地 | 云服务器 |
|---|---|---|
| 需要一直开机 | 是 | 服务器持续运行 |
| QQ 登录 | noVNC 扫码 | noVNC 扫码 |
| 性能 | 受本机配置影响 | 取决于服务器 |
| 公网访问 | 默认不行，仅本机 | 需要 SSH 隧道 |
| 数据 | `data\` | `data/` |

功能完全一样，插件、人格、记忆、涟漪、相册都可用。
