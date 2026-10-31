# data

运行时数据目录，默认不提交到 Git。

`scripts/install.sh` 会在这里创建：

- `cmd_config.json`（AstrBot 配置）
- `data_v4.db`（会话数据库）
- `config/`（插件配置）
- `plugin_data/`（长期记忆、心脏、日记、画像、日程）
- `snowluma/`（SnowLuma 数据）
- `snowluma-qq-config/`、`snowluma-qq-data/`（QQ 登录态）

这些内容可能包含 API Key、QQ 号、聊天记录、记忆、二维码和登录凭据，
请不要复制到公开仓库。
