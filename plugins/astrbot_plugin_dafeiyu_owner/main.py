"""大肥鱼认主：让部署者用大号私聊绑定主人。

流程：
1. 机器人账号使用 QQ 小号登录。
2. 部署者用大号私聊机器人，发送 ``/认主 大号QQ``。
3. 插件校验发送者 QQ 与填写值一致后，写入各插件配置和 AstrBot 管理员列表。
4. 默认自动重启 AstrBot，让配置生效。

所有写入都只发生在本地 ``data/`` 目录，不修改插件源码。
"""

from __future__ import annotations

import json
import re
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


class DafeiyuOwnerPlugin(Star):
    """通过 /认主 绑定主人 QQ。"""

    def __init__(self, context: Context, config: dict | None = None):
        super().__init__(context, config)
        self.cfg = config or {}
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

    # ------------------------------------------------------------------ apply
    def _apply_owner(self, qq: str, umo: str) -> list[str]:
        """Write the owner into plugin configs and AstrBot admins.

        Args:
            qq: Owner QQ number.
            umo: Owner private-chat UMO.

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
        # 默认占位值 astrbot 不是真实 QQ，认主后移除。
        admins = [str(item) for item in admins if str(item) != "astrbot"]
        if qq not in admins:
            admins.append(qq)
        cmd_config["admins_id"] = admins
        self._write_json(cmd_path, cmd_config)
        written.append(cmd_path.name)

        self._write_json(
            self.owner_file,
            {
                "qq": qq,
                "umo": umo,
                "claimed_at": datetime.now().replace(microsecond=0).isoformat(),
            },
        )
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

                logger.info("[owner] 认主完成，正在重启 AstrBot")
                restart_process(0)
            except Exception:
                logger.exception("[owner] 自动重启失败，请手动重启 AstrBot")

        threading.Thread(target=run, daemon=True).start()

    # ----------------------------------------------------------------- command
    @filter.command("认主")
    async def claim_owner(self, event: AstrMessageEvent):
        """用大号私聊机器人绑定主人：/认主 你的QQ。"""
        if not bool(self.cfg.get("enabled", True)):
            return

        if event.get_group_id() or not event.is_private_chat():
            yield event.plain_result("认主请私聊我发，不要在群里发。")
            return

        raw = str(getattr(event, "message_str", "") or "").strip()
        match = COMMAND_PATTERN.match(raw)
        if not match or not match.group(1):
            yield event.plain_result(
                "用法：用你的大号私聊我，发送 /认主 你的QQ，例如 /认主 123456789。"
            )
            return

        target = match.group(1).strip().lstrip("@")
        if not QQ_PATTERN.match(target):
            yield event.plain_result("QQ 号应为 5-12 位数字，请检查后重发。")
            return

        sender = str(event.get_sender_id() or "")
        if sender != target:
            yield event.plain_result(
                "你填写的 QQ 和当前发消息的账号不一致。\n"
                "请用要绑定的大号本人私聊我，发送 /认主 你的QQ。"
            )
            return

        owner = self._read_json(self.owner_file)
        current = str(owner.get("qq") or "")
        allow_rebind = bool(self.cfg.get("allow_rebind", False))
        if current and current != target and not allow_rebind:
            yield event.plain_result(
                f"已经绑定过主人（QQ {current}）。\n"
                "如需更换，请先删除 data/owner.json 并重启 AstrBot，再用新号认主。"
            )
            return

        umo = str(getattr(event, "unified_msg_origin", "") or "")
        try:
            written = self._apply_owner(target, umo)
        except Exception:
            logger.exception("[owner] 写入主人配置失败")
            yield event.plain_result("写入主人配置失败，请查看 AstrBot 日志。")
            return

        lines = [
            f"认主成功：QQ {target}",
            "已写入：" + "、".join(written),
        ]
        if current == target:
            lines.append("这个号之前已经绑定过，本次重新同步了配置。")
        if bool(self.cfg.get("auto_restart", True)):
            lines.append("AstrBot 将在几秒后自动重启，重启后生效。")
            self._schedule_restart()
        else:
            lines.append("请手动重启 AstrBot 后生效。")
        logger.info(f"[owner] 绑定主人 QQ {target} umo={umo}")
        yield event.plain_result("\n".join(lines))
