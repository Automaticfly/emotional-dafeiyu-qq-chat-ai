# 增量安装到已有 AstrBot + SnowLuma

如果用户已经有一套能运行的 AstrBot + SnowLuma，不需要重装，只把「大肥鱼层」注入进去即可。

## 会注入什么

- `plugins/*`：心脏、记忆 2.0、涟漪、上下文、社交、表情、认主、LivingMemory
- `patches/*`：豆包 TTS、分段回复、会话 TTS 开关
- `persona/dafeiyu.md`：公开人格模板（只复制文件，不自动启用）
- `config/*.example.json`、`config/CONFIGURATION.md`：配置示例和说明

## 不会碰什么

- `data/config/`：插件实际配置
- `data/cmd_config.json`：AstrBot 主配置
- `.env`
- 用户的 `docker-compose.yml`
- `data/plugin_data/`：记忆、日记、画像、涟漪池、相册
- 用户自己加的插件

## 使用

### Linux

```bash
bash scripts/inject.sh --path /opt/astrbot
```

先看会做什么：

```bash
bash scripts/inject.sh --path /opt/astrbot --dry-run
```

不自动重启：

```bash
bash scripts/inject.sh --path /opt/astrbot --no-restart
```

### Windows

```powershell
powershell -ExecutionPolicy Bypass -File scripts\inject.ps1 -Path D:\dafeiyu
```

只预览：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\inject.ps1 -Path D:\dafeiyu -DryRun
```

不自动重启：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\inject.ps1 -Path D:\dafeiyu -NoRestart
```

## 选项

| 选项 | 说明 |
|---|---|
| `--path` / `-Path` | 目标部署目录，必须有 `docker-compose.yml` |
| `--dry-run` / `-DryRun` | 只显示动作，不写文件 |
| `--no-restart` / `-NoRestart` | 注入后不重启 AstrBot |
| `--no-patches` / `-NoPatches` | 不注入 `patches/` |
| `--with-dockerfile` / `-WithDockerfile` | 同时更新目标 `Dockerfile.astrbot`（先备份） |

## 插件目录怎么判断

按顺序：

1. 目标有 `plugins/` 且没有 `data/plugins/` → 用 `plugins/`
2. compose 里挂载了 `./plugins:/AstrBot/data/plugins` → 用 `plugins/`
3. 其他情况 → 用 `data/plugins/`

## 备份和清单

每次注入都会生成：

```text
backups/inject-<时间>/
.dafeiyu-layer.json
```

`backups/inject-<时间>/` 里是被覆盖前的文件；注入失败会用它自动回滚。

`.dafeiyu-layer.json` 记录：

- 注入版本和提交
- 注入时间
- 目标目录
- 插件目录
- 插件列表

## 健康检查和回滚

注入后默认重启 AstrBot，并轮询：

```text
http://127.0.0.1:6185/
```

最多等 2 分钟。如果起不来：

1. 自动恢复 `plugins`、`patches`、`persona`、`config`、`Dockerfile`
2. 重新启动 AstrBot
3. 再检查一次健康状态

如果回滚后仍失败，会提示查看：

```bash
docker compose logs astrbot
```

## 补丁说明

`patches/` 只有目标 Dockerfile 里存在 `COPY patches/...` 时才会在重建后生效。

如果目标 Dockerfile 没有这些行：

- 注入脚本会提示
- 可以用 `--with-dockerfile` 更新 Dockerfile
- 或者手动把补丁 COPY 进去后重建

## 底层加固

从 v1.3.0 起，仓库默认：

- 锁定 AstrBot 基础镜像摘要
- 锁定 SnowLuma 镜像摘要
- 给两个容器加健康检查
- 更新前备份 `data/config`、`data/cmd_config.json`、`.env`、compose、Dockerfile、插件和补丁
- 更新后健康检查失败时自动回滚

需要主动升级基础镜像时，在 `.env` 里设置：

```text
ASTRBOT_BASE_IMAGE=soulter/astrbot@sha256:...
SNOWLUMA_IMAGE=motricseven7/snowluma@sha256:...
```

然后重新 build / up。

## 注意

- 注入不会自动启用 `persona/dafeiyu.md`，需要在 AstrBot WebUI 里导入
- `config/*.example.json` 是示例，不会写到 `data/config`
- 如果目标部署不是用 Docker Compose 管理，注入脚本只会复制文件，不会重启
- 注入前建议先备份整个 `data/` 目录
