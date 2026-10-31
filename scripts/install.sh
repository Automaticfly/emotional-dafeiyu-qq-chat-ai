#!/usr/bin/env bash
# 大肥鱼 QQ 机器人 · 一键安装脚本
#
# 用法：
#   bash scripts/install.sh
#
# 作用：
#   1. 检查 Docker 和目录权限
#   2. 创建 data/ 等运行目录
#   3. 生成 .env 和 SnowLuma OneBot 配置
#   4. 构建并启动 AstrBot + SnowLuma
#   5. 把初始账号密码保存到 data/INITIAL_CREDENTIALS.txt
#
# 如果提示权限不足，请用 sudo 重新运行，或把用户加入 docker 组。
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$(pwd)"
CRED_FILE="$ROOT/data/INITIAL_CREDENTIALS.txt"

# ---------------------------------------------------------------- 输出函数
info() { printf '\033[32m[信息]\033[0m %s\n' "$*"; }
warn() { printf '\033[33m[提示]\033[0m %s\n' "$*"; }
err() { printf '\033[31m[错误]\033[0m %s\n' "$*" >&2; }

# ---------------------------------------------------------------- 权限检查
# 目录不可写时，后面创建 data/ 和 .env 会失败，提前给出 sudo 提示。
if [ ! -w "$ROOT" ]; then
    err "当前目录没有写权限：$ROOT"
    warn "请用 sudo 重新运行：sudo bash scripts/install.sh"
    warn "或者把当前用户加入目录所属组后重试。"
    exit 1
fi

# Docker 命令不存在时，给出安装提示。
if ! command -v docker >/dev/null 2>&1; then
    err "没有找到 docker，请先安装 Docker。"
    warn "Ubuntu / Debian 可以执行：curl -fsSL https://get.docker.com | sudo sh"
    exit 1
fi

# docker info 失败通常是当前用户没有 Docker socket 权限。
if ! docker info >/dev/null 2>&1; then
    err "当前用户无法访问 Docker，通常是权限不够。"
    warn "请用 sudo 重新运行：sudo bash scripts/install.sh"
    warn "或把用户加入 docker 组：sudo usermod -aG docker \$USER && newgrp docker"
    exit 1
fi

if docker compose version >/dev/null 2>&1; then
    COMPOSE=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
    COMPOSE=(docker-compose)
else
    err "没有找到 docker compose，请安装 Docker Compose 插件。"
    exit 1
fi
compose() { "${COMPOSE[@]}" "$@"; }

# ---------------------------------------------------------------- 工具函数
generate_password() {
    if command -v openssl >/dev/null 2>&1; then
        openssl rand -hex 8
    else
        LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom | head -c 16 || true
    fi
}

# 用 sudo 运行时，把 data/ 和 .env 还给实际用户，避免以后每次都要 sudo。
fix_ownership() {
    if [ -n "${SUDO_USER:-}" ] && [ "${SUDO_USER}" != "root" ]; then
        chown -R "$SUDO_USER":"$(id -gn "$SUDO_USER")" "$ROOT/data" 2>/dev/null || true
        chown "$SUDO_USER":"$(id -gn "$SUDO_USER")" "$ROOT/.env" 2>/dev/null || true
    fi
}

# 从容器日志里抓一行，顺便去掉终端颜色控制符。
capture_log_line() {
    local service="$1"
    local pattern="$2"
    compose logs --no-color "$service" 2>&1 |
        sed -E 's/\x1b\[[0-9;]*[mK]//g' |
        grep -m1 -E "$pattern" || true
}

# ---------------------------------------------------------------- 1. 数据目录
info "第 1 步：创建运行目录"
mkdir -p \
    data/config \
    data/plugin_data \
    data/logs \
    data/temp \
    data/attachments \
    data/snowluma/config \
    data/snowluma-qq-config \
    data/snowluma-qq-data
info "已创建 data/ 目录"

# 表情包素材从仓库复制到插件数据目录，只在第一次安装时复制。
sticker_target="data/plugin_data/astrbot_plugin_dafeiyu_social/stickers"
mkdir -p "$sticker_target"
if [ ! -d "$sticker_target/dafeiyu" ]; then
    cp -R assets/stickers/dafeiyu "$sticker_target/dafeiyu"
    info "已复制表情包素材到 $sticker_target/dafeiyu"
fi

# ---------------------------------------------------------------- 2. 环境文件
info "第 2 步：准备 .env 配置"
if [ ! -f .env ]; then
    cp .env.example .env
    info "已生成 .env"
fi

# 占位密码没有改过时，自动生成一个随机密码，避免用户漏改。
if grep -q '^VNC_PASSWORD=change-this-vnc-password$' .env; then
    VNC_GENERATED="$(generate_password)"
    sed -i.bak "s/^VNC_PASSWORD=.*/VNC_PASSWORD=${VNC_GENERATED}/" .env
    rm -f .env.bak
    info "已自动生成 noVNC 密码并写入 .env"
fi
VNC_PASSWORD_VALUE="$(grep '^VNC_PASSWORD=' .env | head -n1 | cut -d= -f2- || true)"

# ---------------------------------------------------------------- 3. OneBot
info "第 3 步：准备 SnowLuma OneBot 配置"
if [ ! -f data/snowluma/config/onebot.json ]; then
    cp config/snowluma-onebot.example.json data/snowluma/config/onebot.json
    info "已生成 data/snowluma/config/onebot.json"
    warn "里面的 accessToken 还是占位值，需要和 AstrBot 平台 Token 保持一致。"
fi

fix_ownership

# ---------------------------------------------------------------- 4. 构建启动
info "第 4 步：构建镜像（第一次会比较慢，请耐心等待）"
compose build

