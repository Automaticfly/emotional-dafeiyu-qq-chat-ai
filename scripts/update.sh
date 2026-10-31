#!/usr/bin/env bash
# 大肥鱼 QQ 机器人 · 更新脚本
#
# 从 GitHub 同步最新代码：
#   - git clone 的目录：git fetch + 快进合并
#   - 下载 zip 的目录：下载最新压缩包，只替换代码和插件
# 始终保留 data/ 和 .env。
#
# 如果提示权限不足，请用 sudo 重新运行，或把用户加入 docker 组。
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$(pwd)"

# 目录不可写时无法替换代码，提前提示 sudo。
if [ ! -w "$ROOT" ]; then
    echo "错误：当前目录没有写权限：$ROOT" >&2
    echo "请用 sudo 重新运行：sudo bash scripts/update.sh" >&2
    exit 1
fi

REPO="Automaticfly/emotional-dafeiyu-qq-chat-ai"
BRANCH="${UPDATE_BRANCH:-main}"
REMOTE="${UPDATE_REMOTE:-origin}"
TARBALL_URL="${UPDATE_TARBALL_URL:-https://github.com/${REPO}/archive/refs/heads/${BRANCH}.tar.gz}"
COMMIT_API="https://api.github.com/repos/${REPO}/commits/${BRANCH}"
STATE_FILE="${ROOT}/.last_update_sha"
CURRENT_SCRIPT="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"

CHECK_ONLY=0
NO_RESTART=0
ASSUME_YES=0
AUTO=0
STASH=0
WITH_COMPOSE=0

usage() {
    cat <<'EOF'
用法：bash scripts/update.sh [选项]

选项：
  --check           只检查有没有新版本，不下载、不重启
  --no-restart      只更新代码，不重建镜像、不重启容器
  --stash           工作区有改动时先 git stash，再更新
  --with-compose    压缩包模式下同时更新 docker-compose.yml（默认不动）
  --auto            无人值守模式，供 cron / systemd timer 使用
  -y, --yes         跳过确认
  --branch <名称>   指定分支，默认 main
  -h, --help        显示帮助

更新内容：
  - git 仓库：git fetch + 快进合并
  - 压缩包部署：从 GitHub 下载最新压缩包，只替换代码和插件
  - 始终保留 data/ 和 .env

示例：
  bash scripts/update.sh
  bash scripts/update.sh --check
  bash scripts/update.sh --no-restart
EOF
}

log() {
    printf '[%s] %s\n' "$(date '+%F %T')" "$*"
}

die() {
    log "错误：$*" >&2
    exit 1
}

confirm() {
    [ "$ASSUME_YES" = 1 ] && return 0
    printf '%s [y/N] ' "$1"
    read -r answer
    case "$answer" in
        y | Y | yes | YES) return 0 ;;
        *) return 1 ;;
    esac
}

compose() {
    if docker compose version >/dev/null 2>&1; then
        docker compose "$@"
    else
        docker-compose "$@"
    fi
}

fetch_commit_sha() {
    curl -fsSL --max-time 20 "$COMMIT_API" 2>/dev/null |
        sed -n 's/^[[:space:]]*"sha": "\([0-9a-f]\{40\}\)".*/\1/p' |
        head -n 1
}

