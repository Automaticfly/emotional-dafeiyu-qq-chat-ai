#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

mkdir -p \
  data/config \
  data/plugin_data \
  data/logs \
  data/temp \
  data/attachments \
  data/snowluma/config \
  data/snowluma-qq-config \
  data/snowluma-qq-data

# The sticker pack is kept in the repository under assets/ and copied into
# the plugin runtime data directory on first install.
sticker_target="data/plugin_data/astrbot_plugin_dafeiyu_social/stickers"
mkdir -p "$sticker_target"
if [ ! -d "$sticker_target/dafeiyu" ]; then
  cp -R assets/stickers/dafeiyu "$sticker_target/dafeiyu"
fi

if [ ! -f .env ]; then
  cp .env.example .env
  echo "已生成 .env，请先修改 VNC_PASSWORD 再启动。"
fi

if [ ! -f data/snowluma/config/onebot.json ]; then
  cp config/snowluma-onebot.example.json data/snowluma/config/onebot.json
  echo "已生成 SnowLuma OneBot 配置示例：data/snowluma/config/onebot.json"
  echo "请填写与 AstrBot 平台相同的 token。"
fi

docker compose build
docker compose up -d

echo
echo "容器已启动。"
echo "AstrBot WebUI:  http://127.0.0.1:6185"
echo "SnowLuma WebUI: http://127.0.0.1:5099"
echo "noVNC:          http://127.0.0.1:6081/vnc.html?autoconnect=1"
echo
echo "首次运行请在 AstrBot WebUI 完成 Provider、平台、插件和人格配置。"