info "第 5 步：启动容器"
compose up -d

# ---------------------------------------------------------------- 5. 初始账号
# 面板密码是首次启动时随机生成的，只在日志里出现一次。
# 这里等面板启动后抓取，写入 data/INITIAL_CREDENTIALS.txt。
collect_credentials() {
    local waited=0
    local ab_user_line="" ab_pass_line="" sl_line=""

    info "正在等待面板启动并收集初始账号密码（最多 2 分钟）"
    while [ "$waited" -lt 120 ]; do
        if [ -z "$ab_pass_line" ]; then
            ab_user_line="$(capture_log_line astrbot "Initial username")"
            ab_pass_line="$(capture_log_line astrbot "Initial password")"
        fi
        if [ -z "$sl_line" ]; then
            sl_line="$(capture_log_line snowluma "initial credentials")"
        fi
        if [ -n "$ab_pass_line" ] && [ -n "$sl_line" ]; then
            break
        fi
        sleep 3
        waited=$((waited + 3))
    done

    local ab_user="astrbot" ab_pass="未捕获" sl_user="admin" sl_pass="未捕获"
    if [ -n "$ab_user_line" ]; then
        ab_user="$(printf '%s' "$ab_user_line" | sed -E 's/.*Initial username:[[:space:]]*//; s/[[:space:]]*$//')"
    fi
    if [ -n "$ab_pass_line" ]; then
        ab_pass="$(printf '%s' "$ab_pass_line" | sed -E 's/.*Initial password:[[:space:]]*//; s/[[:space:]]*$//')"
    fi
    if [ -n "$sl_line" ]; then
        sl_user="$(printf '%s' "$sl_line" | sed -E 's/.*user=([^ ]+).*/\1/')"
        sl_pass="$(printf '%s' "$sl_line" | sed -E 's/.*password=([^ ]+).*/\1/')"
    fi

    {
        echo "# 大肥鱼 QQ 机器人 · 初始登录信息"
        echo "# 生成时间：$(date '+%F %T')"
        echo "# 登录后请立即修改密码，并删除本文件。"
        echo
        echo "[AstrBot 面板]"
        echo "地址：http://127.0.0.1:6185"
        echo "用户名：${ab_user}"
        echo "密码：${ab_pass}"
        echo
        echo "[SnowLuma 面板]"
        echo "地址：http://127.0.0.1:5099"
        echo "用户名：${sl_user}"
        echo "密码：${sl_pass}"
        echo
        echo "[noVNC 远程桌面]"
        echo "地址：http://127.0.0.1:6081/vnc.html?autoconnect=1"
        echo "密码：${VNC_PASSWORD_VALUE:-见 .env 的 VNC_PASSWORD}"
        echo
        echo "[绑定主人]"
        echo "1. 机器人账号请使用 QQ 小号登录"
        echo "2. 用你的大号先加机器人小号为好友"
        echo "3. 用大号私聊机器人，发送：/认主 你的QQ"
        echo "4. 绑定后 AstrBot 会自动重启一次"
        echo
        echo "[下一步]"
        echo "1. 登录 AstrBot，配置模型 Provider 和 OneBot 平台"
        echo "2. 登录 SnowLuma，确认 QQ 进程在线"
        echo "3. 打开 noVNC，用手机 QQ 扫码登录"
        echo "4. 让 AstrBot 平台 Token 与 SnowLuma 的 accessToken 保持一致"
        echo
        echo "[忘记密码时]"
        echo "AstrBot："
        echo "  docker exec -it astrbot astrbot conf set dashboard.password '新密码'"
        echo "  docker compose restart astrbot"
        echo "SnowLuma："
        echo "  docker compose stop snowluma"
        echo "  mv data/snowluma/config/webui.json data/snowluma/config/webui.json.bak"
        echo "  docker compose start snowluma"
        echo "  docker compose logs snowluma | grep 'initial credentials'"
        echo
        echo "[服务器部署]"
        echo "端口只绑定 127.0.0.1，请先建 SSH 隧道："
        echo "ssh -N -L 6185:127.0.0.1:6185 -L 5099:127.0.0.1:5099 -L 6081:127.0.0.1:6081 root@YOUR_SERVER_IP"
    } >"$CRED_FILE"
    chmod 600 "$CRED_FILE"
    fix_ownership
}

if [ -f "$CRED_FILE" ]; then
    warn "已存在初始登录文件，保留原内容：$CRED_FILE"
else
    collect_credentials
fi

# ---------------------------------------------------------------- 6. 完成
echo
info "安装完成"
echo "  AstrBot 面板：http://127.0.0.1:6185"
echo "  SnowLuma 面板：http://127.0.0.1:5099"
echo "  noVNC 远程桌面：http://127.0.0.1:6081/vnc.html?autoconnect=1"
echo
warn "初始账号密码已保存到：data/INITIAL_CREDENTIALS.txt"
warn "登录后请立即修改两个面板的密码，并删除这个文件。"
echo
echo "首次使用顺序："
echo "  1. AstrBot 登录 → 配置模型 Provider"
echo "  2. SnowLuma 登录 → 确认 QQ 进程"
echo "  3. noVNC 扫码登录 QQ（建议用小号）"
echo "  4. AstrBot 新建 OneBot v11 平台，Token 与 SnowLuma 一致"
echo "  5. 用大号私聊机器人，发送 /认主 你的QQ"
echo
echo "机器人账号建议使用 QQ 小号；大号只用来认主和聊天。"
echo
echo "更新：bash scripts/update.sh"
echo "卸载：bash scripts/uninstall.sh"