sync_plugins_to_data() {
    [ -d "$ROOT/plugins" ] || return 0
    [ -d "$ROOT/data/plugins" ] || return 0
    local dir name
    for dir in "$ROOT"/plugins/*/; do
        name="$(basename "$dir")"
        if [ -d "$ROOT/data/plugins/$name" ]; then
            rm -rf -- "$ROOT/data/plugins/$name"
            cp -a "$dir" "$ROOT/data/plugins/$name"
        fi
    done
    log "已同步 plugins/ 到 data/plugins/"
}

while [ $# -gt 0 ]; do
    case "$1" in
        --check) CHECK_ONLY=1 ;;
        --no-restart) NO_RESTART=1 ;;
        --stash) STASH=1 ;;
        --with-compose) WITH_COMPOSE=1 ;;
        --auto)
            AUTO=1
            ASSUME_YES=1
            ;;
        -y | --yes) ASSUME_YES=1 ;;
        --branch)
            [ $# -ge 2 ] || die "--branch 需要一个值"
            BRANCH="$2"
            shift
            ;;
        -h | --help)
            usage
            exit 0
            ;;
        *)
            usage
            exit 2
            ;;
    esac
    shift
done

if [ "$AUTO" = 1 ]; then
    log "无人值守模式"
fi

if [ "$NO_RESTART" = 0 ] && [ "$CHECK_ONLY" = 0 ]; then
    command -v docker >/dev/null 2>&1 || die "没有找到 docker，无法重建和重启"
    docker info >/dev/null 2>&1 || die "当前用户无权访问 Docker。请用 sudo 重新运行，或执行 sudo usermod -aG docker \$USER 后重新登录"
fi

if [ "$CHECK_ONLY" = 0 ] && [ "$NO_RESTART" = 0 ]; then
    confirm "将更新代码并重建、重启容器，data/ 和 .env 会保留。继续吗？" || {
        echo "已取消"
        exit 0
    }
fi

if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    log "检测到 git 仓库，使用 git 更新"
    git fetch --prune "$REMOTE" "$BRANCH"
    LOCAL_SHA="$(git rev-parse HEAD)"
    REMOTE_SHA="$(git rev-parse FETCH_HEAD)"
    if [ "$LOCAL_SHA" = "$REMOTE_SHA" ]; then
        log "已经是最新版本（${LOCAL_SHA:0:7}）"
        exit 0
    fi
    log "发现新版本：${LOCAL_SHA:0:7} -> ${REMOTE_SHA:0:7}"
    git --no-pager log --oneline "HEAD..FETCH_HEAD" | head -n 10
    if [ "$CHECK_ONLY" = 1 ]; then
        log "运行 bash scripts/update.sh 进行更新"
        exit 0
    fi
    if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
        if [ "$STASH" = 1 ]; then
            git stash push -u -m "update-stash-$(date +%Y%m%d-%H%M%S)"
            log "本地改动已保存到 git stash"
        else
            die "工作区有未提交的改动；先提交或 stash，或加 --stash"
        fi
    fi
    git merge --ff-only FETCH_HEAD || die "无法快进合并，请手动处理分叉后重试"
    sync_plugins_to_data
    UPDATE_SHA="$REMOTE_SHA"
else
    log "没有 .git，使用 GitHub 压缩包同步"

    REMOTE_SHA="$(fetch_commit_sha || true)"
    LAST_SHA="$(cat "$STATE_FILE" 2>/dev/null || true)"
    if [ -n "$REMOTE_SHA" ] && [ "$REMOTE_SHA" = "$LAST_SHA" ]; then
        log "已经是最新版本（${REMOTE_SHA:0:7}）"
        exit 0
    fi
    if [ -n "$REMOTE_SHA" ]; then
        log "远端版本：${REMOTE_SHA:0:7}（本地记录：${LAST_SHA:0:7}）"
    else
        log "无法查询远端提交（可能被限流），直接下载最新压缩包"
    fi

    if [ "$CHECK_ONLY" = 1 ]; then
        if [ -z "$REMOTE_SHA" ]; then
            log "无法确认远端版本，建议直接运行 bash scripts/update.sh"
        else
            log "有可用更新"
        fi
        exit 0
    fi

    TMP_DIR="$(mktemp -d)"
    trap 'rm -rf "$TMP_DIR"' EXIT
    log "下载 $TARBALL_URL"
    if command -v curl >/dev/null 2>&1; then
        curl -fL --retry 3 --connect-timeout 20 --max-time 300 \
            "$TARBALL_URL" -o "$TMP_DIR/repo.tar.gz"
    elif command -v wget >/dev/null 2>&1; then
        wget -O "$TMP_DIR/repo.tar.gz" "$TARBALL_URL"
    else
        die "需要 curl 或 wget 才能下载更新"
    fi
    tar -xzf "$TMP_DIR/repo.tar.gz" -C "$TMP_DIR"
    SRC="$TMP_DIR/${REPO##*/}-${BRANCH}"
    [ -f "$SRC/docker-compose.yml" ] || die "压缩包结构不正确"

    BACKUP="$ROOT/backups/update-$(date +%Y%m%d-%H%M%S)"
    mkdir -p "$BACKUP"

    backup_item() {
        local rel="$1"
        [ -e "$ROOT/$rel" ] || return 0
        mkdir -p "$BACKUP/$(dirname "$rel")"
        cp -a "$ROOT/$rel" "$BACKUP/$rel"
    }

    replace_path() {
        local rel="$1"
        local src_rel="${2:-$1}"
        case "$rel" in
            /* | *..*) die "非法路径：$rel" ;;
        esac
        case "$src_rel" in
            /* | *..*) die "非法源路径：$src_rel" ;;
        esac
        [ -e "$SRC/$src_rel" ] || return 0
        backup_item "$rel"
        rm -rf -- "$ROOT/$rel"
        mkdir -p "$(dirname "$ROOT/$rel")"
        cp -a "$SRC/$src_rel" "$ROOT/$rel"
        log "更新 $rel"
    }

    replace_files_in() {
        local rel="$1"
        local file sub
        [ -d "$SRC/$rel" ] || return 0
        while IFS= read -r -d '' file; do
            sub="${file#"$SRC/"}"
            backup_item "$sub"
            mkdir -p "$(dirname "$ROOT/$sub")"
            if [ "$ROOT/$sub" = "$CURRENT_SCRIPT" ]; then
                case "$(uname -s)" in
                    MINGW* | MSYS* | CYGWIN*)
                        cp -a "$file" "$ROOT/$sub.new"
                        log "当前脚本已写入 $sub.new（Windows 下请在下次手动替换）"
                        continue
                        ;;
                esac
            fi
            cp -a "$file" "$ROOT/$sub.tmp.$$"
            mv -f "$ROOT/$sub.tmp.$$" "$ROOT/$sub"
        done < <(find "$SRC/$rel" -type f -print0)
        log "更新 $rel"
    }

    for rel in \
        Dockerfile.astrbot .dockerignore .gitattributes .gitignore \
        README.md ARCHITECTURE.md COMMANDS.md CHANGELOG.md DISCLAIMER.md \
        LICENSE PRIVACY.md THIRD_PARTY_NOTICES.md RELEASING.md; do
        replace_path "$rel"
    done

    for rel in patches config persona assets; do
        replace_path "$rel"
    done

    # 正在运行的脚本用临时文件加 mv 替换，避免把自己删掉。
    replace_files_in scripts

    if [ -d "$ROOT/plugins" ] || [ ! -d "$ROOT/data/plugins" ]; then
        PLUGIN_REL="plugins"
    else
        PLUGIN_REL="data/plugins"
    fi
    for dir in "$SRC"/plugins/*/; do
        name="$(basename "$dir")"
        replace_path "$PLUGIN_REL/$name" "plugins/$name"
    done
    log "插件目录：$PLUGIN_REL"

    if [ "$WITH_COMPOSE" = 1 ]; then
        replace_path docker-compose.yml
    else
        log "跳过 docker-compose.yml（需要时加 --with-compose）"
    fi

    if [ -n "$REMOTE_SHA" ]; then
        printf '%s\n' "$REMOTE_SHA" >"$STATE_FILE"
    fi
    UPDATE_SHA="${REMOTE_SHA:-unknown}"
    log "代码已更新，旧文件备份在 $BACKUP"
fi

if [ "$NO_RESTART" = 1 ]; then
    log "按 --no-restart 跳过重建和重启，当前版本：${UPDATE_SHA:0:7}"
    exit 0
fi

log "重建镜像并启动容器"
compose build --pull
compose up -d
compose ps
log "更新完成：${UPDATE_SHA:0:7}"
