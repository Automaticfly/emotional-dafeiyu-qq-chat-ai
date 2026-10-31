# 大肥鱼认主

部署完成后，用**大号**私聊机器人发送：

```text
/认主 你的QQ号
```

机器人会校验「发送者 QQ」和「填写的 QQ」是否一致，一致才写入主人配置。

## 为什么要用小号登录机器人

- 机器人账号需要长期在线，用小号可以避免大号被风控或掉线影响日常使用
- 大号只用来认主和日常聊天
- 大号需要先加机器人小号为好友，才能私聊发 `/认主`

## 认主后会写入什么

| 文件 | 写入内容 |
|---|---|
| `data/config/astrbot_plugin_dafeiyu_social_config.json` | `admin_qq`、`admin_umo`、`qzone_auto_umo` |
| `data/config/astrbot_plugin_dafeiyu_ripple_config.json` | `admin_qq`、`admin_umo` |
| `data/config/astrbot_plugin_dafeiyu_memory2_config.json` | `main_umo` |
| `data/config/astrbot_plugin_dafeiyu_heart_config.json` | `main_umo` |
| `data/cmd_config.json` | `admins_id` 加入大号 QQ，移除占位值 `astrbot` |
| `data/owner.json` | 主人 QQ、UMO、绑定时间 |

写入完成后默认自动重启 AstrBot，让配置生效。

## 安全规则

- 只接受私聊，群里发 `/认主` 会被拒绝
- 发送者 QQ 必须等于填写的 QQ，不能替别人认主
- 已经绑定主人后，其他 QQ 不能覆盖
- 默认不接受「我是主人」这类文字声明，只认配置

## 更换主人

默认不允许直接覆盖。需要更换时：

1. 停止 AstrBot
2. 删除 `data/owner.json`
3. 启动 AstrBot
4. 用新的大号私聊发送 `/认主 新QQ`

也可以开启插件配置里的 `allow_rebind`，但不建议长期开启。

## 配置

| 配置 | 默认 | 说明 |
|---|---|---|
| `enabled` | true | 是否启用 `/认主` |
| `auto_restart` | true | 认主后是否自动重启 |
| `restart_delay_seconds` | 6 | 重启前等待秒数 |
| `allow_rebind` | false | 是否允许其他 QQ 覆盖主人 |

## 注意

- `/认主` 只负责写配置，不修改 AstrBot 源码
- 写入的是 `data/` 下的本地配置，不会上传到 GitHub
- 如果自动重启失败，手动执行 `docker compose restart astrbot` 即可
