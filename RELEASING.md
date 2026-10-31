# 发版流程

这份文档规定大肥鱼 QQ 机器人的版本号、更新日志和 GitHub Release 的发布方式。
以后每个版本都按这里的步骤走。

## 1. 版本号

使用[语义化版本](https://semver.org/lang/zh-CN/)：`主版本.次版本.修订号`。

| 变更类型 | 版本变化 | 例子 |
|---|---|---|
| 破坏性变更：配置格式、数据库结构、部署方式不兼容 | 主版本 +1 | `1.4.2` → `2.0.0` |
| 向后兼容的新功能 | 次版本 +1 | `1.4.2` → `1.5.0` |
| 向后兼容的修复、文档、脚本调整 | 修订号 +1 | `1.4.2` → `1.4.3` |
| 预发布 | 加后缀并标记 prerelease | `1.5.0-beta.1` |

规则：

- 版本号只在发布时递增，开发过程中不提前改。
- 首个公开版本是 `v1.0.0`。
- tag 使用带 `v` 前缀的注解 tag，例如 `v1.2.0`。
- tag 必须打在包含该版本 `CHANGELOG.md` 的提交上。

## 2. 更新日志

文件：`CHANGELOG.md`

格式：

```markdown
## [未发布]

## [1.1.0] - 2026-11-15

### 新增
- ...

### 变更
- ...

### 修复
- ...

### 移除
- ...

### 安全
- ...
```

要求：

- 开发中的改动先写在 `## [未发布]` 下面。
- 发版时把 `## [未发布]` 改成 `## [版本号] - YYYY-MM-DD`，并在顶部重新留一个空的 `## [未发布]`。
- 只写用户能感知的变化：功能、行为、配置、兼容性、安全。
- 修复要写清楚修了什么，不要只写「修复若干问题」。
- 破坏性变更必须单独标注 **破坏性变更**，并写迁移方法。
- 中文为主；命令、路径、配置键保持原文。
- 日期用发版当天。
- `1.0.0` 作为首个版本按功能域罗列，后续版本按「新增 / 变更 / 修复 / 移除 / 安全」分类。

## 3. 发版前检查

- [ ] 插件能正常导入：`python -m compileall` 或实际启动
- [ ] 关键功能有冒烟测试结果
- [ ] `CHANGELOG.md` 已更新，版本号和日期正确
- [ ] `README.md` 顶部的「当前版本」已更新
- [ ] 仓库里没有个人信息：QQ 号、API Key、聊天记录、记忆库、相册、二维码、服务器 IP
- [ ] `git status` 干净，改动已提交
- [ ] `scripts/update.sh --check` 在服务器上能正常识别新版本

隐私扫描至少覆盖：

```bash
git grep -n -E '你的QQ号|api[_-]?key|access[_-]?token|BEGIN .*PRIVATE KEY'
git grep -n -E '([0-9]{1,3}\.){3}[0-9]{1,3}'
```

## 4. 发布步骤

### 4.1 提交代码

```bash
git add -A
git commit -m "feat: ..."
git push origin main
```

提交信息用约定式前缀：`feat` / `fix` / `docs` / `refactor` / `chore`。

### 4.2 打 tag

```bash
git tag -a v1.1.0 -m "v1.1.0 一句话说明"
git push origin v1.1.0
```

### 4.3 创建 GitHub Release

- Release 标题：`v1.1.0 · 一句话主题`
- tag：`v1.1.0`
- 目标：`main` 上的发版提交
- 勾选 Latest release
- 预发布版本勾选 pre-release

Release 说明包含：

1. 一句话介绍这个版本
2. **本版亮点**：3-8 条用户能感知的变化
3. **破坏性变更 / 升级注意**（如果有）
4. 安装 / 更新 / 卸载命令
5. 文档链接（README、CHANGELOG、ARCHITECTURE、COMMANDS）
6. 数据与隐私说明
7. 许可与免责声明
8. 完整更新日志链接

模板：

````markdown
一句话说明这个版本。

## 本版亮点

- ...

## 升级注意

- 无 / 需要执行的迁移步骤

## 安装 / 更新

```bash
bash scripts/install.sh
bash scripts/update.sh
```

## 文档

- [更新日志](.../blob/v1.1.0/CHANGELOG.md)
- [README](.../blob/main/README.md)

## 数据与隐私

不包含账号、密钥、聊天记录、记忆库、相册或登录态。

## 许可与声明

仅限个人非商业使用；第三方内容版权归各自权利人所有。
````

### 4.4 中文编码

通过 GitHub API 发布 Release 时，必须把 JSON 转成 **UTF-8 字节**再发送，
并带上：

```text
Content-Type: application/json; charset=utf-8
```

否则中文会变成 `????`。发布后要打开页面确认。

### 4.5 发布后检查

- [ ] Release 页面中文正常，没有 `????`
- [ ] 状态是正式版（或正确的 prerelease）
- [ ] 显示为 Latest
- [ ] tag 指向正确的提交
- [ ] `CHANGELOG.md` 链接能打开
- [ ] 服务器上的文档和脚本已同步
- [ ] `bash scripts/update.sh --check` 显示最新

## 5. 同步服务器

服务器通过 `scripts/update.sh` 同步。发布后可以手动跑一次：

```bash
cd /opt/astrbot
bash scripts/update.sh
```

如果这个版本只改了文档，不想重启机器人，可以只同步文档和脚本，并把
`.last_update_sha` 更新到发版提交，避免 cron 再重建一次。

## 6. 回滚

- 代码回滚：`git revert <提交>` 后重新发一个修订版本
- 版本回滚：把 Release 标记为 pre-release 或撤回，并发布新的修订版本说明
- 服务器回滚：`backups/update-<时间>/` 里有更新前的代码备份

不要删除已经发布的 tag；用新的修订版本修正问题。
