#!/usr/bin/env bash
# 大肥鱼 QQ 机器人 · 卸载脚本
#
# 默认：停止并删除容器，保留 data/ 和 .env。
# --purge：连 data/、.env 一起删除（聊天记录、记忆、相册、登录态）。
# --remove-images：同时删除项目镜像。
#
# 如果提示权限不足，请用 sudo 重新运行，或把用户加入 docker 组。
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$(pwd)"

PURGE=0
REMOVE_IMAGES=0
ASSUME_YES=0

usage() {
    cat <<'EOF'
用法：bash scripts/uninstall.sh [选项]

选项：
  --purge          连 data/ 和 .env 一起删除（聊天记录、记忆、相册、登录态）
  --remove-images  同时删除本项目构建/使用的镜像
  -y, --yes        跳过确认
  -h, --help       显示帮助

默认行为：
  停止并删除 AstrBot / SnowLuma 容器，保留 data/、.env 和程序文件。

注意：
  程序目录本身不会被删除；彻底清理请手动删除整个目录。
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --purge) PURGE=1 ;;
        --remove-images) REMOVE_IMAGES=1 ;;
        -y | --yes) ASSUME_YES=1 ;;
        -h | --help)
            usage
            exit 0
            ;;
        *)
            echo "未知参数：$1" >&2
            usage
            exit 2
            ;;
    esac
    shift
done

if [ ! -f "$ROOT/docker-compose.yml" ]; then
    echo "没有找到 docker-compose.yml，请在项目根目录运行。" >&2
    exit 1
fi

if [ "$PURGE" = 1 ]; then
    echo "将停止并删除容器，并永久删除："
    echo "  data/     聊天记录、长期记忆、涟漪池、相册、登录态"
    echo "  .env      VNC 密码等本地配置"
else
    echo "将停止并删除 AstrBot / SnowLuma 容器，保留 data/ 和 .env。"
fi

if [ "$ASSUME_YES" != 1 ]; then
    printf '继续？[y/N] '
    read -r answer
    case "$answer" in
        y | Y | yes | YES) ;;
        *)
            echo "已取消"
            exit 0
            ;;
    esac
fi

if command -v docker >/dev/null 2>&1; then
    if ! docker info >/dev/null 2>&1; then
        echo "提示：当前用户无权访问 Docker，跳过容器清理。"
        echo "如需清理容器，请用 sudo 重新运行：sudo bash scripts/uninstall.sh"
    elif docker compose version >/dev/null 2>&1; then
        docker compose down --remove-orphans || true
    elif command -v docker-compose >/dev/null 2>&1; then
        docker-compose down --remove-orphans || true
    else
        echo "没有 docker compose，跳过容器清理。"
    fi
else
    echo "没有检测到 docker，跳过容器清理。"
fi

if [ "$REMOVE_IMAGES" = 1 ] && command -v docker >/dev/null 2>&1; then
    docker rmi dafeiyu-astrbot:latest >/dev/null 2>&1 || true
    docker rmi motricseven7/snowluma:latest >/dev/null 2>&1 || true
    echo "已尝试删除项目镜像。"
fi

if [ "$PURGE" = 1 ]; then
    if [ ! -w "$ROOT" ]; then
        echo "错误：当前目录没有写权限，无法删除 data/ 和 .env。" >&2
        echo "请用 sudo 重新运行：sudo bash scripts/uninstall.sh --purge" >&2
        exit 1
    fi
    rm -rf -- "$ROOT/data"
    rm -f -- "$ROOT/.env" "$ROOT/update.log" "$ROOT/.last_update_sha"
    echo "已删除 data/、.env 和更新记录。"
    if [ -d "$ROOT/backups" ]; then
        echo "更新备份仍保留在：$ROOT/backups（确认不需要后可手动删除）"
    fi
fi

echo
echo "容器已卸载。"
echo "程序文件仍在：$ROOT"
echo "要彻底删除，请手动删除这个目录。"
