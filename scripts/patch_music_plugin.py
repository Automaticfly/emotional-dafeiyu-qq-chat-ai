#!/usr/bin/env python3
"""给已安装的 B 站音乐插件补上匿名 Cookie 和 HTTP 412 重试。

背景
----
插件市场里的「我想听歌！」（0.1.3，目录 astrbot_plugin_listen_music）和
「bili播放器」（0.2.5，目录 astrbot_plugin_bili_player）在未登录 B 站时，
请求视频详情可能被 B 站返回 HTTP 412。插件会把 412 当成“没有可播放版本”，
于是出现“晴天能放，永世的摇篮找不到”这类现象。

本脚本只修改**已经安装**的插件文件，不下载、不打包第三方源码：

1. 生成一组匿名设备 Cookie（buvid3 / buvid4 / b_nut / b_lsid / _uuid）；
2. 每次请求自动带上这些 Cookie；
3. 遇到 HTTP 412 时换一组 Cookie 重试一次；
4. 修改前备份原文件，修改后做 Python 语法检查。

用法
----
Linux / macOS / Windows（已安装 Python 3.10+）：

    python scripts/patch_music_plugin.py --path /opt/astrbot
    python scripts\\patch_music_plugin.py --path D:\\AstrBot

Docker 部署（宿主机没有 Python 也可以）：

    docker cp scripts/patch_music_plugin.py astrbot:/tmp/patch_music_plugin.py
    docker compose exec astrbot python /tmp/patch_music_plugin.py --path /AstrBot

只检查、不写入：

    python scripts/patch_music_plugin.py --path /opt/astrbot --dry-run

脚本是幂等的：已经打过补丁会直接跳过。支持 0.1.3 和 0.2.5 的
`core/bilibili.py`；如果目标版本结构变化，会报错并保留原文件。

注意：这是对第三方插件的本地兼容补丁。插件在 AstrBot 里更新或重装后，
补丁可能被覆盖，需要重新运行本脚本。
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

PLUGIN_NAMES = (
    "astrbot_plugin_listen_music",  # 0.1.3「我想听歌！」
    "astrbot_plugin_bili_player",   # 0.2.5「bili播放器」
)

IMPORT_ANCHOR = "\nimport time\n"
IMPORT_PATCH = "\nimport time\nimport uuid\n"

HELPER_ANCHOR = "\ndef _clean_cookie_map("
HELPER = '''

def _anonymous_cookies() -> dict[str, str]:
    """Return anonymous device cookies that keep Bilibili from returning HTTP 412."""

    return {
        "buvid3": f"{uuid.uuid4()}infoc",
        "buvid4": f"{uuid.uuid4()}infoc",
        "b_nut": str(int(time.time())),
        "b_lsid": uuid.uuid4().hex[:16].upper(),
        "_uuid": f"{uuid.uuid4()}infoc",
    }

'''

INIT_ANCHOR = "        self._credentials_getter = credentials_getter\n"
INIT_PATCH = INIT_ANCHOR + "        self._anonymous_cookies = _anonymous_cookies()\n"

OLD_CREDENTIALS = '''    async def _credentials(self) -> dict[str, str]:
        if self._credentials_getter is None:
            return {}
        result = self._credentials_getter()
        if inspect.isawaitable(result):
            result = await result
        return _clean_cookie_map(result if isinstance(result, Mapping) else None)
'''

NEW_CREDENTIALS = '''    async def _credentials(self) -> dict[str, str]:
        account_cookies: dict[str, str] = {}
        if self._credentials_getter is not None:
            result = self._credentials_getter()
            if inspect.isawaitable(result):
                result = await result
            account_cookies = _clean_cookie_map(
                result if isinstance(result, Mapping) else None
            )
        return {**self._anonymous_cookies, **account_cookies}
'''

OLD_WBI = '''            query = urlencode(tuple(signed_params.items()), quote_via=quote, safe="")
            payload, _ = await self._request_json(f"{endpoint}?{query}")
            code = _to_int(payload.get("code"), -1)
'''

NEW_WBI = '''            query = urlencode(tuple(signed_params.items()), quote_via=quote, safe="")
            try:
                payload, _ = await self._request_json(f"{endpoint}?{query}")
            except BilibiliError as exc:
                if attempt == 0 and "HTTP 412" in str(exc):
                    self._anonymous_cookies = _anonymous_cookies()
                    await self._invalidate_wbi_key()
                    continue
                raise
            code = _to_int(payload.get("code"), -1)
'''

MARKERS = (
    "import uuid",
    "def _anonymous_cookies()",
    "self._anonymous_cookies = _anonymous_cookies()",
    "return {**self._anonymous_cookies, **account_cookies}",
    "HTTP 412",
)


class PatchError(RuntimeError):
    """目标文件结构不匹配，拒绝写入。"""


def iter_plugin_files(base: Path) -> list[Path]:
    """在 AstrBot 根目录、data/plugins 或 plugins 中查找音乐插件。"""

    base = base.expanduser().resolve()
    candidates: list[Path] = []

    # --path 直接指向插件目录
    if base.name in PLUGIN_NAMES and (base / "core" / "bilibili.py").is_file():
        candidates.append(base / "core" / "bilibili.py")

    roots = [
        base / "data" / "plugins",  # Linux / 原生 AstrBot
        base / "plugins",           # Windows compose 的 ./plugins 挂载
        base,                       # 直接包含插件目录的父目录
        base / "data" / "plugins_disabled",
        base / "backups" / "disabled-plugins",
    ]
    if base.name == "plugins" or base.name == "plugins_disabled":
        roots.append(base)

    for root in roots:
        if not root.is_dir():
            continue
        for name in PLUGIN_NAMES:
            path = root / name / "core" / "bilibili.py"
            if path.is_file():
                candidates.append(path)

    # 去重，保持顺序
    unique: list[Path] = []
    seen: set[Path] = set()
    for path in candidates:
        resolved = path.resolve()
        if resolved not in seen:
            seen.add(resolved)
            unique.append(resolved)
    return unique


def patch_text(text: str) -> tuple[str, bool]:
    """返回 (补丁后的文本, 是否有改动)。"""

    if all(marker in text for marker in MARKERS):
        return text, False

    changed = False

    if "\nimport uuid\n" not in text:
        if IMPORT_ANCHOR not in text:
            raise PatchError("找不到 `import time`，目标版本结构不匹配")
        text = text.replace(IMPORT_ANCHOR, IMPORT_PATCH, 1)
        changed = True

    if "def _anonymous_cookies()" not in text:
        if HELPER_ANCHOR not in text:
            raise PatchError("找不到 `_clean_cookie_map`，目标版本结构不匹配")
        text = text.replace(HELPER_ANCHOR, HELPER + HELPER_ANCHOR, 1)
        changed = True

    if "self._anonymous_cookies = _anonymous_cookies()" not in text:
        if INIT_ANCHOR not in text:
            raise PatchError("找不到 BilibiliClient 初始化位置，目标版本结构不匹配")
        text = text.replace(INIT_ANCHOR, INIT_PATCH, 1)
        changed = True

    if OLD_CREDENTIALS in text:
        text = text.replace(OLD_CREDENTIALS, NEW_CREDENTIALS, 1)
        changed = True
    elif NEW_CREDENTIALS not in text:
        raise PatchError("找不到 `_credentials` 旧实现，目标版本结构不匹配")

    if OLD_WBI in text:
        text = text.replace(OLD_WBI, NEW_WBI, 1)
        changed = True
    elif "if attempt == 0 and \"HTTP 412\" in str(exc):" not in text:
        raise PatchError("找不到 `_wbi_data` 旧实现，目标版本结构不匹配")

    for marker in MARKERS:
        if marker not in text:
            raise PatchError(f"补丁后缺少标记：{marker}")

    # 写入前做语法检查，避免留下半截文件
    compile(text, "<patched-bilibili>", "exec")
    return text, changed


def choose_backup_dir(base: Path, plugin_file: Path) -> Path:
    base = base.expanduser().resolve()
    if (base / "data").is_dir() or (base / "plugins").is_dir() or (base / "docker-compose.yml").is_file():
        return base / "backups" / "plugin-patches"
    return plugin_file.parent / "backups"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="给已安装的 B 站音乐插件补匿名 Cookie 和 412 重试"
    )
    parser.add_argument(
        "--path",
        default=".",
        help="AstrBot 根目录、data/plugins、plugins，或插件目录本身；默认当前目录",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只显示会改什么，不写文件",
    )
    args = parser.parse_args()

    base = Path(args.path)
    files = iter_plugin_files(base)
    if not files:
        print("[SKIP] 没有找到已安装的 B 站音乐插件。")
        print("       已查找：data/plugins、plugins、disabled 目录。")
        print("       先在 AstrBot 插件市场安装「我想听歌！」或「bili播放器」，再运行本脚本。")
        return 0

    exit_code = 0
    for path in files:
        plugin_name = path.parent.parent.name
        try:
            raw = path.read_bytes()
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            print(f"[ERROR] {path} 不是 UTF-8 文本：{exc}")
            exit_code = 1
            continue

        newline = "\r\n" if b"\r\n" in raw else "\n"
        normalized = text.replace("\r\n", "\n")
        try:
            patched, changed = patch_text(normalized)
        except PatchError as exc:
            print(f"[ERROR] {plugin_name}: {exc}")
            print(f"        文件未修改：{path}")
            exit_code = 1
            continue

        if not changed:
            print(f"[OK] {plugin_name} 已经是补丁版，跳过：{path}")
            continue

        if args.dry_run:
            print(f"[TODO] {plugin_name} 需要补丁（--dry-run，未写入）：{path}")
            continue

        backup_dir = choose_backup_dir(base, path)
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup = backup_dir / f"{plugin_name}_bilibili.py.orig.bak"
        if not backup.exists():
            shutil.copy2(path, backup)
            print(f"[BACKUP] {backup}")
        else:
            print(f"[BACKUP] 已存在，保留最早的备份：{backup}")

        output = patched.replace("\n", newline) if newline == "\r\n" else patched
        tmp = path.with_name(path.name + ".tmp")
        try:
            tmp.write_bytes(output.encode("utf-8"))
            os.replace(tmp, path)
        finally:
            if tmp.exists():
                tmp.unlink()

        print(f"[PATCHED] {plugin_name}：{path}")
        print("          重启 AstrBot 后生效。")

    if exit_code == 0:
        print("[DONE] 完成。若修改了文件，请重启 AstrBot。")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
