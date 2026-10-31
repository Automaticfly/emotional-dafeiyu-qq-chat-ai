"""大肥鱼记忆 2.0：日记 2.0、主人画像与短期日程表。

所有数据只写入本插件自己的 SQLite（data/plugin_data/astrbot_plugin_dafeiyu_memory2/memory2.db），
不修改 AstrBot 核心、人格或其他插件。对外暴露 LLM 工具、三个查看命令和凌晨结算。
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timedelta
from pathlib import Path

import aiosqlite

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import LLMResponse, ProviderRequest
from astrbot.api.star import Context, Star, StarTools

PLUGIN_NAME = "astrbot_plugin_dafeiyu_memory2"

LEVEL_NAMES = {
    "normal": "普通",
    "important": "重要",
    "eternal": "刻骨铭心",
    "shelved": "搁着",
}

LEVEL_ORDER = ["normal", "important", "eternal"]
VALID_LEVELS = set(LEVEL_ORDER)

LEVEL_DEFAULTS = {
    "normal": {"init": 7, "extend": 3, "warn": 1},
    "important": {"init": 21, "extend": 7, "warn": 3},
    "eternal": {"init": 90, "extend": 30, "warn": 7},
}

DAY_SECONDS = 86400

SCHEMA = """
CREATE TABLE IF NOT EXISTS diary (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    umo TEXT NOT NULL,
    level TEXT NOT NULL DEFAULT 'normal',
    title TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL,
    mood TEXT,
    unsaid TEXT,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    base_days REAL NOT NULL DEFAULT 7,
    extended_count INTEGER NOT NULL DEFAULT 0,
    last_extended_at TEXT,
    warned_at TEXT,
    deleted_at TEXT,
    original_content TEXT,
    fuzzy INTEGER NOT NULL DEFAULT 0,
    shelf_reason TEXT,
    shelf_frozen INTEGER NOT NULL DEFAULT 0,
    last_viewed_at TEXT,
    shelf_at TEXT,
    shelf_level TEXT
);
CREATE TABLE IF NOT EXISTS portrait (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    umo TEXT NOT NULL,
    content TEXT NOT NULL,
    strength INTEGER NOT NULL DEFAULT 60,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_mentioned_at TEXT,
    source TEXT NOT NULL DEFAULT 'chat'
);
CREATE TABLE IF NOT EXISTS schedule (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    umo TEXT NOT NULL,
    content TEXT NOT NULL,
    fuzzy_content TEXT,
    original_content TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    reinforced_count INTEGER NOT NULL DEFAULT 0,
    last_settled_at TEXT,
    promoted INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    umo TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS plugin_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_diary_umo_deleted ON diary(umo, deleted_at, expires_at);
CREATE INDEX IF NOT EXISTS idx_portrait_umo_status ON portrait(umo, status);
CREATE INDEX IF NOT EXISTS idx_schedule_umo_status ON schedule(umo, status);
CREATE INDEX IF NOT EXISTS idx_messages_umo_time ON messages(umo, created_at);
"""


def _parse_id(value: object) -> int | None:
    """Parse a tool-facing id such as ``d12``, ``p3`` or ``#4``.

    Args:
        value: Raw id value from a command or LLM tool call.

    Returns:
        Positive integer id, or None when no valid id is present.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    match = re.search(r"\d+", str(value or ""))
    if not match:
        return None
    number = int(match.group(0))
    return number if number > 0 else None


def _parse_dt(value: object) -> datetime | None:
    """Parse an ISO datetime defensively and return a naive local datetime.

    Args:
        value: ISO string, datetime, or empty value.

    Returns:
        Parsed naive datetime, or None when parsing fails.
    """
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone().replace(tzinfo=None)
    return parsed


def _short_title(text: object, limit: int = 16) -> str:
    """Take the first short clause of a text for a fuzzy diary view.

    Args:
        text: Original text.
        limit: Maximum visible characters.

    Returns:
        A short title, possibly ending with an ellipsis.
    """
    cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
    if not cleaned:
        return ""
    first = re.split(r"[。！？!?；;，,\n]", cleaned, maxsplit=1)[0].strip()
    if not first:
        first = cleaned
    if len(first) > limit:
        return first[:limit] + "…"
    return first


def _as_list(value: object) -> list:
    """Return a list only when the value really is a list.

    Args:
        value: Arbitrary JSON value.

    Returns:
        The original list, or an empty list.
    """
    return value if isinstance(value, list) else []


def _normalize_portrait(text: str) -> str:
    """Normalize portrait text for duplicate detection.

    Args:
        text: Portrait content.

    Returns:
        Text without whitespace or common punctuation.
    """
    return re.sub(r"[\s\u3000，。！？、,.!?；;：:（）()\[\]【】\"'“”‘’]+", "", text or "")


def _normalize_level(value: object) -> str | None:
    """Normalize an English or Chinese diary level name.

    Args:
        value: Raw level from a tool or settlement JSON.

    Returns:
        normal, important, eternal, or None when unrecognized.
    """
    raw = str(value or "").strip().lower()
    aliases = {
        "normal": "normal",
        "普通": "normal",
        "important": "important",
        "重要": "important",
        "eternal": "eternal",
        "刻骨铭心": "eternal",
        "永恒": "eternal",
    }
    return aliases.get(raw)


class DafeiyuMemory2Plugin(Star):
    """日记 2.0、画像和日程表插件。"""

    def __init__(self, context: Context, config: dict | None = None):
        super().__init__(context, config)
        self.cfg = config or {}
        try:
            data_dir = Path(StarTools.get_data_dir(PLUGIN_NAME))
        except Exception:
            logger.exception("[memory2] 无法解析插件数据目录，使用默认路径")
            data_dir = (
                Path(__file__).resolve().parents[3]
                / "data"
                / "plugin_data"
                / PLUGIN_NAME
            )
        self.data_dir = data_dir
        self.db_path = data_dir / "memory2.db"
        self._db_ready = False
        self._db_lock = asyncio.Lock()
        self._settle_task: asyncio.Task | None = None
        self._last_message_cleanup: datetime | None = None

    # ------------------------------------------------------------------ setup
    async def _ensure_db(self) -> None:
        """Create the database and apply small safe migrations."""
        if self._db_ready:
            return
        async with self._db_lock:
            if self._db_ready:
                return
            self.data_dir.mkdir(parents=True, exist_ok=True)
            async with aiosqlite.connect(self.db_path) as db:
                await db.executescript(SCHEMA)
                await self._migrate(db)
                await db.commit()
            self._db_ready = True

    async def _migrate(self, db: aiosqlite.Connection) -> None:
        """Add internal shelf columns to an older diary table if needed.

        Args:
            db: Open database connection.
        """
        cursor = await db.execute("PRAGMA table_info(diary)")
        columns = {row[1] for row in await cursor.fetchall()}
        additions = {
            "shelf_at": "TEXT",
            "shelf_level": "TEXT",
        }
        for column, declaration in additions.items():
            if column not in columns:
                await db.execute(
                    f"ALTER TABLE diary ADD COLUMN {column} {declaration}"
                )

    def _ensure_settle_task(self) -> None:
        """Start the background settlement loop once."""
        if not self.cfg.get("enabled", True) or not self.cfg.get(
            "settle_enabled", True
        ):
            return
        if self._settle_task is None or self._settle_task.done():
            try:
                self._settle_task = asyncio.create_task(self._settle_loop())
            except RuntimeError:
                pass

    @filter.on_astrbot_loaded()
    async def _on_loaded(self):
        """Initialize storage and the settlement loop when AstrBot is ready."""
        try:
            await self._ensure_db()
            await self._cleanup_messages()
            self._ensure_settle_task()
        except Exception:
            logger.exception("[memory2] 插件初始化失败")

    async def terminate(self):
        """Cancel the background settlement loop on unload."""
        if self._settle_task and not self._settle_task.done():
            self._settle_task.cancel()
            try:
                await self._settle_task
            except asyncio.CancelledError:
                pass

    # --------------------------------------------------------------- helpers
    def _cfg_int(self, key: str, default: int, low: int, high: int) -> int:
        """Read a bounded integer configuration value.

        Args:
            key: Configuration key.
            default: Fallback value.
            low: Minimum allowed value.
            high: Maximum allowed value.

        Returns:
            Bounded integer.
        """
        try:
            value = int(self.cfg.get(key, default))
        except (TypeError, ValueError):
            value = default
        return max(low, min(high, value))

    def _cfg_str(self, key: str, default: str = "") -> str:
        """Read a string configuration value.

        Args:
            key: Configuration key.
            default: Fallback value.

        Returns:
            Trimmed string.
        """
        value = self.cfg.get(key, default)
        return str(value or default).strip()

    def _now(self) -> datetime:
        """Return the current local time without microseconds.

        Returns:
            Naive local datetime.
        """
        return datetime.now().replace(microsecond=0)

    def _level_params(self, level: str) -> tuple[str, int, int, int]:
        """Return display name, initial days, extend days and warn days.

        Args:
            level: One of normal, important, eternal.

        Returns:
            A tuple of display name, initial lifetime, extension and warning days.
        """
        safe_level = level if level in VALID_LEVELS else "normal"
        defaults = LEVEL_DEFAULTS[safe_level]
        initial = self._cfg_int(
            f"{safe_level}_init_days", defaults["init"], 1, 3650
        )
        extend = self._cfg_int(
            f"{safe_level}_extend_days", defaults["extend"], 1, 3650
        )
        warn = self._cfg_int(f"{safe_level}_warn_days", defaults["warn"], 0, 365)
        return LEVEL_NAMES[safe_level], initial, extend, warn

    def _limit(self, value: object, default: int | None = None) -> int:
        """Clamp a requested list limit.

        Args:
            value: Raw limit from a tool or command.
            default: Optional default instead of config default.

        Returns:
            Valid list limit.
        """
        default_value = (
            default
            if default is not None
            else self._cfg_int("list_default_limit", 10, 1, 200)
        )
        maximum = self._cfg_int("list_max_limit", 50, 1, 200)
        try:
            number = int(value)
        except (TypeError, ValueError):
            number = default_value
        return max(1, min(maximum, number))

    def _shelf_level(self, row: aiosqlite.Row) -> str:
        """Return the regular level behind a shelved diary.

        Args:
            row: Diary row.

        Returns:
            Stored shelf level or normal.
        """
        stored = str(row["shelf_level"] or "")
        return stored if stored in VALID_LEVELS else "normal"

    def _diary_times(
        self, row: aiosqlite.Row, now: datetime | None = None
    ) -> tuple[datetime, datetime, datetime, float, float]:
        """Calculate diary timestamps and remaining ratio defensively.

        Args:
            row: Diary row.
            now: Optional current time.

        Returns:
            Tuple of created, expires, effective now, ratio and remaining days.
        """
        current = now or self._now()
        created = _parse_dt(row["created_at"]) or current
        try:
            base_days = float(row["base_days"] or 7)
        except (TypeError, ValueError):
            base_days = 7.0
        expires = _parse_dt(row["expires_at"])
        if expires is None:
            expires = created + timedelta(days=max(1.0, base_days))
        total_seconds = (expires - created).total_seconds()
        if total_seconds <= 0:
            total_seconds = max(1.0, base_days) * DAY_SECONDS
        effective_now = current
        if row["level"] == "shelved" and int(row["shelf_frozen"] or 0):
            shelf_at = _parse_dt(row["shelf_at"])
            if shelf_at is not None:
                effective_now = shelf_at
        remaining_seconds = (expires - effective_now).total_seconds()
        ratio = remaining_seconds / total_seconds
        ratio = max(0.0, min(1.0, ratio))
        return created, expires, effective_now, ratio, remaining_seconds / DAY_SECONDS

    def _level_hint(
        self, level: str, lifespan_days: float, remaining_days: float
    ) -> str:
        """Build a level-change hint for a diary.

        Args:
            level: Regular diary level.
            lifespan_days: Total configured lifespan from writing to expiry.
            remaining_days: Remaining days.

        Returns:
            Hint text, or an empty string.
        """
        hints: list[str] = []
        if level == "normal":
            if lifespan_days > self._cfg_int(
                "normal_upgrade_after_days", 13, 1, 3650
            ):
                hints.append("可升档")
        elif level == "important":
            if lifespan_days > self._cfg_int(
                "important_upgrade_after_days", 60, 1, 3650
            ):
                hints.append("可升到刻骨铭心")
            if remaining_days < self._cfg_int(
                "important_downgrade_remaining_days", 10, 0, 3650
            ):
                hints.append("可降档")
        elif level == "eternal":
            if remaining_days < self._cfg_int(
                "eternal_downgrade_remaining_days", 30, 0, 3650
            ):
                hints.append("可降档")
        return "，".join(hints)

    def _diary_hint(self, row: aiosqlite.Row, remaining_days: float) -> str:
        """Build a hint for a diary row.

        Args:
            row: Diary row.
            remaining_days: Remaining days.

        Returns:
            Hint text, or an empty string.
        """
        level = self._shelf_level(row) if row["level"] == "shelved" else row["level"]
        created = _parse_dt(row["created_at"])
        expires = _parse_dt(row["expires_at"])
        if created is None or expires is None:
            return ""
        lifespan = max(0.0, (expires - created).total_seconds() / DAY_SECONDS)
        return self._level_hint(level, lifespan, remaining_days)

    def _diary_line(self, row: aiosqlite.Row, now: datetime) -> str:
        """Render a fuzzy diary line without revealing the full content.

        Args:
            row: Diary row.
            now: Current time.

        Returns:
            A one-line fuzzy view.
        """
        label = LEVEL_NAMES.get(str(row["level"]), "普通")
        created, _expires, _effective, ratio, remaining = self._diary_times(row, now)
        mood = str(row["mood"] or "").strip()
        fuzzy = bool(int(row["fuzzy"] or 0))
        title = _short_title(
            str(row["title"] or "").strip() or row["content"], 16
        )
        parts = [f"【{label}】{created:%m月%d日}"]
        if ratio > 0.5 and not fuzzy:
            if mood:
                parts.append(f" · {mood}")
            if title:
                parts.append(f"　{title}")
        elif ratio >= 0.2:
            if mood:
                parts.append(f" · {mood}")
            elif not fuzzy and title:
                parts.append(f"　{title}")
        else:
            if mood:
                parts.append(f" · {mood}")
        if fuzzy:
            parts.append("（已模糊）")
        if row["level"] == "shelved":
            reason = str(row["shelf_reason"] or "")
            if reason == "afraid":
                parts.append("（怕，冻着）")
            elif reason == "uninterested":
                parts.append("（不想再看）")
            else:
                parts.append("（搁着）")
        if remaining <= 0:
            parts.append("（已到期）")
        parts.append(f"　id=d{row['id']}")
        hint = self._diary_hint(row, remaining)
        if hint:
            parts.append(f"（{hint}）")
        return "".join(parts)

    def _diary_full_text(self, row: aiosqlite.Row, now: datetime) -> str:
        """Render a full diary for explicit expansion.

        Args:
            row: Diary row.
            now: Current time.

        Returns:
            Multi-line full diary view.
        """
        label = LEVEL_NAMES.get(str(row["level"]), "普通")
        created, expires, _effective, _ratio, remaining = self._diary_times(row, now)
        fuzzy = bool(int(row["fuzzy"] or 0))
        lines = [
            f"【日记 d{row['id']}】{label} · {created:%Y-%m-%d %H:%M}",
            f"标题：{str(row['title'] or '').strip() or '（无标题）'}",
            f"正文：{str(row['content'] or '').strip() or '（空白）'}",
        ]
        if row["mood"]:
            lines.append(f"心情：{row['mood']}")
        if row["unsaid"] and not fuzzy:
            lines.append(f"没敢说：{row['unsaid']}")
        frozen = row["level"] == "shelved" and int(row["shelf_frozen"] or 0)
        remaining_label = (
            f"剩 {remaining:.1f} 天"
            if remaining > 0
            else f"已过期 {abs(remaining):.1f} 天"
        )
        if frozen:
            lines.append(
                f"冻结中：原到期 {expires:%Y-%m-%d %H:%M}，"
                f"冻结时{remaining_label}。"
            )
        else:
            lines.append(
                f"到期：{expires:%Y-%m-%d %H:%M}（{remaining_label}）"
            )
        if fuzzy:
            lines.append("这篇已经模糊过了，原文还留着底，但不再翻出来。")
        if row["level"] == "shelved":
            reason = str(row["shelf_reason"] or "")
            reason_text = "怕，所以寿命冻着" if reason == "afraid" else "不想再看，寿命照常走"
            lines.append(f"现在搁着：{reason_text}。")
        elif remaining <= 0:
            lines.append("已经到期了，但还没被删掉，等她决定。")
        else:
            hint = self._diary_hint(row, remaining)
            if hint:
                lines.append(f"提醒：{hint}。")
        lines.append("（只是翻开看看，不会续命。）")
        return "\n".join(lines)

    async def _get_diary(
        self, db: aiosqlite.Connection, umo: str, diary_id: object
    ) -> aiosqlite.Row | None:
        """Fetch a non-deleted diary belonging to one session.

        Args:
            db: Open database connection.
            umo: Unified message origin.
            diary_id: Raw diary id.

        Returns:
            Row when found, otherwise None.
        """
        number = _parse_id(diary_id)
        if number is None:
            return None
        cursor = await db.execute(
            "SELECT * FROM diary WHERE id = ? AND umo = ? AND deleted_at IS NULL",
            (number, umo),
        )
        return await cursor.fetchone()

    async def _get_portrait(
        self, db: aiosqlite.Connection, umo: str, portrait_id: object
    ) -> aiosqlite.Row | None:
        """Fetch a non-deleted portrait belonging to one session.

        Args:
            db: Open database connection.
            umo: Unified message origin.
            portrait_id: Raw portrait id.

        Returns:
            Row when found, otherwise None.
        """
        number = _parse_id(portrait_id)
        if number is None:
            return None
        cursor = await db.execute(
            "SELECT * FROM portrait WHERE id = ? AND umo = ? AND status != 'deleted'",
            (number, umo),
        )
        return await cursor.fetchone()

    async def _get_schedule(
        self, db: aiosqlite.Connection, umo: str, schedule_id: object
    ) -> aiosqlite.Row | None:
        """Fetch a non-deleted schedule belonging to one session.

        Args:
            db: Open database connection.
            umo: Unified message origin.
            schedule_id: Raw schedule id.

        Returns:
            Row when found, otherwise None.
        """
        number = _parse_id(schedule_id)
        if number is None:
            return None
        cursor = await db.execute(
            "SELECT * FROM schedule WHERE id = ? AND umo = ? AND status != 'deleted'",
            (number, umo),
        )
        return await cursor.fetchone()

    # ------------------------------------------------------------- chat hooks
    @filter.on_llm_request()
    async def inject_memory2(self, event: AstrMessageEvent, req: ProviderRequest):
        """Record the user message and inject a very short memory index.

        Args:
            event: Current message event.
            req: Provider request to modify.
        """
        if not self.cfg.get("enabled", True):
            return
        try:
            await self._ensure_db()
            self._ensure_settle_task()
            umo = str(getattr(event, "unified_msg_origin", "") or "")
            if not umo:
                return
            if not event.get_extra("_memory2_user_stored"):
                text = str(getattr(event, "message_str", "") or "").strip()
                if text and not text.startswith("/"):
                    await self._add_message(umo, "user", text)
                event.set_extra("_memory2_user_stored", True)
            if event.get_extra("_memory2_injected"):
                return
            if self.cfg.get("injection_enabled", True):
                context_text = await self._build_injection(umo)
                if context_text:
                    req.system_prompt = (req.system_prompt or "") + "\n\n" + context_text
            event.set_extra("_memory2_injected", True)
        except Exception:
            logger.exception("[memory2] 注入记忆索引失败")

    @filter.on_llm_response()
    async def store_bot_message(self, event: AstrMessageEvent, resp: LLMResponse):
        """Record the assistant reply for the next settlement.

        Args:
            event: Current message event.
            resp: LLM response.
        """
        if not self.cfg.get("enabled", True):
            return
        try:
            if event.get_extra("_memory2_bot_stored"):
                return
            text = str(getattr(resp, "completion_text", "") or "").strip()
            umo = str(getattr(event, "unified_msg_origin", "") or "")
            if text and umo:
                await self._add_message(umo, "assistant", text)
            event.set_extra("_memory2_bot_stored", True)
        except Exception:
            logger.exception("[memory2] 记录机器人回复失败")

    async def _add_message(self, umo: str, role: str, content: str) -> None:
        """Store one chat message, keeping only the newest seven days.

        Args:
            umo: Unified message origin.
            role: user or assistant.
            content: Message text.
        """
        try:
            text = str(content or "").strip()
            if not text:
                return
            if len(text) > 4000:
                text = text[-4000:]
            now = self._now().isoformat(timespec="seconds")
            await self._ensure_db()
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(
                    "INSERT INTO messages (umo, role, content, created_at) "
                    "VALUES (?, ?, ?, ?)",
                    (umo, role, text, now),
                )
                await db.commit()
            if (
                self._last_message_cleanup is None
                or (self._now() - self._last_message_cleanup).total_seconds() > 3600
            ):
                await self._cleanup_messages()
        except Exception:
            logger.exception("[memory2] 写入聊天记录失败")

    async def _cleanup_messages(self) -> None:
        """Delete messages older than seven days."""
        try:
            await self._ensure_db()
            cutoff = (self._now() - timedelta(days=7)).isoformat(timespec="seconds")
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(
                    "DELETE FROM messages WHERE created_at < ?", (cutoff,)
                )
                await db.commit()
            self._last_message_cleanup = self._now()
        except Exception:
            logger.exception("[memory2] 清理旧聊天记录失败")

    async def _build_injection(self, umo: str) -> str:
        """Build the short index injected into the system prompt.

        Args:
            umo: Unified message origin.

        Returns:
            At most a short multi-line index, or an empty string on failure.
        """
        try:
            now = self._now()
            now_iso = now.isoformat(timespec="seconds")
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                cursor = await db.execute(
                    "SELECT * FROM diary WHERE umo = ? AND deleted_at IS NULL",
                    (umo,),
                )
                diaries = await cursor.fetchall()
                cursor = await db.execute(
                    "SELECT COUNT(*) FROM portrait WHERE umo = ? AND status = 'active'",
                    (umo,),
                )
                portrait_count = int((await cursor.fetchone())[0] or 0)
                cursor = await db.execute(
                    "SELECT COUNT(*) FROM schedule WHERE umo = ? AND status IN ('active', 'fuzzy')",
                    (umo,),
                )
                schedule_count = int((await cursor.fetchone())[0] or 0)
            near_count = 0
            for row in diaries:
                if row["level"] == "shelved" and int(row["shelf_frozen"] or 0):
                    continue
                _created, _expires, _effective, _ratio, remaining = self._diary_times(
                    row, now
                )
                level = (
                    self._shelf_level(row)
                    if row["level"] == "shelved"
                    else str(row["level"] or "normal")
                )
                _name, _init, _extend, warn = self._level_params(level)
                if remaining <= warn:
                    near_count += 1
            text = (
                "【记】\n"
                f"日记 {len(diaries)} 条（{near_count} 条快到期）；"
                f"画像 {portrait_count} 条；日程 {schedule_count} 条。"
            )
            if len(text) > 120:
                text = text[:117] + "..."
            return text
        except Exception:
            logger.exception("[memory2] 构建注入索引失败")
            return ""

    # ---------------------------------------------------------- diary tools
    @filter.llm_tool(name="write_diary")
    async def write_diary(
        self,
        event: AstrMessageEvent,
        level: str = "normal",
        title: str = "",
        content: str = "",
        mood: str = "",
        unsaid: str = "",
    ) -> str:
        """写一篇日记。日记会随时间变模糊、到期，只有明确延长才会留得更久。

        Args:
            level(string): 档位，只能是 normal（普通）、important（重要）或 eternal（刻骨铭心）。
            title(string): 短标题，最多 40 字；空着会自动从正文取第一小句。
            content(string): 日记正文，最多 2000 字。
            mood(string): 当时的心情，一个短词，可留空。
            unsaid(string): 没敢说出口的话，可留空。

        Returns:
            str: 写入结果和新日记的 id。
        """
        try:
            umo = str(getattr(event, "unified_msg_origin", "") or "")
            if not umo:
                return "没有会话信息，这次没写成。"
            safe_level = _normalize_level(level)
            if safe_level is None:
                return "档位只能是 normal、important 或 eternal。"
            text = str(content or "").strip()
            if not text:
                return "正文是空的，这次没写成。"
            max_content = self._cfg_int("diary_content_max_chars", 2000, 100, 20000)
            text = text[:max_content]
            max_title = self._cfg_int("diary_title_max_chars", 40, 1, 200)
            clean_title = str(title or "").strip()[:max_title]
            if not clean_title:
                clean_title = _short_title(text, max_title)
            clean_mood = str(mood or "").strip()[:20]
            clean_unsaid = str(unsaid or "").strip()[:500]
            name, initial, _extend, _warn = self._level_params(safe_level)
            now = self._now()
            expires = now + timedelta(days=initial)
            await self._ensure_db()
            async with aiosqlite.connect(self.db_path) as db:
                cursor = await db.execute(
                    "INSERT INTO diary "
                    "(umo, level, title, content, mood, unsaid, created_at, expires_at, "
                    "base_days, extended_count, fuzzy, shelf_frozen) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, 0)",
                    (
                        umo,
                        safe_level,
                        clean_title,
                        text,
                        clean_mood,
                        clean_unsaid,
                        now.isoformat(timespec="seconds"),
                        expires.isoformat(timespec="seconds"),
                        initial,
                    ),
                )
                await db.commit()
                new_id = int(cursor.lastrowid or 0)
            return (
                f"写好了，id=d{new_id}。【{name}】{expires:%m月%d日} 到期"
                f"（{initial} 天）。"
            )
        except Exception:
            logger.exception("[memory2] 写日记失败")
            return "写日记时出了点小问题，这次没记成。"

    @filter.llm_tool(name="list_diary")
    async def list_diary(self, event: AstrMessageEvent, limit: int = 10) -> str:
        """列出日记的模糊视图。只给心情、短标题和日期，不给正文；看正文要调用 expand_diary。

        Args:
            limit(int): 最多显示多少条，默认 10。

        Returns:
            str: 模糊列表。
        """
        try:
            umo = str(getattr(event, "unified_msg_origin", "") or "")
            if not umo:
                return "没有会话信息。"
            use_limit = self._limit(limit)
            now = self._now()
            now_iso = now.isoformat(timespec="seconds")
            await self._ensure_db()
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                cursor = await db.execute(
                    "SELECT COUNT(*) FROM diary WHERE umo = ? AND deleted_at IS NULL",
                    (umo,),
                )
                total = int((await cursor.fetchone())[0] or 0)
                cursor = await db.execute(
                    "SELECT * FROM diary WHERE umo = ? AND deleted_at IS NULL "
                    "ORDER BY created_at DESC, id DESC LIMIT ?",
                    (umo, use_limit),
                )
                rows = await cursor.fetchall()
                cursor = await db.execute(
                    "SELECT * FROM diary WHERE umo = ? AND deleted_at IS NULL",
                    (umo,),
                )
                all_rows = await cursor.fetchall()
            near_count = 0
            for row in all_rows:
                if row["level"] == "shelved" and int(row["shelf_frozen"] or 0):
                    continue
                _c, _e, _eff, _ratio, remaining = self._diary_times(row, now)
                level = (
                    self._shelf_level(row)
                    if row["level"] == "shelved"
                    else str(row["level"] or "normal")
                )
                _name, _init, _extend, warn = self._level_params(level)
                if remaining <= warn:
                    near_count += 1
            if not rows:
                return "日记本还是空的。"
            lines = [f"【日记 2.0】共 {total} 条（{near_count} 条快到期）"]
            for row in rows:
                lines.append(self._diary_line(row, now))
            if total > len(rows):
                lines.append(f"……还有 {total - len(rows)} 条，想看更多就加大 limit。")
            lines.append("（列表是模糊的；想读全文请调用 expand_diary。）")
            return "\n".join(lines)
        except Exception:
            logger.exception("[memory2] 列出日记失败")
            return "翻日记本时出了点小问题。"

    @filter.llm_tool(name="expand_diary")
    async def expand_diary(
        self, event: AstrMessageEvent, diary_id: str = ""
    ) -> str:
        """展开一篇日记看全文。会记录 last_viewed_at，但不会延长寿命。

        Args:
            diary_id(string): 日记 id，例如 d3、3 或 #3。

        Returns:
            str: 日记全文和剩余寿命。
        """
        try:
            umo = str(getattr(event, "unified_msg_origin", "") or "")
            if not umo:
                return "没有会话信息。"
            await self._ensure_db()
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                row = await self._get_diary(db, umo, diary_id)
                if row is None:
                    return "没有找到这篇日记，可能已经被删掉了。"
                now = self._now()
                await db.execute(
                    "UPDATE diary SET last_viewed_at = ? WHERE id = ?",
                    (now.isoformat(timespec="seconds"), row["id"]),
                )
                await db.commit()
            return self._diary_full_text(row, now)
        except Exception:
            logger.exception("[memory2] 展开日记失败")
            return "展开这篇日记时出了点小问题。"

    @filter.llm_tool(name="extend_diary")
    async def extend_diary(
        self, event: AstrMessageEvent, diary_id: str = ""
    ) -> str:
        """明确延长一篇日记的寿命。按档位加天数；同一天只能延长一次。

        Args:
            diary_id(string): 日记 id，例如 d3、3 或 #3。

        Returns:
            str: 延长结果和新的到期时间。
        """
        try:
            umo = str(getattr(event, "unified_msg_origin", "") or "")
            if not umo:
                return "没有会话信息。"
            await self._ensure_db()
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                row = await self._get_diary(db, umo, diary_id)
                if row is None:
                    return "没有找到这篇日记。"
                if row["level"] == "shelved":
                    return "这篇还搁着，先拿回来再延长。"
                now = self._now()
                last_extended = _parse_dt(row["last_extended_at"])
                if last_extended is not None and last_extended.date() == now.date():
                    return "今天已经给这篇延长过一次了，明天再来。"
                level = str(row["level"] or "normal")
                _name, _initial, extend_days, _warn = self._level_params(level)
                expires = _parse_dt(row["expires_at"]) or now
                base = max(expires, now)
                new_expires = base + timedelta(days=extend_days)
                count = int(row["extended_count"] or 0) + 1
                await db.execute(
                    "UPDATE diary SET expires_at = ?, extended_count = ?, "
                    "last_extended_at = ?, warned_at = NULL WHERE id = ?",
                    (
                        new_expires.isoformat(timespec="seconds"),
                        count,
                        now.isoformat(timespec="seconds"),
                        row["id"],
                    ),
                )
                await db.commit()
                created = _parse_dt(row["created_at"]) or now
                lifespan = (new_expires - created).total_seconds() / DAY_SECONDS
                remaining = (new_expires - now).total_seconds() / DAY_SECONDS
                hint = self._level_hint(level, lifespan, remaining)
            suffix = f" 提醒：{hint}。" if hint else ""
            return (
                f"给 d{row['id']} 延长了 {extend_days} 天，到 "
                f"{new_expires:%m月%d日}。今天只能延长这一次。{suffix}"
            )
        except Exception:
            logger.exception("[memory2] 延长日记失败")
            return "延长日记时出了点小问题。"

    @filter.llm_tool(name="change_diary_level")
    async def change_diary_level(
        self, event: AstrMessageEvent, diary_id: str = "", direction: str = ""
    ) -> str:
        """升档或降档。按新档位的初始寿命重新计算，不在旧寿命上叠加。

        Args:
            diary_id(string): 日记 id，例如 d3、3 或 #3。
            direction(string): up（升档）或 down（降档）。

        Returns:
            str: 变更结果和新的到期时间。
        """
        try:
            umo = str(getattr(event, "unified_msg_origin", "") or "")
            if not umo:
                return "没有会话信息。"
            way = str(direction or "").strip().lower()
            if way in ("up", "升", "升档", "升级"):
                way = "up"
            elif way in ("down", "降", "降档", "降级"):
                way = "down"
            else:
                return "direction 只能是 up 或 down。"
            await self._ensure_db()
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                row = await self._get_diary(db, umo, diary_id)
                if row is None:
                    return "没有找到这篇日记。"
                current = str(row["level"] or "normal")
                if current == "shelved":
                    return "这篇还搁着，先拿回来再改档位。"
                if current not in VALID_LEVELS:
                    current = "normal"
                index = LEVEL_ORDER.index(current)
                next_index = index + 1 if way == "up" else index - 1
                if next_index < 0:
                    return "已经是普通档了，不能再降。"
                if next_index >= len(LEVEL_ORDER):
                    return "已经是刻骨铭心了，不能再升。"
                new_level = LEVEL_ORDER[next_index]
                name, initial, _extend, _warn = self._level_params(new_level)
                now = self._now()
                created = _parse_dt(row["created_at"]) or now
                # The lifespan is absolute from the day it was written.
                new_expires = created + timedelta(days=initial)
                await db.execute(
                    "UPDATE diary SET level = ?, base_days = ?, expires_at = ?, "
                    "warned_at = NULL WHERE id = ?",
                    (
                        new_level,
                        initial,
                        new_expires.isoformat(timespec="seconds"),
                        row["id"],
                    ),
                )
                await db.commit()
            note = "（按写下日期算，已经到期）" if new_expires <= now else ""
            return (
                f"d{row['id']} 改成【{name}】了，按新档位重新算 {initial} 天，"
                f"到 {new_expires:%m月%d日}。{note}"
            )
        except Exception:
            logger.exception("[memory2] 修改日记档位失败")
            return "改档位时出了点小问题。"

    @filter.llm_tool(name="shelve_diary")
    async def shelve_diary(
        self, event: AstrMessageEvent, diary_id: str = "", reason: str = ""
    ) -> str:
        """把一篇日记搁起来。afraid 冻结寿命；uninterested 照常衰减。

        Args:
            diary_id(string): 日记 id，例如 d3、3 或 #3。
            reason(string): afraid（怕）或 uninterested（不想再看）。

        Returns:
            str: 搁置结果。
        """
        try:
            umo = str(getattr(event, "unified_msg_origin", "") or "")
            if not umo:
                return "没有会话信息。"
            raw_reason = str(reason or "").strip().lower()
            if raw_reason in ("afraid", "怕", "害怕", "恐惧"):
                reason_code = "afraid"
            elif raw_reason in (
                "uninterested",
                "不想",
                "没兴趣",
                "不想再看",
                "无兴趣",
            ):
                reason_code = "uninterested"
            else:
                return "reason 只能是 afraid（怕）或 uninterested（不想再看）。"
            await self._ensure_db()
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                row = await self._get_diary(db, umo, diary_id)
                if row is None:
                    return "没有找到这篇日记。"
                if row["level"] == "shelved":
                    return "这篇已经搁着了。"
                now = self._now()
                frozen = 1 if reason_code == "afraid" else 0
                await db.execute(
                    "UPDATE diary SET level = 'shelved', shelf_reason = ?, "
                    "shelf_frozen = ?, shelf_at = ?, shelf_level = ? WHERE id = ?",
                    (
                        reason_code,
                        frozen,
                        now.isoformat(timespec="seconds"),
                        row["level"],
                        row["id"],
                    ),
                )
                await db.commit()
            if reason_code == "afraid":
                return f"把 d{row['id']} 搁起来了：怕，所以寿命冻住。"
            return f"把 d{row['id']} 搁起来了：不想再看，寿命照常走。"
        except Exception:
            logger.exception("[memory2] 搁置日记失败")
            return "搁置日记时出了点小问题。"

    @filter.llm_tool(name="unshelve_diary")
    async def unshelve_diary(
        self, event: AstrMessageEvent, diary_id: str = ""
    ) -> str:
        """把搁着的日记拿回常规档位。怕而冻结的寿命会把冻住的那段补回来。

        Args:
            diary_id(string): 日记 id，例如 d3、3 或 #3。

        Returns:
            str: 拿回结果和剩余寿命。
        """
        try:
            umo = str(getattr(event, "unified_msg_origin", "") or "")
            if not umo:
                return "没有会话信息。"
            await self._ensure_db()
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                row = await self._get_diary(db, umo, diary_id)
                if row is None:
                    return "没有找到这篇日记。"
                if row["level"] != "shelved":
                    return "这篇没有搁着。"
                now = self._now()
                level = self._shelf_level(row)
                name, initial, _extend, _warn = self._level_params(level)
                expires = _parse_dt(row["expires_at"])
                if expires is None:
                    expires = now + timedelta(days=initial)
                if int(row["shelf_frozen"] or 0):
                    shelf_at = _parse_dt(row["shelf_at"])
                    if shelf_at is not None and now > shelf_at:
                        expires = expires + (now - shelf_at)
                await db.execute(
                    "UPDATE diary SET level = ?, base_days = ?, expires_at = ?, "
                    "shelf_reason = NULL, shelf_frozen = 0, shelf_at = NULL, "
                    "shelf_level = NULL, warned_at = NULL WHERE id = ?",
                    (
                        level,
                        initial,
                        expires.isoformat(timespec="seconds"),
                        row["id"],
                    ),
                )
                await db.commit()
                remaining = (expires - now).total_seconds() / DAY_SECONDS
            return (
                f"把 d{row['id']} 拿回来了，恢复【{name}】，"
                f"到 {expires:%m月%d日}（剩 {remaining:.1f} 天）。"
            )
        except Exception:
            logger.exception("[memory2] 拿回日记失败")
            return "拿回日记时出了点小问题。"

    @filter.llm_tool(name="fuzzy_diary")
    async def fuzzy_diary(
        self, event: AstrMessageEvent, diary_id: str = "", new_content: str = ""
    ) -> str:
        """把一篇日记改写成模糊的内容。原文会留在 original_content 里。

        Args:
            diary_id(string): 日记 id，例如 d3、3 或 #3。
            new_content(string): 改写后的模糊内容，最多 500 字。

        Returns:
            str: 模糊化结果。
        """
        try:
            umo = str(getattr(event, "unified_msg_origin", "") or "")
            if not umo:
                return "没有会话信息。"
            text = str(new_content or "").strip()[:500]
            if not text:
                return "新的模糊内容是空的。"
            await self._ensure_db()
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                row = await self._get_diary(db, umo, diary_id)
                if row is None:
                    return "没有找到这篇日记。"
                await db.execute(
                    "UPDATE diary SET original_content = COALESCE(original_content, content), "
                    "content = ?, fuzzy = 1 WHERE id = ?",
                    (text, row["id"]),
                )
                await db.commit()
            return f"把 d{row['id']} 改得模糊了，原文还留着底。"
        except Exception:
            logger.exception("[memory2] 模糊化日记失败")
            return "模糊化日记时出了点小问题。"

    @filter.llm_tool(name="delete_diary")
    async def delete_diary(
        self, event: AstrMessageEvent, diary_id: str = ""
    ) -> str:
        """软删除一篇日记，放进回收站。不会物理删除。

        Args:
            diary_id(string): 日记 id，例如 d3、3 或 #3。

        Returns:
            str: 删除结果。
        """
        try:
            umo = str(getattr(event, "unified_msg_origin", "") or "")
            if not umo:
                return "没有会话信息。"
            await self._ensure_db()
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                row = await self._get_diary(db, umo, diary_id)
                if row is None:
                    return "没有找到这篇日记，可能已经在回收站了。"
                now = self._now().isoformat(timespec="seconds")
                await db.execute(
                    "UPDATE diary SET deleted_at = ? WHERE id = ?", (now, row["id"])
                )
                await db.commit()
            return f"把 d{row['id']} 放进回收站了。"
        except Exception:
            logger.exception("[memory2] 删除日记失败")
            return "删除日记时出了点小问题。"

    @filter.llm_tool(name="update_diary_meta")
    async def update_diary_meta(
        self,
        event: AstrMessageEvent,
        diary_id: str = "",
        mood: str = "",
        unsaid: str = "",
    ) -> str:
        """补写日记的心情或没敢说的话。只更新传了内容的字段。

        Args:
            diary_id(string): 日记 id，例如 d3、3 或 #3。
            mood(string): 补写的心情，最多 20 字，可留空。
            unsaid(string): 补写的没敢说的话，最多 500 字，可留空。

        Returns:
            str: 更新结果。
        """
        try:
            umo = str(getattr(event, "unified_msg_origin", "") or "")
            if not umo:
                return "没有会话信息。"
            clean_mood = str(mood or "").strip()[:20]
            clean_unsaid = str(unsaid or "").strip()[:500]
            if not clean_mood and not clean_unsaid:
                return "没有要补写的内容。"
            await self._ensure_db()
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                row = await self._get_diary(db, umo, diary_id)
                if row is None:
                    return "没有找到这篇日记。"
                fields = []
                values = []
                if clean_mood:
                    fields.append("mood = ?")
                    values.append(clean_mood)
                if clean_unsaid:
                    fields.append("unsaid = ?")
                    values.append(clean_unsaid)
                values.append(row["id"])
                await db.execute(
                    f"UPDATE diary SET {', '.join(fields)} WHERE id = ?", values
                )
                await db.commit()
            return f"给 d{row['id']} 补好了。"
        except Exception:
            logger.exception("[memory2] 补写日记元信息失败")
            return "补写日记时出了点小问题。"

    @filter.llm_tool(name="diary_by_mood")
    async def diary_by_mood(
        self, event: AstrMessageEvent, mood: str = "", limit: int = 10
    ) -> str:
        """按心情翻日记，仍然只返回模糊视图。

        Args:
            mood(string): 心情关键词，例如 难过、开心、不安。
            limit(int): 最多显示多少条，默认 10。

        Returns:
            str: 符合心情的模糊日记列表。
        """
        try:
            umo = str(getattr(event, "unified_msg_origin", "") or "")
            if not umo:
                return "没有会话信息。"
            keyword = str(mood or "").strip()[:20]
            if not keyword:
                return "要按什么心情翻？"
            use_limit = self._limit(limit)
            now = self._now()
            await self._ensure_db()
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                cursor = await db.execute(
                    "SELECT * FROM diary WHERE umo = ? AND deleted_at IS NULL "
                    "AND mood LIKE ? ORDER BY created_at DESC, id DESC LIMIT ?",
                    (umo, f"%{keyword}%", use_limit),
                )
                rows = await cursor.fetchall()
            if not rows:
                return f"没有找到心情是「{keyword}」的日记。"
            lines = [f"【心情：{keyword}】共找到 {len(rows)} 条（模糊视图）"]
            for row in rows:
                lines.append(self._diary_line(row, now))
            return "\n".join(lines)
        except Exception:
            logger.exception("[memory2] 按心情翻日记失败")
            return "翻日记时出了点小问题。"

    @filter.llm_tool(name="list_diary_recycle")
    async def list_diary_recycle(
        self, event: AstrMessageEvent, limit: int = 20
    ) -> str:
        """查看日记回收站。只给管理员或私聊主人看，只显示标题和删除时间。

        Args:
            limit(int): 最多显示多少条，默认 20。

        Returns:
            str: 回收站列表。
        """
        try:
            if getattr(event, "get_group_id", lambda: "")() and not getattr(
                event, "is_admin", lambda: False
            )():
                return "回收站只给主人看。"
            umo = str(getattr(event, "unified_msg_origin", "") or "")
            if not umo:
                return "没有会话信息。"
            use_limit = self._limit(
                limit, self._cfg_int("recycle_max_list", 30, 1, 200)
            )
            await self._ensure_db()
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                cursor = await db.execute(
                    "SELECT COUNT(*) FROM diary WHERE umo = ? AND deleted_at IS NOT NULL",
                    (umo,),
                )
                total = int((await cursor.fetchone())[0] or 0)
                cursor = await db.execute(
                    "SELECT * FROM diary WHERE umo = ? AND deleted_at IS NOT NULL "
                    "ORDER BY deleted_at DESC, id DESC LIMIT ?",
                    (umo, use_limit),
                )
                rows = await cursor.fetchall()
            if not rows:
                return "回收站是空的。"
            lines = [f"【日记回收站】共 {total} 条"]
            for row in rows:
                deleted = _parse_dt(row["deleted_at"])
                deleted_text = f"{deleted:%m月%d日 %H:%M}" if deleted else "未知时间"
                label = LEVEL_NAMES.get(str(row["level"]), "普通")
                lines.append(
                    f"d{row['id']}【{label}】{str(row['title'] or '（无标题）')}"
                    f"（删除于 {deleted_text}）"
                )
            if total > len(rows):
                lines.append(f"……还有 {total - len(rows)} 条。")
            return "\n".join(lines)
        except Exception:
            logger.exception("[memory2] 查看回收站失败")
            return "打开回收站时出了点小问题。"

    # -------------------------------------------------------- portrait tools
    async def _remember_portrait_db(
        self,
        db: aiosqlite.Connection,
        umo: str,
        content: str,
        source: str,
        now: datetime,
    ) -> tuple[int, bool]:
        """Insert or merge one portrait inside an existing transaction.

        Args:
            db: Open database connection.
            umo: Unified message origin.
            content: Portrait text.
            source: chat, diary or schedule.
            now: Current time.

        Returns:
            Tuple of portrait id and whether an existing row was merged.
        """
        max_chars = self._cfg_int("portrait_max_chars", 120, 20, 500)
        text = str(content or "").strip()[:max_chars]
        normalized = _normalize_portrait(text)
        step = self._cfg_int("portrait_strength_step", 5, 1, 50)
        now_iso = now.isoformat(timespec="seconds")
        cursor = await db.execute(
            "SELECT * FROM portrait WHERE umo = ? AND status != 'deleted' "
            "ORDER BY id DESC LIMIT 200",
            (umo,),
        )
        rows = await cursor.fetchall()
        for row in rows:
            if _normalize_portrait(str(row["content"] or "")) == normalized:
                new_strength = min(100, max(0, int(row["strength"] or 0)) + step)
                await db.execute(
                    "UPDATE portrait SET strength = ?, status = 'active', "
                    "updated_at = ?, last_mentioned_at = ? WHERE id = ?",
                    (new_strength, now_iso, now_iso, row["id"]),
                )
                return int(row["id"]), True
        initial = self._cfg_int("portrait_initial_strength", 60, 0, 100)
        safe_source = source if source in ("chat", "diary", "schedule") else "chat"
        cursor = await db.execute(
            "INSERT INTO portrait "
            "(umo, content, strength, status, created_at, updated_at, "
            "last_mentioned_at, source) "
            "VALUES (?, ?, ?, 'active', ?, ?, ?, ?)",
            (umo, text, initial, now_iso, now_iso, now_iso, safe_source),
        )
        return int(cursor.lastrowid or 0), False

    @filter.llm_tool(name="remember_portrait")
    async def remember_portrait(
        self, event: AstrMessageEvent, content: str = ""
    ) -> str:
        """新增或合并一条对主人的长期认识、习惯或印象，最多 120 字。

        Args:
            content(string): 一句长期认识，最多 120 字。

        Returns:
            str: 写入或合并结果。
        """
        try:
            umo = str(getattr(event, "unified_msg_origin", "") or "")
            if not umo:
                return "没有会话信息。"
            text = str(content or "").strip()
            if not _normalize_portrait(text):
                return "画像是空的，这次没记。"
            max_chars = self._cfg_int("portrait_max_chars", 120, 20, 500)
            text = text[:max_chars]
            now = self._now()
            await self._ensure_db()
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                portrait_id, merged = await self._remember_portrait_db(
                    db, umo, text, "chat", now
                )
                await db.commit()
            if merged:
                return f"这条画像已经有了，合并加强了一下（p{portrait_id}）。"
            return f"记下一条对主人的画像：p{portrait_id}。"
        except Exception:
            logger.exception("[memory2] 写入画像失败")
            return "写画像时出了点小问题。"

    @filter.llm_tool(name="list_portrait")
    async def list_portrait(self, event: AstrMessageEvent, limit: int = 20) -> str:
        """列出还在成立的画像和强度。

        Args:
            limit(int): 最多显示多少条，默认 20。

        Returns:
            str: 画像列表。
        """
        try:
            umo = str(getattr(event, "unified_msg_origin", "") or "")
            if not umo:
                return "没有会话信息。"
            use_limit = self._limit(limit, 20)
            await self._ensure_db()
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                cursor = await db.execute(
                    "SELECT COUNT(*) FROM portrait WHERE umo = ? AND status = 'active'",
                    (umo,),
                )
                total = int((await cursor.fetchone())[0] or 0)
                cursor = await db.execute(
                    "SELECT COUNT(*) FROM portrait WHERE umo = ? AND status = 'faded'",
                    (umo,),
                )
                faded = int((await cursor.fetchone())[0] or 0)
                cursor = await db.execute(
                    "SELECT * FROM portrait WHERE umo = ? AND status = 'active' "
                    "ORDER BY strength DESC, id DESC LIMIT ?",
                    (umo, use_limit),
                )
                rows = await cursor.fetchall()
            if not rows:
                return "还没攒下对主人的画像。"
            lines = [f"【画像】{total} 条还成立" + (f"，{faded} 条已淡忘" if faded else "")]
            for row in rows:
                mentioned = _parse_dt(row["last_mentioned_at"])
                mention_text = f"{mentioned:%m月%d日}" if mentioned else "还没提过"
                lines.append(
                    f"p{row['id']} · 强度 {int(row['strength'] or 0)} · "
                    f"{str(row['content'] or '').strip()}（最近：{mention_text}）"
                )
            if total > len(rows):
                lines.append(f"……还有 {total - len(rows)} 条。")
            return "\n".join(lines)
        except Exception:
            logger.exception("[memory2] 列出画像失败")
            return "翻画像时出了点小问题。"

    @filter.llm_tool(name="strengthen_portrait")
    async def strengthen_portrait(
        self, event: AstrMessageEvent, portrait_id: str = "", delta: int = 5
    ) -> str:
        """当主人再次表现出某个习惯或印象时，加强这条画像。

        Args:
            portrait_id(string): 画像 id，例如 p3、3 或 #3。
            delta(int): 加强幅度，默认 5，最多一次加 50。

        Returns:
            str: 加强后的强度。
        """
        try:
            umo = str(getattr(event, "unified_msg_origin", "") or "")
            if not umo:
                return "没有会话信息。"
            try:
                amount = int(delta)
            except (TypeError, ValueError):
                amount = 0
            if amount <= 0:
                amount = self._cfg_int("portrait_strength_step", 5, 1, 50)
            amount = max(1, min(50, amount))
            await self._ensure_db()
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                row = await self._get_portrait(db, umo, portrait_id)
                if row is None:
                    return "没有找到这条画像。"
                now_iso = self._now().isoformat(timespec="seconds")
                new_strength = min(
                    100, max(0, int(row["strength"] or 0)) + amount
                )
                await db.execute(
                    "UPDATE portrait SET strength = ?, status = 'active', "
                    "updated_at = ?, last_mentioned_at = ? WHERE id = ?",
                    (new_strength, now_iso, now_iso, row["id"]),
                )
                await db.commit()
            return f"p{row['id']} 的强度变成 {new_strength} 了。"
        except Exception:
            logger.exception("[memory2] 加强画像失败")
            return "加强画像时出了点小问题。"

    @filter.llm_tool(name="fade_portrait")
    async def fade_portrait(
        self, event: AstrMessageEvent, portrait_id: str = ""
    ) -> str:
        """标记一条画像已经淡忘、不再成立。不会删除数据。

        Args:
            portrait_id(string): 画像 id，例如 p3、3 或 #3。

        Returns:
            str: 淡忘结果。
        """
        try:
            umo = str(getattr(event, "unified_msg_origin", "") or "")
            if not umo:
                return "没有会话信息。"
            await self._ensure_db()
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                row = await self._get_portrait(db, umo, portrait_id)
                if row is None:
                    return "没有找到这条画像。"
                if row["status"] == "faded":
                    return "这条画像已经淡忘了。"
                now_iso = self._now().isoformat(timespec="seconds")
                await db.execute(
                    "UPDATE portrait SET status = 'faded', updated_at = ? WHERE id = ?",
                    (now_iso, row["id"]),
                )
                await db.commit()
            return f"p{row['id']} 已经淡忘了。"
        except Exception:
            logger.exception("[memory2] 淡忘画像失败")
            return "淡忘画像时出了点小问题。"

    @filter.llm_tool(name="delete_portrait")
    async def delete_portrait(
        self, event: AstrMessageEvent, portrait_id: str = ""
    ) -> str:
        """软删除一条画像。不会物理删除。

        Args:
            portrait_id(string): 画像 id，例如 p3、3 或 #3。

        Returns:
            str: 删除结果。
        """
        try:
            umo = str(getattr(event, "unified_msg_origin", "") or "")
            if not umo:
                return "没有会话信息。"
            await self._ensure_db()
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                row = await self._get_portrait(db, umo, portrait_id)
                if row is None:
                    return "没有找到这条画像。"
                now_iso = self._now().isoformat(timespec="seconds")
                await db.execute(
                    "UPDATE portrait SET status = 'deleted', updated_at = ? WHERE id = ?",
                    (now_iso, row["id"]),
                )
                await db.commit()
            return f"p{row['id']} 已经删掉了。"
        except Exception:
            logger.exception("[memory2] 删除画像失败")
            return "删除画像时出了点小问题。"

    # -------------------------------------------------------- schedule tools
    @filter.llm_tool(name="add_schedule")
    async def add_schedule(
        self, event: AstrMessageEvent, content: str = ""
    ) -> str:
        """记一条短期日常小事，例如明天要做什么、最近在追什么。

        Args:
            content(string): 日程内容，最多 300 字。

        Returns:
            str: 写入结果和日程 id。
        """
        try:
            umo = str(getattr(event, "unified_msg_origin", "") or "")
            if not umo:
                return "没有会话信息。"
            text = str(content or "").strip()
            if not text:
                return "日程是空的，这次没记。"
            max_chars = self._cfg_int("schedule_content_max_chars", 300, 20, 2000)
            text = text[:max_chars]
            now_iso = self._now().isoformat(timespec="seconds")
            await self._ensure_db()
            async with aiosqlite.connect(self.db_path) as db:
                cursor = await db.execute(
                    "INSERT INTO schedule "
                    "(umo, content, status, created_at, updated_at, reinforced_count, promoted) "
                    "VALUES (?, ?, 'active', ?, ?, 0, 0)",
                    (umo, text, now_iso, now_iso),
                )
                await db.commit()
                new_id = int(cursor.lastrowid or 0)
            return f"记下了，id=s{new_id}。"
        except Exception:
            logger.exception("[memory2] 写日程失败")
            return "写日程时出了点小问题。"

    @filter.llm_tool(name="list_schedule")
    async def list_schedule(self, event: AstrMessageEvent, limit: int = 20) -> str:
        """列出还在的短期日程和它们的加固次数。

        Args:
            limit(int): 最多显示多少条，默认 20。

        Returns:
            str: 日程列表。
        """
        try:
            umo = str(getattr(event, "unified_msg_origin", "") or "")
            if not umo:
                return "没有会话信息。"
            use_limit = self._limit(limit, 20)
            await self._ensure_db()
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                cursor = await db.execute(
                    "SELECT COUNT(*) FROM schedule WHERE umo = ? "
                    "AND status IN ('active', 'fuzzy')",
                    (umo,),
                )
                total = int((await cursor.fetchone())[0] or 0)
                cursor = await db.execute(
                    "SELECT * FROM schedule WHERE umo = ? "
                    "AND status IN ('active', 'fuzzy') "
                    "ORDER BY created_at DESC, id DESC LIMIT ?",
                    (umo, use_limit),
                )
                rows = await cursor.fetchall()
            if not rows:
                return "日程表是空的。"
            lines = [f"【日程表】共 {total} 条"]
            for row in rows:
                status = str(row["status"] or "active")
                if status == "fuzzy":
                    text = str(row["fuzzy_content"] or row["content"] or "").strip()
                    prefix = "模糊"
                else:
                    text = str(row["content"] or "").strip()
                    prefix = "日程"
                promoted = " · 已进画像" if int(row["promoted"] or 0) else ""
                lines.append(
                    f"s{row['id']} · {status} · 加固 {int(row['reinforced_count'] or 0)} 次"
                    f"{promoted} · {prefix}：{text}"
                )
            if total > len(rows):
                lines.append(f"……还有 {total - len(rows)} 条。")
            return "\n".join(lines)
        except Exception:
            logger.exception("[memory2] 列出日程失败")
            return "翻日程表时出了点小问题。"

    @filter.llm_tool(name="update_schedule")
    async def update_schedule(
        self, event: AstrMessageEvent, schedule_id: str = "", content: str = ""
    ) -> str:
        """修改一条日程。修改后会恢复成清晰状态。

        Args:
            schedule_id(string): 日程 id，例如 s3、3 或 #3。
            content(string): 新的日程内容，最多 300 字。

        Returns:
            str: 修改结果。
        """
        try:
            umo = str(getattr(event, "unified_msg_origin", "") or "")
            if not umo:
                return "没有会话信息。"
            text = str(content or "").strip()
            if not text:
                return "新的日程内容是空的。"
            max_chars = self._cfg_int("schedule_content_max_chars", 300, 20, 2000)
            text = text[:max_chars]
            await self._ensure_db()
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                row = await self._get_schedule(db, umo, schedule_id)
                if row is None:
                    return "没有找到这条日程。"
                now_iso = self._now().isoformat(timespec="seconds")
                await db.execute(
                    "UPDATE schedule SET content = ?, status = 'active', "
                    "fuzzy_content = NULL, original_content = NULL, updated_at = ? "
                    "WHERE id = ?",
                    (text, now_iso, row["id"]),
                )
                await db.commit()
            return f"s{row['id']} 改好了。"
        except Exception:
            logger.exception("[memory2] 修改日程失败")
            return "修改日程时出了点小问题。"

    @filter.llm_tool(name="fuzzy_schedule")
    async def fuzzy_schedule(
        self, event: AstrMessageEvent, schedule_id: str = "", new_content: str = ""
    ) -> str:
        """把一条日程模糊化，原文会留在 original_content 里。

        Args:
            schedule_id(string): 日程 id，例如 s3、3 或 #3。
            new_content(string): 模糊后的内容，最多 300 字。

        Returns:
            str: 模糊化结果。
        """
        try:
            umo = str(getattr(event, "unified_msg_origin", "") or "")
            if not umo:
                return "没有会话信息。"
            text = str(new_content or "").strip()
            if not text:
                return "新的模糊内容是空的。"
            max_chars = self._cfg_int("schedule_content_max_chars", 300, 20, 2000)
            text = text[:max_chars]
            await self._ensure_db()
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                row = await self._get_schedule(db, umo, schedule_id)
                if row is None:
                    return "没有找到这条日程。"
                now_iso = self._now().isoformat(timespec="seconds")
                await db.execute(
                    "UPDATE schedule SET original_content = "
                    "COALESCE(original_content, content), fuzzy_content = ?, "
                    "status = 'fuzzy', updated_at = ? WHERE id = ?",
                    (text, now_iso, row["id"]),
                )
                await db.commit()
            return f"把 s{row['id']} 模糊了，原文还留着底。"
        except Exception:
            logger.exception("[memory2] 模糊化日程失败")
            return "模糊化日程时出了点小问题。"

    @filter.llm_tool(name="delete_schedule")
    async def delete_schedule(
        self, event: AstrMessageEvent, schedule_id: str = ""
    ) -> str:
        """软删除一条日程。不会物理删除。

        Args:
            schedule_id(string): 日程 id，例如 s3、3 或 #3。

        Returns:
            str: 删除结果。
        """
        try:
            umo = str(getattr(event, "unified_msg_origin", "") or "")
            if not umo:
                return "没有会话信息。"
            await self._ensure_db()
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                row = await self._get_schedule(db, umo, schedule_id)
                if row is None:
                    return "没有找到这条日程。"
                now_iso = self._now().isoformat(timespec="seconds")
                await db.execute(
                    "UPDATE schedule SET status = 'deleted', updated_at = ? WHERE id = ?",
                    (now_iso, row["id"]),
                )
                await db.commit()
            return f"s{row['id']} 删掉了。"
        except Exception:
            logger.exception("[memory2] 删除日程失败")
            return "删除日程时出了点小问题。"

    @filter.llm_tool(name="reinforce_schedule")
    async def reinforce_schedule(
        self, event: AstrMessageEvent, schedule_id: str = ""
    ) -> str:
        """加深对一条日程的印象，加固次数 +1；模糊的日程会恢复清晰。

        Args:
            schedule_id(string): 日程 id，例如 s3、3 或 #3。

        Returns:
            str: 加固结果。
        """
        try:
            umo = str(getattr(event, "unified_msg_origin", "") or "")
            if not umo:
                return "没有会话信息。"
            await self._ensure_db()
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                row = await self._get_schedule(db, umo, schedule_id)
                if row is None:
                    return "没有找到这条日程。"
                count = int(row["reinforced_count"] or 0) + 1
                now_iso = self._now().isoformat(timespec="seconds")
                if row["status"] == "fuzzy":
                    restored = str(row["original_content"] or row["content"] or "").strip()
                    await db.execute(
                        "UPDATE schedule SET reinforced_count = ?, status = 'active', "
                        "content = ?, fuzzy_content = NULL, original_content = NULL, "
                        "updated_at = ? WHERE id = ?",
                        (count, restored, now_iso, row["id"]),
                    )
                    await db.commit()
                    return f"s{row['id']} 加固了 {count} 次，也重新记清楚了。"
                await db.execute(
                    "UPDATE schedule SET reinforced_count = ?, updated_at = ? WHERE id = ?",
                    (count, now_iso, row["id"]),
                )
                await db.commit()
            return f"s{row['id']} 加固了 {count} 次。"
        except Exception:
            logger.exception("[memory2] 加固日程失败")
            return "加固日程时出了点小问题。"

    @filter.llm_tool(name="promote_schedule")
    async def promote_schedule(
        self, event: AstrMessageEvent, schedule_id: str = ""
    ) -> str:
        """把一条日程升级成画像，成为对主人的长期认识。

        Args:
            schedule_id(string): 日程 id，例如 s3、3 或 #3。

        Returns:
            str: 升级结果和画像 id。
        """
        try:
            umo = str(getattr(event, "unified_msg_origin", "") or "")
            if not umo:
                return "没有会话信息。"
            await self._ensure_db()
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                row = await self._get_schedule(db, umo, schedule_id)
                if row is None:
                    return "没有找到这条日程。"
                if int(row["promoted"] or 0):
                    return "这条日程已经升级过了。"
                text = str(row["original_content"] or row["content"] or "").strip()
                if not text:
                    return "这条日程是空的，升级不了。"
                now = self._now()
                portrait_id, merged = await self._remember_portrait_db(
                    db, umo, text, "schedule", now
                )
                await db.execute(
                    "UPDATE schedule SET promoted = 1, updated_at = ? WHERE id = ?",
                    (now.isoformat(timespec="seconds"), row["id"]),
                )
                await db.commit()
            verb = "合并到了已有画像" if merged else "升级成了画像"
            return f"s{row['id']} {verb} p{portrait_id}。"
        except Exception:
            logger.exception("[memory2] 升级日程失败")
            return "升级日程时出了点小问题。"

    # --------------------------------------------------------------- commands
    @filter.command("日记2")
    async def cmd_diary2(self, event: AstrMessageEvent):
        """查看日记 2.0：模糊列表、展开或回收站。

        Args:
            event: Command event.
        """
        try:
            if getattr(event, "get_group_id", lambda: "")() and not getattr(
                event, "is_admin", lambda: False
            )():
                yield event.plain_result("这个只给主人看。")
                return
            raw = str(getattr(event, "message_str", "") or "").strip()
            index = raw.find("日记2")
            if index >= 0:
                raw = raw[index + len("日记2") :].strip()
            if not raw:
                result = await self.list_diary(event, 20)
            elif raw.startswith("展开"):
                target = raw[len("展开") :].strip()
                if _parse_id(target) is None:
                    result = "用法：/日记2 展开 <id>，例如 /日记2 展开 d3。"
                else:
                    result = await self.expand_diary(event, target)
            elif raw.startswith("回收站"):
                result = await self.list_diary_recycle(event, 30)
            else:
                result = "用法：/日记2、/日记2 展开 <id>、/日记2 回收站。"
            yield event.plain_result(result)
        except Exception:
            logger.exception("[memory2] /日记2 命令失败")
            yield event.plain_result("翻日记时出了点小问题。")

    @filter.command("画像")
    async def cmd_portrait(self, event: AstrMessageEvent):
        """查看还在成立的画像和强度。

        Args:
            event: Command event.
        """
        try:
            if getattr(event, "get_group_id", lambda: "")() and not getattr(
                event, "is_admin", lambda: False
            )():
                yield event.plain_result("这个只给主人看。")
                return
            yield event.plain_result(await self.list_portrait(event, 30))
        except Exception:
            logger.exception("[memory2] /画像 命令失败")
            yield event.plain_result("翻画像时出了点小问题。")

    @filter.command("日程")
    async def cmd_schedule(self, event: AstrMessageEvent):
        """查看短期日程表。

        Args:
            event: Command event.
        """
        try:
            if getattr(event, "get_group_id", lambda: "")() and not getattr(
                event, "is_admin", lambda: False
            )():
                yield event.plain_result("这个只给主人看。")
                return
            yield event.plain_result(await self.list_schedule(event, 30))
        except Exception:
            logger.exception("[memory2] /日程 命令失败")
            yield event.plain_result("翻日程表时出了点小问题。")

    # ------------------------------------------------------------- settlement
    def _in_settle_window(self, now: datetime) -> bool:
        """Whether the current hour is inside the settlement window.

        Args:
            now: Current local time.

        Returns:
            True when settlement may run.
        """
        start = self._cfg_int("settle_start_hour", 4, 0, 23)
        end = self._cfg_int("settle_end_hour", 8, 0, 24)
        if start == end:
            return True
        if start < end:
            return start <= now.hour < end
        return now.hour >= start or now.hour < end

    async def _settle_loop(self) -> None:
        """Run the settlement window check every configured interval."""
        while True:
            try:
                if self.cfg.get("enabled", True) and self.cfg.get(
                    "settle_enabled", True
                ):
                    now = self._now()
                    if self._in_settle_window(now):
                        await self._settle_window(now)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("[memory2] 结算循环异常")
            interval = self._cfg_int("settle_interval_seconds", 600, 60, 3600)
            await asyncio.sleep(interval)

    async def _meta_get(self, key: str) -> str | None:
        """Read one value from the internal key-value table.

        Args:
            key: Meta key.

        Returns:
            Stored value or None.
        """
        try:
            async with aiosqlite.connect(self.db_path) as db:
                cursor = await db.execute(
                    "SELECT value FROM plugin_meta WHERE key = ?", (key,)
                )
                row = await cursor.fetchone()
                return str(row[0]) if row else None
        except Exception:
            logger.exception("[memory2] 读取结算标记失败")
            return None

    async def _meta_set(self, key: str, value: str) -> None:
        """Write one value into the internal key-value table.

        Args:
            key: Meta key.
            value: Value to store.
        """
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(
                    "INSERT INTO plugin_meta (key, value) VALUES (?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (key, value),
                )
                await db.commit()
        except Exception:
            logger.exception("[memory2] 写入结算标记失败")

    async def _settle_window(self, now: datetime) -> None:
        """Settle every recently active session once per day.

        Args:
            now: Current local time.
        """
        await self._ensure_db()
        today = now.date().isoformat()
        main_umo = self._cfg_str("main_umo")
        since = (now - timedelta(hours=24)).isoformat(timespec="seconds")
        max_umos = self._cfg_int("max_umo_per_settle", 5, 1, 50)
        if main_umo:
            umos = [main_umo]
        else:
            async with aiosqlite.connect(self.db_path) as db:
                cursor = await db.execute(
                    "SELECT umo, MAX(created_at) AS last_at FROM messages "
                    "WHERE created_at >= ? GROUP BY umo ORDER BY last_at DESC LIMIT ?",
                    (since, max_umos),
                )
                umos = [str(row[0]) for row in await cursor.fetchall()]
        for umo in umos:
            try:
                key = f"settled:{umo}:{today}"
                if await self._meta_get(key) == today:
                    continue
                if await self._settle_one(umo, today):
                    await self._meta_set(key, today)
            except Exception:
                logger.exception(f"[memory2] 结算失败: {umo}")

    async def _settle_one(self, umo: str, today: str) -> bool:
        """Run one LLM settlement for one session.

        Args:
            umo: Unified message origin.
            today: Current date string.

        Returns:
            True when the settlement completed (even if there was nothing to do).
        """
        await self._ensure_db()
        now = self._now()
        since = (now - timedelta(hours=24)).isoformat(timespec="seconds")
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT role, content FROM messages WHERE umo = ? AND created_at >= ? "
                "ORDER BY id ASC",
                (umo, since),
            )
            messages = await cursor.fetchall()
        if not messages:
            logger.info(f"[memory2] 24 小时内没有消息，跳过结算: {umo}")
            return False
        transcript = "\n".join(
            f"{'主人' if row['role'] == 'user' else '大肥鱼'}：{row['content']}"
            for row in messages
        )
        max_chars = self._cfg_int("max_transcript_chars", 12000, 1000, 50000)
        if len(transcript) > max_chars:
            transcript = "（前略）\n" + transcript[-max_chars:]
        context = await self._collect_settlement_context(umo, now)
        if not context["has_any"]:
            logger.info(f"[memory2] 没有需要结算的记忆，跳过模型调用: {umo}")
            return True
        try:
            provider = await self.context.get_using_provider_async(umo)
        except Exception:
            logger.exception(f"[memory2] 获取对话模型失败: {umo}")
            return False
        if provider is None:
            logger.warning(f"[memory2] 没有可用对话模型，结算稍后重试: {umo}")
            return False
        prompt = self._build_settlement_prompt(transcript, context, now)
        try:
            resp = await provider.text_chat(
                prompt=prompt,
                contexts=[],
                system_prompt=(
                    "你是「大肥鱼」，一只温柔、爱笑、会记事的蓝色鲸鱼娘。"
                    "现在是凌晨记忆结算，只输出一个 JSON 对象，不要解释，不要 markdown。"
                ),
            )
            raw = str(getattr(resp, "completion_text", "") or "").strip()
        except Exception:
            logger.exception(f"[memory2] 结算模型调用失败: {umo}")
            return False
        data = self._extract_json(raw)
        if data is None:
            logger.warning(
                f"[memory2] 结算 JSON 解析失败，保持原数据不动: {umo} {raw[:200]}"
            )
            await self._safe_maintenance(umo, context)
            return False
        await self._decay_portraits(umo, now)
        now_iso = now.isoformat(timespec="seconds")
        try:
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                await self._mark_diary_warnings(db, context["due_diaries"], now_iso)
                summary = await self._apply_settlement(db, umo, data, now)
                await db.commit()
        except Exception:
            logger.exception(f"[memory2] 结算决策执行失败，已回滚: {umo}")
            return False
        logger.info(f"[memory2] 结算完成: {umo} date={today} {summary}")
        return True

    async def _decay_portraits(self, umo: str, now: datetime) -> None:
        """Lower the strength of portraits that have not been mentioned for a while.

        Args:
            umo: Unified message origin.
            now: Current local time.
        """
        after_days = self._cfg_int("portrait_decay_after_days", 30, 1, 3650)
        interval_days = self._cfg_int("portrait_decay_interval_days", 7, 1, 365)
        amount = self._cfg_int("portrait_decay_amount", 2, 1, 30)
        mention_cutoff = now - timedelta(days=after_days)
        change_cutoff = now - timedelta(days=interval_days)
        now_iso = now.isoformat(timespec="seconds")
        changed = 0
        try:
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                cursor = await db.execute(
                    "SELECT * FROM portrait WHERE umo = ? AND status = 'active' "
                    "AND strength > 0",
                    (umo,),
                )
                rows = await cursor.fetchall()
                for row in rows:
                    mentioned = (
                        _parse_dt(row["last_mentioned_at"])
                        or _parse_dt(row["updated_at"])
                        or _parse_dt(row["created_at"])
                    )
                    changed_at = _parse_dt(row["updated_at"])
                    if mentioned is None or mentioned > mention_cutoff:
                        continue
                    if changed_at is not None and changed_at > change_cutoff:
                        continue
                    new_strength = max(0, int(row["strength"] or 0) - amount)
                    await db.execute(
                        "UPDATE portrait SET strength = ?, updated_at = ? WHERE id = ?",
                        (new_strength, now_iso, row["id"]),
                    )
                    changed += 1
                if changed:
                    await db.commit()
            if changed:
                logger.info(f"[memory2] 画像自然变淡 {changed} 条: {umo}")
        except Exception:
            logger.exception("[memory2] 画像自然变淡失败")

    async def _collect_settlement_context(
        self, umo: str, now: datetime
    ) -> dict:
        """Collect due diaries, schedules and portraits for the settlement prompt.

        Args:
            umo: Unified message origin.
            now: Current local time.

        Returns:
            Dictionary with row lists and rendered prompt sections.
        """
        max_items = self._cfg_int("max_items_per_settle", 30, 1, 100)
        due_diaries: list[dict] = []
        schedules: list[aiosqlite.Row] = []
        portraits: list[aiosqlite.Row] = []
        high_portraits: list[aiosqlite.Row] = []
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM diary WHERE umo = ? AND deleted_at IS NULL "
                "ORDER BY expires_at ASC LIMIT 200",
                (umo,),
            )
            diary_rows = await cursor.fetchall()
            cursor = await db.execute(
                "SELECT * FROM schedule WHERE umo = ? "
                "AND status IN ('active', 'fuzzy') ORDER BY updated_at ASC LIMIT ?",
                (umo, max_items),
            )
            schedules = list(await cursor.fetchall())
            cursor = await db.execute(
                "SELECT * FROM portrait WHERE umo = ? AND status = 'active' "
                "ORDER BY strength DESC, id DESC LIMIT ?",
                (umo, max_items),
            )
            portraits = list(await cursor.fetchall())
            cursor = await db.execute(
                "SELECT key FROM plugin_meta WHERE key LIKE ?",
                (f"portrait_diary:{umo}:%",),
            )
            promoted_keys = {str(row[0]) for row in await cursor.fetchall()}
        for row in diary_rows:
            if row["level"] == "shelved" and int(row["shelf_frozen"] or 0):
                continue
            _created, _expires, _effective, _ratio, remaining = self._diary_times(
                row, now
            )
            level = (
                self._shelf_level(row)
                if row["level"] == "shelved"
                else str(row["level"] or "normal")
            )
            _name, _init, _extend, warn = self._level_params(level)
            expired = remaining <= 0
            if not expired and remaining > warn:
                continue
            if row["warned_at"] and not expired:
                continue
            due_diaries.append(
                {
                    "row": row,
                    "remaining": remaining,
                    "expired": expired,
                    "hint": self._diary_hint(row, remaining),
                }
            )
            if len(due_diaries) >= max_items:
                break
        high_strength = self._cfg_int("portrait_high_strength", 70, 0, 100)
        for row in portraits:
            key = f"portrait_diary:{umo}:{row['id']}"
            if int(row["strength"] or 0) >= high_strength and key not in promoted_keys:
                high_portraits.append(row)
        diary_lines = []
        for entry in due_diaries:
            row = entry["row"]
            status = "已到期" if entry["expired"] else f"剩 {entry['remaining']:.1f} 天"
            content = str(row["content"] or "").strip()
            if len(content) > 200:
                content = content[:200] + "…"
            extra = []
            if row["mood"]:
                extra.append(f"心情：{row['mood']}")
            if row["fuzzy"]:
                extra.append("已模糊")
            if row["level"] == "shelved":
                extra.append("搁着·寿命照常")
            if entry["hint"]:
                extra.append(entry["hint"])
            suffix = "；".join(extra)
            diary_lines.append(
                f"d{row['id']} | {LEVEL_NAMES.get(str(row['level']), '普通')} | "
                f"{status} | {content}" + (f" | {suffix}" if suffix else "")
            )
        schedule_lines = []
        for row in schedules:
            text = str(row["content"] or "").strip()
            if len(text) > 160:
                text = text[:160] + "…"
            extra = ""
            if row["status"] == "fuzzy":
                fuzzy_text = str(row["fuzzy_content"] or "").strip()
                extra = f" | 模糊：{fuzzy_text[:120]}"
            promoted = " | 已进画像" if int(row["promoted"] or 0) else ""
            schedule_lines.append(
                f"s{row['id']} | {row['status']} | 加固 {int(row['reinforced_count'] or 0)} 次"
                f" | {text}{extra}{promoted}"
            )
        portrait_lines = [
            f"p{row['id']} | 强度 {int(row['strength'] or 0)} | {str(row['content'] or '').strip()}"
            for row in portraits
        ]
        high_lines = [
            f"p{row['id']} | 强度 {int(row['strength'] or 0)} | {str(row['content'] or '').strip()}"
            for row in high_portraits
        ]
        sections = (
            "【快到期/到期日记】\n"
            + ("\n".join(diary_lines) if diary_lines else "（无）")
            + "\n\n【日程】\n"
            + ("\n".join(schedule_lines) if schedule_lines else "（无）")
            + "\n\n【画像】\n"
            + ("\n".join(portrait_lines) if portrait_lines else "（无）")
            + "\n\n【可考虑写进日记的高强度画像】\n"
            + ("\n".join(high_lines) if high_lines else "（无）")
        )
        return {
            "due_diaries": due_diaries,
            "schedules": schedules,
            "portraits": portraits,
            "high_portraits": high_portraits,
            "sections": sections,
            "has_any": bool(due_diaries or schedules or portraits),
        }

    def _build_settlement_prompt(
        self, transcript: str, context: dict, now: datetime
    ) -> str:
        """Build the single settlement prompt.

        Args:
            transcript: Recent chat transcript.
            context: Collected settlement context.
            now: Current local time.

        Returns:
            Prompt text.
        """
        return f"""现在是 {now:%Y-%m-%d %H:%M}，做一次深夜记忆结算。只输出一个 JSON 对象，不要解释，不要 markdown 代码块。

最近 24 小时聊天：
{transcript}

待处理的记忆：
{context['sections']}

请只输出以下结构（没有动作就给空数组；不确定就 keep，绝对不要编造 id）：
{{
  "diary_actions": [{{"id": "d1", "action": "extend|up|down|shelve|fuzzy|delete|keep", "reason": "简短理由", "new_content": "模糊化时的新内容"}}],
  "schedule_actions": [{{"id": "s1", "action": "fuzzy|delete|reinforce|promote|keep", "new_content": "模糊化时的新内容"}}],
  "portrait_actions": [{{"id": "p1", "action": "strengthen|fade|keep", "delta": 5}}],
  "diary_from_portrait": [{{"portrait_id": "p1", "level": "normal|important|eternal"}}]
}}

规则：
1. 日记到期先提醒，只有真的舍不得才 extend/up/down；shelve 的 reason 可以写怕或不想再看。
2. 删除只能是软删除；fuzzy 必须给 new_content，原文会留底。
3. 日程只在确实记不清时 fuzzy，确实不再需要才 delete；重要日常可以 promote 成画像。
4. 画像只按还成不成立 fade，不要因为时间久就删；高强度画像可考虑写成日记。
5. 只能使用上面列出的 id，不要新增 id；拿不准就 keep。"""

    def _extract_json(self, text: str) -> dict | None:
        """Extract the first plausible JSON object from model output.

        Args:
            text: Raw model output.

        Returns:
            Parsed dictionary, or None.
        """
        if not text:
            return None
        cleaned = text.strip()
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start < 0 or end <= start:
            return None
        candidate = cleaned[start : end + 1]
        for attempt in (candidate, re.sub(r",\s*([}\]])", r"\1", candidate)):
            try:
                data = json.loads(attempt)
            except (json.JSONDecodeError, TypeError, ValueError):
                continue
            if isinstance(data, dict):
                return data
        return None

    async def _safe_maintenance(self, umo: str, context: dict) -> None:
        """Run only non-destructive cleanup when a settlement decision is unusable.

        Args:
            umo: Unified message origin.
            context: Collected settlement context for logging.
        """
        try:
            await self._cleanup_messages()
            logger.warning(
                "[memory2] 结算未执行任何删除/模糊，仅清理了过期聊天记录: "
                f"{umo} due_diaries={len(context.get('due_diaries', []))} "
                f"schedules={len(context.get('schedules', []))} "
                f"portraits={len(context.get('portraits', []))}"
            )
        except Exception:
            logger.exception("[memory2] 安全清理失败")

    async def _mark_diary_warnings(
        self, db: aiosqlite.Connection, due: list[dict], now_iso: str
    ) -> None:
        """Record that due diaries were shown to the settlement model.

        Args:
            db: Open database connection.
            due: Due diary entries.
            now_iso: Current timestamp.
        """
        for entry in due:
            row = entry.get("row")
            if row is None:
                continue
            await db.execute(
                "UPDATE diary SET warned_at = ? WHERE id = ? AND deleted_at IS NULL",
                (now_iso, row["id"]),
            )

    async def _apply_settlement(
        self,
        db: aiosqlite.Connection,
        umo: str,
        data: dict,
        now: datetime,
    ) -> str:
        """Apply one validated settlement decision inside a transaction.

        Args:
            db: Open database connection.
            umo: Unified message origin.
            data: Parsed settlement JSON.
            now: Current local time.

        Returns:
            Short summary for logs.
        """
        now_iso = now.isoformat(timespec="seconds")
        diary_applied = 0
        schedule_applied = 0
        portrait_applied = 0
        from_portrait = 0
        for item in _as_list(data.get("diary_actions"))[:50]:
            if not isinstance(item, dict):
                continue
            row = await self._get_diary(db, umo, item.get("id"))
            if row is None:
                continue
            action = str(item.get("action") or "").strip().lower()
            if action == "extend":
                if row["level"] == "shelved":
                    continue
                last_extended = _parse_dt(row["last_extended_at"])
                if last_extended is not None and last_extended.date() == now.date():
                    continue
                _name, _init, extend_days, _warn = self._level_params(row["level"])
                expires = _parse_dt(row["expires_at"]) or now
                new_expires = max(expires, now) + timedelta(days=extend_days)
                await db.execute(
                    "UPDATE diary SET expires_at = ?, extended_count = ?, "
                    "last_extended_at = ?, warned_at = NULL WHERE id = ?",
                    (
                        new_expires.isoformat(timespec="seconds"),
                        int(row["extended_count"] or 0) + 1,
                        now_iso,
                        row["id"],
                    ),
                )
                diary_applied += 1
            elif action in ("up", "down"):
                if row["level"] == "shelved":
                    continue
                current = row["level"] if row["level"] in VALID_LEVELS else "normal"
                index = LEVEL_ORDER.index(current)
                next_index = index + 1 if action == "up" else index - 1
                if next_index < 0 or next_index >= len(LEVEL_ORDER):
                    continue
                new_level = LEVEL_ORDER[next_index]
                _name, initial, _extend, _warn = self._level_params(new_level)
                created = _parse_dt(row["created_at"]) or now
                new_expires = created + timedelta(days=initial)
                await db.execute(
                    "UPDATE diary SET level = ?, base_days = ?, expires_at = ?, "
                    "warned_at = NULL WHERE id = ?",
                    (
                        new_level,
                        initial,
                        new_expires.isoformat(timespec="seconds"),
                        row["id"],
                    ),
                )
                diary_applied += 1
            elif action == "shelve":
                if row["level"] == "shelved":
                    continue
                reason_text = str(
                    item.get("shelf_reason") or item.get("reason") or ""
                ).lower()
                reason_code = "afraid" if ("怕" in reason_text or "afraid" in reason_text) else "uninterested"
                await db.execute(
                    "UPDATE diary SET level = 'shelved', shelf_reason = ?, "
                    "shelf_frozen = ?, shelf_at = ?, shelf_level = ? WHERE id = ?",
                    (
                        reason_code,
                        1 if reason_code == "afraid" else 0,
                        now_iso,
                        row["level"],
                        row["id"],
                    ),
                )
                diary_applied += 1
            elif action == "fuzzy":
                new_content = str(item.get("new_content") or "").strip()[:500]
                if not new_content:
                    new_content = "有些细节已经记不清了，只剩一点模糊的感觉。"
                await db.execute(
                    "UPDATE diary SET original_content = COALESCE(original_content, content), "
                    "content = ?, fuzzy = 1 WHERE id = ?",
                    (new_content, row["id"]),
                )
                diary_applied += 1
            elif action == "delete":
                await db.execute(
                    "UPDATE diary SET deleted_at = ? WHERE id = ?",
                    (now_iso, row["id"]),
                )
                diary_applied += 1
        for item in _as_list(data.get("schedule_actions"))[:50]:
            if not isinstance(item, dict):
                continue
            row = await self._get_schedule(db, umo, item.get("id"))
            if row is None:
                continue
            action = str(item.get("action") or "").strip().lower()
            if action == "fuzzy":
                new_content = str(item.get("new_content") or "").strip()[:300]
                if not new_content:
                    new_content = "有些日常已经记不清了，只记得大概有这么件事。"
                await db.execute(
                    "UPDATE schedule SET original_content = "
                    "COALESCE(original_content, content), fuzzy_content = ?, "
                    "status = 'fuzzy', updated_at = ?, last_settled_at = ? WHERE id = ?",
                    (new_content, now_iso, now_iso, row["id"]),
                )
                schedule_applied += 1
            elif action == "delete":
                await db.execute(
                    "UPDATE schedule SET status = 'deleted', updated_at = ?, "
                    "last_settled_at = ? WHERE id = ?",
                    (now_iso, now_iso, row["id"]),
                )
                schedule_applied += 1
            elif action == "reinforce":
                count = int(row["reinforced_count"] or 0) + 1
                if row["status"] == "fuzzy":
                    restored = str(
                        row["original_content"] or row["content"] or ""
                    ).strip()
                    await db.execute(
                        "UPDATE schedule SET reinforced_count = ?, status = 'active', "
                        "content = ?, fuzzy_content = NULL, original_content = NULL, "
                        "updated_at = ?, last_settled_at = ? WHERE id = ?",
                        (count, restored, now_iso, now_iso, row["id"]),
                    )
                else:
                    await db.execute(
                        "UPDATE schedule SET reinforced_count = ?, updated_at = ?, "
                        "last_settled_at = ? WHERE id = ?",
                        (count, now_iso, now_iso, row["id"]),
                    )
                schedule_applied += 1
            elif action == "promote":
                if int(row["promoted"] or 0):
                    continue
                text = str(row["original_content"] or row["content"] or "").strip()
                if not text:
                    continue
                await self._remember_portrait_db(db, umo, text, "schedule", now)
                await db.execute(
                    "UPDATE schedule SET promoted = 1, updated_at = ?, "
                    "last_settled_at = ? WHERE id = ?",
                    (now_iso, now_iso, row["id"]),
                )
                schedule_applied += 1
            elif action == "keep":
                await db.execute(
                    "UPDATE schedule SET last_settled_at = ? WHERE id = ?",
                    (now_iso, row["id"]),
                )
        for item in _as_list(data.get("portrait_actions"))[:50]:
            if not isinstance(item, dict):
                continue
            row = await self._get_portrait(db, umo, item.get("id"))
            if row is None:
                continue
            action = str(item.get("action") or "").strip().lower()
            if action == "strengthen":
                try:
                    delta = int(item.get("delta", 5))
                except (TypeError, ValueError):
                    delta = 5
                delta = max(1, min(30, delta))
                new_strength = min(100, max(0, int(row["strength"] or 0)) + delta)
                await db.execute(
                    "UPDATE portrait SET strength = ?, status = 'active', "
                    "updated_at = ?, last_mentioned_at = ? WHERE id = ?",
                    (new_strength, now_iso, now_iso, row["id"]),
                )
                portrait_applied += 1
            elif action == "fade":
                await db.execute(
                    "UPDATE portrait SET status = 'faded', updated_at = ? WHERE id = ?",
                    (now_iso, row["id"]),
                )
                portrait_applied += 1
        for item in _as_list(data.get("diary_from_portrait"))[:20]:
            if not isinstance(item, dict):
                continue
            portrait = await self._get_portrait(db, umo, item.get("portrait_id"))
            if portrait is None or portrait["status"] != "active":
                continue
            key = f"portrait_diary:{umo}:{portrait['id']}"
            cursor = await db.execute(
                "SELECT value FROM plugin_meta WHERE key = ?", (key,)
            )
            if await cursor.fetchone():
                continue
            level = _normalize_level(item.get("level")) or "normal"
            _name, initial, _extend, _warn = self._level_params(level)
            text = str(portrait["content"] or "").strip()[:2000]
            if not text:
                continue
            cursor = await db.execute(
                "SELECT id FROM diary WHERE umo = ? AND deleted_at IS NULL "
                "AND content = ? LIMIT 1",
                (umo, text),
            )
            if await cursor.fetchone():
                await db.execute(
                    "INSERT OR REPLACE INTO plugin_meta (key, value) VALUES (?, ?)",
                    (key, now_iso),
                )
                continue
            title = _short_title(text, self._cfg_int("diary_title_max_chars", 40, 1, 200))
            expires = now + timedelta(days=initial)
            await db.execute(
                "INSERT INTO diary "
                "(umo, level, title, content, mood, unsaid, created_at, expires_at, "
                "base_days, extended_count, fuzzy, shelf_frozen) "
                "VALUES (?, ?, ?, ?, ?, '', ?, ?, ?, 0, 0, 0)",
                (
                    umo,
                    level,
                    title,
                    text,
                    "想起主人",
                    now_iso,
                    expires.isoformat(timespec="seconds"),
                    initial,
                ),
            )
            await db.execute(
                "INSERT OR REPLACE INTO plugin_meta (key, value) VALUES (?, ?)",
                (key, now_iso),
            )
            await db.execute(
                "UPDATE portrait SET last_mentioned_at = ?, updated_at = ? WHERE id = ?",
                (now_iso, now_iso, portrait["id"]),
            )
            from_portrait += 1
        return (
            f"diary={diary_applied} schedule={schedule_applied} "
            f"portrait={portrait_applied} from_portrait={from_portrait}"
        )
