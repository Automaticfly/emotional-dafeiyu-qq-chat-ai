# 大肥鱼认主

部署完成后，用**大号**私聊机器人完成认主，并设置一个换主密码。

## 首次认主

机器人账号建议使用 **QQ 小号**，大号只用来认主和日常聊天。

1. 用大号加机器人小号为好友
2. 用大号私聊机器人：

```text
/认主 你的QQ号
```

3. 机器人校验「发送者 QQ」和「填写的 QQ」是否一致
4. 设置换主密码：

```text
/认主 密码 你的密码
```

密码要求：

- 8-64 位
- 至少一个字母和一个数字
- 不能是纯数字
- 不能和 QQ 号相同

## 更换主人

新大号先加机器人小号为好友，然后用新号本人私聊：

```text
/认主 更换 新QQ 换主密码
```

校验顺序：

1. 必须是私聊
2. 发送者 QQ 必须等于新 QQ
3. 新 QQ 不能和当前主人相同
4. 换主密码必须正确
5. 通过后：
   - 从 `admins_id` 移除旧 QQ
   - 把 `social` / `ripple` / `memory2` / `heart` 的配置改成新 QQ
   - 重写 `data/owner.json`
   - 自动重启 AstrBot

## 其他命令

```text
/认主 改密码 旧密码 新密码   修改换主密码
/认主 状态                   查看当前主人和密码状态
```

## AI 怎么知道谁是主人

认主插件会在每轮 LLM 请求时检查当前发言人：

- 私聊且 QQ 等于主人：在 system prompt 注入「当前是已确认主人」
- 群聊且 QQ 等于主人：注入「当前发言者是主人，但不要向群友透露身份和私聊内容」
- 其他 QQ：不注入，避免把普通朋友误认成主人

所以认主后问「主人是谁」，她会知道当前对话的人是主人；不会只靠人格提示词猜。

## 认主后会写入什么

| 文件 | 写入内容 |
|---|---|
| `data/config/astrbot_plugin_dafeiyu_social_config.json` | `admin_qq`、`admin_umo`、`qzone_auto_umo` |
| `data/config/astrbot_plugin_dafeiyu_ripple_config.json` | `admin_qq`、`admin_umo` |
| `data/config/astrbot_plugin_dafeiyu_memory2_config.json` | `main_umo` |
| `data/config/astrbot_plugin_dafeiyu_heart_config.json` | `main_umo` |
| `data/cmd_config.json` | `admins_id` |
| `data/owner.json` | 主人 QQ、UMO、绑定时间、密码哈希 |

## 安全规则

- 只接受私聊，群里发 `/认主` 会被拒绝
- 首次认主必须用要绑定的 QQ 本人发送
- 换主必须用新 QQ 本人发送，并输入换主密码
- 密码只保存 PBKDF2 哈希，不保存明文
- 密码比较使用常量时间
- 连续输错 5 次锁定 10 分钟
- 回复里不会回显密码，也不会把密码写进日志
- 默认不认「我是主人」这类文字声明，只认配置

## 忘记密码

只能走服务器手动重置：

1. 停止 AstrBot
2. 删除 `data/owner.json`
3. 从 `data/cmd_config.json` 的 `admins_id` 删除旧 QQ
4. 启动 AstrBot
5. 新号重新 `/认主`

## 注意

- 密码是在 QQ 聊天里发送的，平台和日志理论上可能看到这条消息
- 不要用其他账号的密码，建议单独设一个换主密码
- 换主后旧 QQ 的聊天记录、记忆、日记、相册仍然留在 `data/`，不会自动迁移或删除
- `/认主` 只写本地配置，不修改 AstrBot 源码
