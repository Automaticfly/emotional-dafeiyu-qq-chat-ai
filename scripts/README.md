# 部署脚本

三个脚本都从项目根目录执行，也可以先 `cd scripts` 再运行。

| 脚本 | 作用 |
|---|---|
| `install.sh` | 首次安装：检查权限、生成配置、构建启动、保存初始账号密码 |
| `update.sh` | 从 GitHub 同步最新代码，重建并重启容器 |
| `uninstall.sh` | 停止并删除容器，可选删除数据 |

## 安装

```bash
bash scripts/install.sh
```

安装过程会分步显示中文提示：

1. 创建 `data/` 目录
2. 生成 `.env`（没改 VNC 占位密码时会自动生成随机密码）
3. 生成 SnowLuma OneBot 配置
4. 构建镜像
5. 启动容器
6. 等待面板启动，收集初始账号密码

### 初始账号密码

安装完成后会自动生成：

```text
data/INITIAL_CREDENTIALS.txt
```

里面包含：

- AstrBot 面板地址、用户名、初始密码
- SnowLuma 面板地址、用户名、初始密码
- noVNC 地址和密码
- 忘记密码时的重置命令
- 服务器部署的 SSH 隧道命令

文件权限是 `600`，只有当前用户能读。登录后请立即修改两个面板的密码，并删除这个文件。

如果日志里没抓到密码（例如已经改过密码），文件里会写「未捕获」，并给出查看日志和重置的方法。

### 权限问题

如果出现：

```text
permission denied
Cannot connect to the Docker daemon
mkdir: cannot create directory
```

用 sudo 重新运行：

```bash
sudo bash scripts/install.sh
```

或者把当前用户加入 docker 组（推荐，之后不用 sudo）：

```bash
sudo usermod -aG docker $USER
newgrp docker
```

用 sudo 安装时，脚本会把 `data/` 和 `.env` 的所有权交还给 `SUDO_USER`，避免之后更新或删除时再次遇到权限问题。

## 更新

```bash
bash scripts/update.sh
```

脚本会自动判断部署方式：

- **git clone 的目录**：`git fetch` + 快进合并
- **下载 zip / tar.gz 的目录**：从 GitHub 下载最新压缩包，只替换代码和插件

两种方式都会保留：

- `data/`：聊天记录、记忆、涟漪池、相册、登录态、初始密码文件
- `.env`：VNC 密码等本地配置

更新时如果提示权限不足，用 `sudo bash scripts/update.sh`，或先把用户加入 docker 组。

### 常用选项

```bash
bash scripts/update.sh --check        # 只看有没有新版本
bash scripts/update.sh --no-restart   # 只同步代码，不重建容器
bash scripts/update.sh --stash        # 本地有改动时先 stash
bash scripts/update.sh --with-compose # 压缩包模式也更新 docker-compose.yml
bash scripts/update.sh --branch dev   # 跟其他分支
```

### 压缩包模式会更新什么

默认替换：

```text
Dockerfile.astrbot
.dockerignore / .gitattributes / .gitignore
README.md / ARCHITECTURE.md / COMMANDS.md / DISCLAIMER.md
LICENSE / PRIVACY.md / THIRD_PARTY_NOTICES.md
patches/
plugins/<项目自带插件>/
config/ persona/ assets/
scripts/
```

默认**不动**：

```text
data/
.env
docker-compose.yml   # 需要 --with-compose
```

旧文件会备份到 `backups/update-<时间>/`。自己额外放进 `plugins/` 或 `data/plugins/` 的插件不会被删除。

### 自动同步

用 cron 每天凌晨同步一次：

```bash
crontab -e
```

```cron
30 4 * * * cd /opt/astrbot && bash scripts/update.sh --auto >> update.log 2>&1
```

`--auto` 不会询问确认；如果工作区有本地改动会停止并在日志里说明。

用 systemd timer 也可以：

```ini
# /etc/systemd/system/dafeiyu-update.service
[Unit]
Description=Update Dafeiyu bot

[Service]
Type=oneshot
WorkingDirectory=/opt/astrbot
ExecStart=/bin/bash /opt/astrbot/scripts/update.sh --auto
```

```ini
# /etc/systemd/system/dafeiyu-update.timer
[Unit]
Description=Daily Dafeiyu update

[Timer]
OnCalendar=*-*-* 04:30:00
Persistent=true

[Install]
WantedBy=timers.target
```

```bash
systemctl daemon-reload
systemctl enable --now dafeiyu-update.timer
```

## 卸载

```bash
bash scripts/uninstall.sh
```

默认只停止并删除 AstrBot / SnowLuma 容器，保留 `data/` 和 `.env`，方便以后重装。

```bash
bash scripts/uninstall.sh --purge          # 连 data/、.env 一起删除
bash scripts/uninstall.sh --remove-images  # 同时删除项目镜像
bash scripts/uninstall.sh -y               # 不询问
```

`--purge` 会删除：

- `data/`：聊天记录、长期记忆、涟漪池、相册、QQ 登录态
- `.env`：VNC 密码等本地配置
- `update.log`、`.last_update_sha`

`backups/` 会保留，确认不需要后可以手动删除。

程序目录本身不会被脚本删除。彻底清理：

```bash
rm -rf /opt/astrbot
```

## Windows

脚本要在 **Git Bash** 或 **WSL** 里运行，PowerShell 不能直接执行 `.sh`。

Windows 下更新时，正在运行的 `update.sh` 会写成 `update.sh.new`，下次手动替换即可，避免文件被占用导致更新中断。

## 排查

- **提示“没有 .git，使用压缩包同步”**：正常，说明是下载的 zip 部署
- **提示工作区有改动**：先 `git status` 看看，提交或 `--stash`
- **拉取失败 / 下载超时**：确认能访问 GitHub；可以换镜像地址再试：
  `UPDATE_TARBALL_URL=https://gh-proxy.com/https://github.com/Automaticfly/emotional-dafeiyu-qq-chat-ai/archive/refs/heads/main.tar.gz bash scripts/update.sh`
- **重建失败**：先 `docker compose logs astrbot` 看错误；旧文件在 `backups/update-<时间>/`
- **更新后没变化**：确认执行了 `docker compose up -d`，或直接 `bash scripts/update.sh` 不加 `--no-restart`
