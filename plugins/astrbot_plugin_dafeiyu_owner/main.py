"""大肥鱼认主：用大号私聊绑定主人，并支持密码验证后更换主人。

流程：
1. 机器人账号使用 QQ 小号登录。
2. 部署者用大号私聊机器人，发送 ``/认主 大号QQ``。
3. 绑定后设置换主密码：``/认主 密码 你的密码``。
4. 需要换主时，新号发送 ``/认主 更换 新QQ 密码``。
   校验通过后清除旧 QQ 的管理员权限，写入新主人配置。

密码只保存 PBKDF2 哈希，不保存明文；连续输错会短暂锁定。
所有写入都只发生在本地 ``data/`` 目录，不修改插件源码。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import threading
import time
from datetime import datetime
from pathlib import Path

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, StarTools

PLUGIN_NAME = "astrbot_plugin_dafeiyu_owner"

# 每个插件需要写入的主人配置键。
OWNER_KEYS = {
    "astrbot_plugin_dafeiyu_social": ("admin_qq", "admin_umo", "qzone_auto_umo"),
    "astrbot_plugin_dafeiyu_ripple": ("admin_qq", "admin_umo"),
    "astrbot_plugin_dafeiyu_memory2": ("main_umo",),
    "astrbot_plugin_dafeiyu_heart": ("main_umo",),
}

QQ_PATTERN = re.compile(r"^\d{5,12}$")
COMMAND_PATTERN = re.compile(r"^/?认主(?:\s+(.+))?$")

PBKDF2_ITERATIONS = 200_000
PASSWORD_MIN = 8
PASSWORD_MAX = 64
MAX_FAILURES = 5
LOCK_SECONDS = 600


class DafeiyuOwnerPlugin(Star):
    """通过 /认主 绑定主人，并用密码保护换主流程。"""

    def __init__(self, context: Context, config: dict | None = None):
        super().__init__(context, config)
        self.cfg = config or {}
        self._failures: dict[str, tuple[int, float]] = {}
        try:
            self.data_root = Path(StarTools.get_data_dir(PLUGIN_NAME)).parents[1]
        except Exception:
            logger.exception("[owner] 无法解析 data 目录")
            self.data_root = Path("/AstrBot/data")

    # ------------------------------------------------------------------ paths
    @property
    def owner_file(self) -> Path:
        """Return the owner marker path."""
        return self.data_root / "owner.json"

    @property
    def config_dir(self) -> Path:
        """Return the plugin config directory."""
        return self.data_root / "config"

    @property
    def plugin_dir(self) -> Path:
        """Return the plugin source directory."""
        return self.data_root / "plugins"

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _read_json(path: Path) -> dict:
        """Read a JSON object, returning an empty dict on any problem.

        Args:
            path: File to read.

        Returns:
            Parsed object or an empty dict.
        """
        try:
            if not path.is_file():
                return {}
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError, TypeError):
            return {}

    @staticmethod
    def _write_json(path: Path, data: dict) -> None:
        """Write a JSON object with UTF-8 and a trailing newline.

        Args:
            path: Target file.
            data: Object to write.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _schema_defaults(self, plugin_name: str) -> dict:
        """Read default values from a plugin's config schema.

        Args:
            plugin_name: Plugin directory name.

        Returns:
            Mapping of config keys to defaults.
        """
        schema_path = self.plugin_dir / plugin_name / "_conf_schema.json"
        schema = self._read_json(schema_path)
        defaults: dict = {}
        for key, meta in schema.items():
            if isinstance(meta, dict) and "default" in meta:
                defaults[key] = meta["default"]
        return defaults

    # ------------------------------------------------------------------ password
    @staticmethod
    def _b64(data: bytes) -> str:
        """Encode bytes as base64 text.

        Args:
            data: Raw bytes.

        Returns:
            Base64 string.
        """
        return base64.b64encode(data).decode("ascii")

    @staticmethod
    def _unb64(text: object) -> bytes:
        """Decode base64 text.

        Args:
            text: Base64 string.

        Returns:
            Raw bytes.

        Raises:
            ValueError: When the input is not valid base64.
        """
        return base64.b64decode(str(text or ""), validate=True)

    def _hash_password(self, password: str, salt: bytes | None = None) -> dict:
        """Build a password hash record.

        Args:
            password: Plain password.
            salt: Optional fixed salt for tests.

        Returns:
            Hash record with algorithm, iterations, salt and hash.
        """
        salt = salt or secrets.token_bytes(16)
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS
        )
        return {
            "algo": "pbkdf2_sha256",
            "iterations": PBKDF2_ITERATIONS,
            "salt": self._b64(salt),
            "hash": self._b64(digest),
        }

    def _verify_password(self, password: str, record: object) -> bool:
        """Verify a password against a stored hash record.

        Args:
            password: Plain password.
            record: Stored hash record.

        Returns:
            True when the password matches.
        """
        if not isinstance(record, dict):
            return False
        try:
            if record.get("algo") != "pbkdf2_sha256":
                return False
            iterations = int(record.get("iterations") or 0)
            if iterations < 10_000:
                return False
            salt = self._unb64(record.get("salt"))
            expected = self._unb64(record.get("hash"))
            actual = hashlib.pbkdf2_hmac(
                "sha256", password.encode("utf-8"), salt, iterations
            )
            return hmac.compare_digest(actual, expected)
        except (ValueError, TypeError):
            return False

    @staticmethod
    def _password_problem(password: str, qq: str) -> str | None:
        """Return a human-readable password rule violation.

        Args:
            password: Candidate password.
            qq: Owner QQ, which must not be used as the password.

        Returns:
            Error message, or None when the password is acceptable.
        """
        if not PASSWORD_MIN <= len(password) <= PASSWORD_MAX:
            return f"密码长度需要 {PASSWORD_MIN}-{PASSWORD_MAX} 位。"
        if password.isdigit():
            return "密码不能是纯数字。"
        if not any(char.isalpha() for char in password):
            return "密码至少要有一个字母。"
        if not any(char.isdigit() for char in password):
            return "密码至少要有一个数字。"
        if password == qq:
            return "密码不能和 QQ 号相同。"
        if password.lower() in {"password", "12345678", "qwerty123", "abc12345"}:
            return "这个密码太常见了，换一个。"
        return None

    def _lock_remaining(self, qq: str) -> int:
        """Return remaining lock seconds for a sender.

        Args:
            qq: Sender QQ.

        Returns:
            Seconds until unlock, or 0 when not locked.
        """
        _count, until = self._failures.get(qq, (0, 0.0))
        return max(0, int(until - time.monotonic()))

    def _record_failure(self, qq: str) -> int:
        """Record a wrong password attempt.

        Args:
            qq: Sender QQ.

        Returns:
            Remaining attempts before lockout, or 0 when now locked.
        """
        count, until = self._failures.get(qq, (0, 0.0))
        if time.monotonic() >= until:
            count = 0
        count += 1
        if count >= MAX_FAILURES:
            self._failures[qq] = (0, time.monotonic() + LOCK_SECONDS)
            return 0
        self._failures[qq] = (count, 0.0)
        return MAX_FAILURES - count

    def _clear_failures(self, qq: str) -> None:
        """Clear failed attempts after a successful verification.

        Args:
            qq: Sender QQ.
        """
        self._failures.pop(qq, None)

    # ------------------------------------------------------------------ owner
    def _owner(self) -> dict:
        """Return the stored owner record.

        Returns:
            Owner record, or an empty dict.
        """
        return self._read_json(self.owner_file)

    def _write_owner(
        self, qq: str, umo: str, password: dict | None = None
    ) -> None:
        """Write the owner marker file.

        Args:
            qq: Owner QQ.
            umo: Owner private-chat UMO.
            password: Optional password hash record.
        """
        record = {
            "qq": qq,
            "umo": umo,
            "claimed_at": datetime.now().replace(microsecond=0).isoformat(),
        }
        if password:
            record["password"] = password
        self._write_json(self.owner_file, record)

    def _apply_owner(
        self,
        qq: str,
        umo: str,
        old_qq: str = "",
        password: dict | None = None,
    ) -> list[str]:
        """Write the owner into plugin configs and AstrBot admins.

        Args:
            qq: New owner QQ.
            umo: New owner private-chat UMO.
            old_qq: Previous owner QQ to remove from admins, if any.
            password: Password hash record to keep.

        Returns:
            Names of the config files that were written.
        """
        written: list[str] = []
        self.config_dir.mkdir(parents=True, exist_ok=True)

        for plugin_name, keys in OWNER_KEYS.items():
            if not (self.plugin_dir / plugin_name).is_dir():
                continue
            path = self.config_dir / f"{plugin_name}_config.json"
            merged = {
                **self._schema_defaults(plugin_name),
                **self._read_json(path),
            }
            for key in keys:
                merged[key] = umo if key.endswith("umo") else qq
            self._write_json(path, merged)
            written.append(path.name)

        cmd_path = self.data_root / "cmd_config.json"
        if not cmd_path.is_file():
            raise RuntimeError("cmd_config.json 不存在")
        cmd_config = self._read_json(cmd_path)
        if not cmd_config:
            raise RuntimeError("cmd_config.json 无法解析")
        admins = cmd_config.get("admins_id")
        if not isinstance(admins, list):
            admins = []
        excluded = {"astrbot"}
        if old_qq:
            excluded.add(old_qq)
        admins = [str(item) for item in admins if str(item) not in excluded]
        if qq not in admins:
            admins.append(qq)
        cmd_config["admins_id"] = admins
        self._write_json(cmd_path, cmd_config)
        written.append(cmd_path.name)

        self._write_owner(qq, umo, password)
        return written

    # ----------------------------------------------------------------- restart
    def _schedule_restart(self) -> None:
        """Restart AstrBot in a background thread after a short delay."""
        delay = 6
        try:
            delay = int(self.cfg.get("restart_delay_seconds", 6))
        except (TypeError, ValueError):
            delay = 6
        delay = max(1, min(delay, 60))

        def run() -> None:
            time.sleep(delay)
            try:
                from astrbot.core.process_restart import restart_process

                logger.info("[owner] 认主配置已更新，正在重启 AstrBot")
                restart_process(0)
            except Exception:
                logger.exception("[owner] 自动重启失败，请手动重启 AstrBot")

        threading.Thread(target=run, daemon=True).start()

    # ----------------------------------------------------------------- usage
    @staticmethod
    def _usage() -> str:
        """Return the command help text.

        Returns:
            Multi-line usage text.
        """
        return (
            "用法（都要私聊）：\n"
            "/认主 你的QQ                首次认主\n"
            "/认主 密码 你的密码         设置换主密码（8-64 位，含字母和数字）\n"
            "/认主 改密码 旧密码 新密码   修改换主密码\n"
            "/认主 更换 新QQ 密码        验证密码后更换主人\n"
            "/认主 状态                  查看当前主人和密码状态"
        )

    # ----------------------------------------------------------------- command
    @filter.command("认主")
    async def claim_owner(self, event: AstrMessageEvent):
        """用大号私聊绑定主人，或验证密码后更换主人。"""
        if not bool(self.cfg.get("enabled", True)):
            return

        if event.get_group_id() or not event.is_private_chat():
            yield event.plain_result("认主请私聊我发，不要在群里发。")
            return

        raw = str(getattr(event, "message_str", "") or "").strip()
        match = COMMAND_PATTERN.match(raw)
        if not match:
            yield event.plain_result(self._usage())
            return
        args = (match.group(1) or "").strip()
        if not args:
            yield event.plain_result(self._usage())
            return

        text, restart = await self._dispatch(event, args)
        if text:
            yield event.plain_result(text)
        if restart:
            self._schedule_restart()

    async def _dispatch(
        self, event: AstrMessageEvent, args: str
    ) -> tuple[str | None, bool]:
        """Dispatch one /认主 subcommand.

        Args:
            event: Current message event.
            args: Text after ``/认主``.

        Returns:
            Tuple of reply text and whether to restart.
        """
        parts = args.split()
        head = parts[0]

        if head == "状态":
            return self._status(), False
        if head == "密码":
            return self._set_password(event, parts), False
        if head == "改密码":
            return self._change_password(event, parts), False
        if head == "更换":
            return self._change_owner(event, parts)

        if head.isdigit() and len(parts) == 1:
            return self._initial_claim(event, head)
        return self._usage(), False

    def _status(self) -> str:
        """Return a private status summary."""
        owner = self._owner()
        if not owner:
            return "还没有认主。用大号私聊发送 /认主 你的QQ。"
        qq = str(owner.get("qq") or "")
        has_password = isinstance(owner.get("password"), dict)
        return (
            f"当前主人：QQ {qq}\n"
            f"换主密码：{'已设置' if has_password else '未设置'}"
        )

    def _initial_claim(
        self, event: AstrMessageEvent, target: str
    ) -> tuple[str, bool]:
        """Bind the first owner, or re-sync the current owner.

        Args:
            event: Current message event.
            target: QQ from the command.

        Returns:
            Tuple of reply text and whether to restart.
        """
        if not QQ_PATTERN.match(target):
            return "QQ 号应为 5-12 位数字，请检查后重发。", False
        sender = str(event.get_sender_id() or "")
        if sender != target:
            return (
                "你填写的 QQ 和当前发消息的账号不一致。\n"
                "请用要绑定的大号本人私聊我，发送 /认主 你的QQ。",
                False,
            )

        owner = self._owner()
        current = str(owner.get("qq") or "")
        if current and current != target:
            has_password = isinstance(owner.get("password"), dict)
            if has_password:
                return (
                    f"已经绑定过主人（QQ {current}）。\n"
                    f"如需更换，请用新号本人私聊发送：/认主 更换 {target} 你的换主密码。",
                    False,
                )
            return (
                f"已经绑定过主人（QQ {current}），但还没有设置换主密码。\n"
                "请先用旧主人私聊 /认主 密码 你的密码。\n"
                "如果旧号已经无法登录，请按文档在服务器手动删除 data/owner.json，"
                "并从 data/cmd_config.json 的 admins_id 移除旧 QQ。",
                False,
            )

        utc = str(getattr(event, "unified_msg_origin", "") or "")
        password = owner.get("password") if current == target else None
        try:
            self._apply_owner(
                target,
                utc,
                old_qq="" if current == target else current,
                password=password if isinstance(password, dict) else None,
            )
        except Exception:
            logger.exception("[owner] 写入主人配置失败")
            return "写入主人配置失败，请查看 AstrBot 日志。", False

        logger.info(f"[owner] 绑定主人 QQ {target} umo={utc}")
        if current == target:
            return "这个号已经绑定过，本次重新同步了插件配置。", True
        if isinstance(password, dict):
            return f"认主成功：QQ {target}\n已写入插件配置和 AstrBot 管理员列表。", True
        return (
            f"认主成功：QQ {target}\n"
            "请设置换主密码，之后换主会用它验证：\n"
            "/认主 密码 你的密码（8-64 位，含字母和数字）",
            True,
        )

    def _set_password(
        self, event: AstrMessageEvent, parts: list[str]
    ) -> str:
        """Set the initial change-owner password.

        Args:
            event: Current message event.
            parts: Command parts.

        Returns:
            Reply text.
        """
        if len(parts) != 2:
            return "用法：/认主 密码 你的密码"
        owner = self._owner()
        if not owner:
            return "还没有认主，先发送 /认主 你的QQ。"
        qq = str(owner.get("qq") or "")
        if str(event.get_sender_id() or "") != qq:
            return "只有当前主人本人才能设置换主密码。"
        if isinstance(owner.get("password"), dict):
            return "已经设置过换主密码。修改请用：/认主 改密码 旧密码 新密码"

        password = parts[1]
        problem = self._password_problem(password, qq)
        if problem:
            return problem
        owner["password"] = self._hash_password(password)
        owner["password_set_at"] = datetime.now().replace(microsecond=0).isoformat()
        self._write_json(self.owner_file, owner)
        logger.info(f"[owner] 主人 QQ {qq} 设置了换主密码")
        return "换主密码已设置。请牢记；忘记后只能在服务器手动重置。"

    def _change_password(
        self, event: AstrMessageEvent, parts: list[str]
    ) -> str:
        """Change the change-owner password.

        Args:
            event: Current message event.
            parts: Command parts.

        Returns:
            Reply text.
        """
        if len(parts) != 3:
            return "用法：/认主 改密码 旧密码 新密码"
        owner = self._owner()
        if not owner:
            return "还没有认主，先发送 /认主 你的QQ。"
        qq = str(owner.get("qq") or "")
        sender = str(event.get_sender_id() or "")
        if sender != qq:
            return "只有当前主人本人才能修改换主密码。"
        record = owner.get("password")
        if not isinstance(record, dict):
            return "还没有设置换主密码。请用：/认主 密码 你的密码"

        locked = self._lock_remaining(sender)
        if locked:
            return f"密码错误次数过多，请 {max(1, locked // 60)} 分钟后再试。"

        old_password, new_password = parts[1], parts[2]
        if not self._verify_password(old_password, record):
            remaining = self._record_failure(sender)
            if not remaining:
                return "密码错误次数过多，已锁定 10 分钟。"
            return f"旧密码不正确，还可以再试 {remaining} 次。"

        problem = self._password_problem(new_password, qq)
        if problem:
            return problem

        self._clear_failures(sender)
        owner["password"] = self._hash_password(new_password)
        owner["password_set_at"] = datetime.now().replace(microsecond=0).isoformat()
        self._write_json(self.owner_file, owner)
        logger.info(f"[owner] 主人 QQ {qq} 更新了换主密码")
        return "换主密码已更新。"

    def _change_owner(
        self, event: AstrMessageEvent, parts: list[str]
    ) -> tuple[str, bool]:
        """Verify the password and replace the owner.

        Args:
            event: Current message event.
            parts: Command parts.

        Returns:
            Tuple of reply text and whether to restart.
        """
        if len(parts) != 3:
            return "用法：/认主 更换 新QQ 换主密码", False
        new_qq, password = parts[1], parts[2]
        if not QQ_PATTERN.match(new_qq):
            return "新 QQ 号应为 5-12 位数字。", False

        sender = str(event.get_sender_id() or "")
        if sender != new_qq:
            return "请用要绑定的新 QQ 本人私聊发送 /认主 更换 新QQ 密码。", False

        owner = self._owner()
        if not owner:
            return "还没有认主，直接发送 /认主 你的QQ 即可。", False
        old_qq = str(owner.get("qq") or "")
        if old_qq == new_qq:
            return "新 QQ 和当前主人相同，不需要更换。", False
        record = owner.get("password")
        if not isinstance(record, dict):
            return (
                "还没有设置换主密码，无法验证换主。\n"
                "请先用当前主人发送 /认主 密码 你的密码；"
                "如果旧号已无法登录，请在服务器手动重置。",
                False,
            )

        locked = self._lock_remaining(sender)
        if locked:
            return f"密码错误次数过多，请 {max(1, locked // 60)} 分钟后再试。", False
        if not self._verify_password(password, record):
            remaining = self._record_failure(sender)
            if not remaining:
                return "密码错误次数过多，已锁定 10 分钟。", False
            return f"密码不正确，还可以再试 {remaining} 次。", False

        utc = str(getattr(event, "unified_msg_origin", "") or "")
        try:
            written = self._apply_owner(
                new_qq,
                utc,
                old_qq=old_qq,
                password=record,
            )
        except Exception:
            logger.exception("[owner] 更换主人写入失败")
            return "更换主人失败，请查看 AstrBot 日志。", False

        self._clear_failures(sender)
        logger.info(f"[owner] 主人已从 QQ {old_qq} 更换为 QQ {new_qq}")
        return (
            f"主人已更换为 QQ {new_qq}。\n"
            f"已清除旧 QQ {old_qq} 的管理员权限，并写入："
            + "、".join(written)
            + "\nAstrBot 将在几秒后自动重启。",
            True,
        )
