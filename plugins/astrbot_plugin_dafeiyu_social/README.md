# 大肥鱼社交与媒体

这个插件让大肥鱼可以：

- 发 QQ 空间动态（文字 / 多图 / 权限）
- 在聊天里发图片
- 发 QQ 原生小黄脸表情
- 发本地表情包图片/GIF
- 通过 LLM 工具在聊天中主动发图或表情

## 命令

### 发图

```text
/发图 https://example.com/a.jpg
/发图 素材名
/发图 素材名 | 一句话说明
/发图
```

不带参数时从图片素材库随机选一张。

### 表情

```text
/表情 微笑
/表情 14
/表情 微笑 偷笑
/表情
```

支持常用中文名称和 QQ 表情 ID；不带参数随机一个。

### 表情包

```text
/表情包
/表情包 素材名
/表情包素材名
/表情包 001
/表情包 不服气
/表情包 生日快乐
/表情包 素材名 | 一句话说明
/表情包列表
/表情包查 生日
```

支持名称、编号、含义和 OCR 文字模糊匹配。明确点名某个表情包但找不到时，插件会提示匹配项，不再随便发一张。

不带参数随机一个本地表情包。

### QQ 空间动态

```text
/动态 今天天气不错
/动态 好友 今天天气不错
/动态 公开 今天天气不错 | https://example.com/a.jpg
/动态 私密 今天心情很好 | 图片素材名,另一张图片
/动态查
/动态删
/动态删 <tid>
```

权限：

- `公开`：所有人可见
- `好友`：好友可见（默认）
- `私密`：仅自己可见

图片支持网络 URL 或素材库中的文件名，最多 9 张。发布成功后会记住 tid，`/动态删` 不带参数时删除上一条。

## 自动表情包

插件内置了 `dafeiyu` 表情包目录：

- 100 个 240×240 GIF
- 来源：`https://github.com/diwer/ai-biaoqingbao`
- 默认有 30% 概率在文本回复后附加表情包
- 触发后可能连续发 1 或 2 条；两条可以相同或不同
- **单次回复最多 2 条，不会连续发送 3 条**
- 会参考当前心情、主人消息、回复内容和语境选择
- 同一会话有冷却，避免连续刷屏
- 回复已有图片/语音/视频时默认跳过

相关配置：

- `sticker_auto_enabled`
- `sticker_auto_probability`（默认 0.3）
- `sticker_auto_cooldown_seconds`
- `sticker_auto_skip_if_media`
- `sticker_auto_skip_if_face`
- `sticker_pack`
- `sticker_catalog_file`

手动查看：

```text
/表情包列表
/表情包 不服气
/表情包 001
/表情包
```

表情包作者说明与许可在：

```text
stickers/dafeiyu/NOTICE.txt
```

该套表情仅供个人非商业使用；如需商用请先联系原作者确认授权。

## 素材目录

云端路径：

```text
/opt/astrbot/data/plugin_data/astrbot_plugin_dafeiyu_social/images
/opt/astrbot/data/plugin_data/astrbot_plugin_dafeiyu_social/stickers
```

把 `.png` / `.jpg` / `.gif` / `.webp` 放进去即可，不需要重启 AstrBot。命令里的素材名就是去掉扩展名后的文件名。

例如：

```bash
scp -i <key> 我的图.png root@YOUR_SERVER_IP:/opt/astrbot/data/plugin_data/astrbot_plugin_dafeiyu_social/images/
scp -i <key> 开心.gif root@YOUR_SERVER_IP:/opt/astrbot/data/plugin_data/astrbot_plugin_dafeiyu_social/stickers/
```

然后发：

```text
/发图 我的图
/表情包 开心
```

## 原生表情包（可选）

普通图片/GIF 表情包会作为图片发送。如果要发送 QQ 原生 market face，可以在插件数据目录创建 `mfaces.json`：

```json
{
  "doge": {
    "emoji_id": "填写",
    "emoji_package_id": "填写",
    "key": "填写",
    "summary": "[doge]"
  }
}
```

然后：

```text
/表情包 doge
```

没有配置原生 market face 时，同名图片文件仍会作为普通图片发送。

## LLM 主动发送

配置开启 `allow_llm_media` 后，主人可以说：

```text
给我发个可爱的表情包
发张图片看看
来个微笑
```

大肥鱼可以调用 `dafeiyu_send_media` 工具主动发送。

动态自动发布默认关闭（`allow_llm_qzone=false`），建议只用 `/动态` 手动确认后发布，避免误发到公开空间。

## 安全

- 所有命令只允许配置的管理员 QQ 使用
- 默认不允许群聊使用
- 本地素材只能从插件数据目录读取，不能读取服务器其他文件
- 动态默认好友可见，公开需要显式发送 `公开`
