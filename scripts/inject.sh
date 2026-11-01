#!/usr/bin/env bash
# 大肥鱼层增量安装
#
# 把插件、补丁、人格模板和配置示例注入到已有的 AstrBot + SnowLuma 部署。
# 不会修改 data/config、data/cmd_config.json、.env 和用户的 docker-compose.yml。
#
# 用法：
#   bash scripts/inject.sh --path /opt/astrbot
#   bash scripts/inject.sh --path /opt/astrbot --dry-run
#   bash scripts/inject.sh --path /opt/astrbot --no-restart
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SOURCE_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

TARGET=""
DRY_RUN=0
NO_RESTART=0
WITH_PATCHES=1
WITH_DOCKERFILE=0

info() { printf '\033[32m[信息]\033[0m %s\n' "$*"; }
warn() { printf '\033[33m[提示]\033[0m %s\n' "$*"; }
err() { printf '\033[31m[错误]\033[0m %s\n' "$*" >&2; }
die() { err "$*"; exit 1; }

usage() {
    cat <<'EOF'
用法：bash scripts/inject.sh --path <已有部署目录> [选项]

选项：
  --path <目录>       目标 AstrBot + SnowLuma 部署目录，必填
  --dry-run           只显示会做什么，不写文件、不重启
  --no-restart        注入后不重启 AstrBot
  --no-patches        不注入 patches/
  --with-dockerfile   同时更新目标 Dockerfile.astrbot（会先备份）
  -h, --help          显示帮助

不会修改：
  data/config/  data/cmd_config.json  .env  docker-compose.yml

注入内容：
  plugins/*              -> 目标插件目录
  patches/*              -> 目标 patches/
  persona/dafeiyu.md     -> 目标 persona/
  config/*.example.json  -> 目标 config/
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --path)
            [ $# -ge 2 ] || die "--path 需要一个目录"
            TARGET="$2"
            shift
            ;;
        --dry-run) DRY_RUN=1 ;;
        --no-restart) NO_RESTART=1 ;;
        --no-patches) WITH_PATCHES=0 ;;
        --with-dockerfile) WITH_DOCKERFILE=1 ;;
        -h | --help)
            usage
            exit 0
            ;;
        *) die "未知参数：$1（用 --help 查看用法）" ;;
    esac
    shift
done

if [ -z "$TARGET" ]; then
    if [ -f "./docker-compose.yml" ] && [ -d "./data" ]; then
        TARGET="$(pwd)"
    elif [ -d "/opt/astrbot" ]; then
        TARGET="/opt/astrbot"
    else
        die "请用 --path 指定已有的 AstrBot + SnowLuma 部署目录"
    fi
fi

TARGET="$(cd "$TARGET" && pwd)"
[ -f "$TARGET/docker-compose.yml" ] || die "目标目录没有 docker-compose.yml：$TARGET"

COMPOSE_FILE="docker-compose.yml"
case "$(uname -s)" in
    MINGW* | MSYS* | CYGWIN*) COMPOSE_FILE="docker-compose.windows.yml" ;;
esac
if [ ! -f "$TARGET/$COMPOSE_FILE" ]; then
    COMPOSE_FILE="docker-compose.yml"
fi
[ -f "$TARGET/$COMPOSE_FILE" ] || die "目标目录没有可用的 compose 文件"

# 插件实际挂载位置：优先 plugins/，否则 data/plugins/。
PLUGIN_REL="data/plugins"
if [ -d "$TARGET/plugins" ] && [ ! -d "$TARGET/data/plugins" ]; then
    PLUGIN_REL="plugins"
elif grep -qE '^[[:space:]]*-[[:space:]]*\./plugins:/AstrBot/data/plugins' "$TARGET/$COMPOSE_FILE"; then
    PLUGIN_REL="plugins"
fi
PLUGIN_DIR="$TARGET/$PLUGIN_REL"

STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP="$TARGET/backups/inject-$STAMP"
MANIFEST="$TARGET/.dafeiyu-layer.json"

info "源目录：$SOURCE_ROOT"
info "目标目录：$TARGET"
info "插件目录：$PLUGIN_REL"
info "备份目录：$BACKUP"

run() {
    if [ "$DRY_RUN" = 1 ]; then
        echo "[dry-run] $*"
    else
        eval "$@"
    fi
}

backup_item() {
    local rel="$1"
    [ -e "$TARGET/$rel" ] || return 0
    if [ "$DRY_RUN" = 1 ]; then
        echo "[dry-run] 备份 $rel"
        return 0
    fi
    mkdir -p "$BACKUP/$(dirname "$rel")"
    cp -a "$TARGET/$rel" "$BACKUP/$rel"
}

replace_dir() {
    local src="$1"
    local rel="$2"
    [ -d "$src" ] || return 0
    backup_item "$rel"
    if [ "$DRY_RUN" = 1 ]; then
        echo "[dry-run] 替换目录 $rel"
        return 0
    fi
    rm -rf "$TARGET/$rel"
    mkdir -p "$(dirname "$TARGET/$rel")"
    cp -a "$src" "$TARGET/$rel"
    info "已更新 $rel"
}

replace_file() {
    local src="$1"
    local rel="$2"
    [ -f "$src" ] || return 0
    backup_item "$rel"
    if [ "$DRY_RUN" = 1 ]; then
        echo "[dry-run] 替换文件 $rel"
        return 0
    fi
    mkdir -p "$(dirname "$TARGET/$rel")"
    cp -f "$src" "$TARGET/$rel"
    info "已更新 $rel"
}

# ---------------------------------------------------------------- 注入
if [ ! -d "$PLUGIN_DIR" ]; then
    run "mkdir -p '$PLUGIN_DIR'"
fi

PLUGIN_NAMES=()
for dir in "$SOURCE_ROOT"/plugins/*/; do
    name="$(basename "$dir")"
    if [ ! -d "$dir" ]; then
        continue
    fi
    PLUGIN_NAMES+=("$name")
    replace_dir "$dir" "$PLUGIN_REL/$name"
done
[ ${#PLUGIN_NAMES[@]} -gt 0 ] || die "源目录 plugins/ 为空"

if [ "$WITH_PATCHES" = 1 ]; then
    replace_dir "$SOURCE_ROOT/patches" "patches"
fi

replace_file "$SOURCE_ROOT/persona/dafeiyu.md" "persona/dafeiyu.md"

for file in "$SOURCE_ROOT"/config/*.example.json "$SOURCE_ROOT"/config/CONFIGURATION.md; do
    [ -f "$file" ] || continue
    replace_file "$file" "config/$(basename "$file")"
done

if [ "$WITH_DOCKERFILE" = 1 ]; then
    replace_file "$SOURCE_ROOT/Dockerfile.astrbot" "Dockerfile.astrbot"
fi

# ---------------------------------------------------------------- 清单
if [ "$DRY_RUN" = 0 ]; then
    version="unknown"
    if command -v git >/dev/null 2>&1 && git -C "$SOURCE_ROOT" rev-parse --git-dir >/dev/null 2>&1; then
        version="$(git -C "$SOURCE_ROOT" describe --tags --always --dirty 2>/dev/null || echo unknown)"
    fi
    commit="unknown"
    if command -v git >/dev/null 2>&1 && git -C "$SOURCE_ROOT" rev-parse --git-dir >/dev/null 2>&1; then
        commit="$(git -C "$SOURCE_ROOT" rev-parse HEAD 2>/dev/null || echo unknown)"
    fi
    {
        printf '{\n'
        printf '  "layer": "dafeiyu",\n'
        printf '  "version": "%s",\n' "$version"
        printf '  "commit": "%s",\n' "$commit"
        printf '  "installed_at": "%s",\n' "$(date '+%Y-%m-%dT%H:%M:%S%z')"
        printf '  "target": "%s",\n' "$TARGET"
        printf '  "plugin_dir": "%s",\n' "$PLUGIN_REL"
        printf '  "plugins": ['
        first=1
        for name in "${PLUGIN_NAMES[@]}"; do
            if [ "$first" = 1 ]; then first=0; else printf ', '; fi
            printf '"%s"' "$name"
        done
        printf ']\n'
        printf '}\n'
    } >"$MANIFEST"
    info "已写入注入清单：$MANIFEST"
fi

# ---------------------------------------------------------------- 重启
if [ "$NO_RESTART" = 1 ]; then
    warn "按 --no-restart 跳过重启；配置在下次重启 AstrBot 后生效。"
    exit 0
fi

if ! command -v docker >/dev/null 2>&1; then
    warn "没有找到 docker，跳过重启。请手动重启 AstrBot。"
    exit 0
fi

cd "$TARGET"
if ! docker compose -f "$COMPOSE_FILE" config --services 2>/dev/null | grep -qx astrbot; then
    warn "没有在 compose 里找到 astrbot 服务，跳过自动重启。"
    exit 0
fi

needs_build=0
if [ "$WITH_PATCHES" = 1 ] && grep -q 'COPY patches/' "$TARGET/Dockerfile.astrbot" 2>/dev/null; then
    needs_build=1
fi
if [ "$WITH_PATCHES" = 1 ] && [ "$needs_build" = 0 ]; then
    warn "目标 Dockerfile 没有 COPY patches/，patches 已复制但不会自动生效。"
    warn "需要时加 --with-dockerfile 更新 Dockerfile，或手动把补丁 COPY 进去后 rebuild。"
fi

if ! command -v curl >/dev/null 2>&1; then
    warn "没有找到 curl，跳过健康检查。请手动确认 AstrBot 是否正常。"
    exit 0
fi

health_ok() {
    curl -fsS --max-time 5 http://127.0.0.1:6185/ >/dev/null 2>&1
}

wait_health() {
    local i
    for i in $(seq 1 40); do
        health_ok && return 0
        sleep 3
    done
    return 1
}

start_astrbot() {
    if [ "$needs_build" = 1 ]; then
        docker compose -f "$COMPOSE_FILE" build astrbot
        docker compose -f "$COMPOSE_FILE" up -d --force-recreate astrbot
    else
        docker compose -f "$COMPOSE_FILE" restart astrbot
    fi
}

info "重启 AstrBot 使注入生效"
start_astrbot

if wait_health; then
    info "AstrBot 已恢复健康：http://127.0.0.1:6185/"
    exit 0
fi

err "AstrBot 启动后健康检查失败，开始回滚"
if [ -d "$BACKUP" ]; then
    for name in "${PLUGIN_NAMES[@]}"; do
        rel="$PLUGIN_REL/$name"
        if [ -e "$BACKUP/$rel" ]; then
            rm -rf "$TARGET/$rel"
            mkdir -p "$(dirname "$TARGET/$rel")"
            cp -a "$BACKUP/$rel" "$TARGET/$rel"
        else
            rm -rf "$TARGET/$rel"
        fi
    done

    if [ -e "$BACKUP/patches" ]; then
        rm -rf "$TARGET/patches"
        cp -a "$BACKUP/patches" "$TARGET/patches"
    fi
    if [ -e "$BACKUP/persona/dafeiyu.md" ]; then
        mkdir -p "$TARGET/persona"
        cp -a "$BACKUP/persona/dafeiyu.md" "$TARGET/persona/dafeiyu.md"
    fi
    for file in "$SOURCE_ROOT"/config/*.example.json "$SOURCE_ROOT"/config/CONFIGURATION.md; do
        [ -f "$file" ] || continue
        rel="config/$(basename "$file")"
        if [ -e "$BACKUP/$rel" ]; then
            cp -a "$BACKUP/$rel" "$TARGET/$rel"
        else
            rm -f "$TARGET/$rel"
        fi
    done
    if [ -e "$BACKUP/Dockerfile.astrbot" ]; then
        cp -a "$BACKUP/Dockerfile.astrbot" "$TARGET/Dockerfile.astrbot"
    fi
    warn "已恢复注入前的文件：$BACKUP"
    start_astrbot || true
    if wait_health; then
        warn "回滚后 AstrBot 已恢复，请查看日志排查注入失败原因。"
    else
        err "回滚后仍未恢复，请查看 docker compose logs astrbot。"
    fi
else
    err "没有可用的备份目录，请手动恢复。"
fi
exit 1
