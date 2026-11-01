"""大肥鱼抽屉：表情包收藏 + 主人随口小事的实时记录。

- 只收主人相关：主人私聊，或主人在群里发的表情包/小图
- 主人说“我喜欢/我几点睡/我习惯”时当场记下，不等凌晨结算
- 提供抽屉工具和命令，方便 AI 主动存取
"""

from __future__ import annotations

import hashlib
import json
import random
import re
from datetime import datetime
from pathlib import Path

import aiohttp
import aiosqlite

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.message_components import Image
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import Context, Star, StarTools

PLUGIN_NAME = "astrbot_plugin_dafeiyu_drawer"

SCHEMA = """
CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    umo TEXT NOT NULL,
    content TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'fact',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'auto',
    UNIQUE(umo, content)
);
CREATE TABLE IF NOT EXISTS stickers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sha256 TEXT NOT NULL UNIQUE,
    file TEXT NOT NULL,
    source_umo TEXT NOT NULL DEFAULT '',
    sender TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_notes_umo ON notes(umo, updated_at);
"""

IMAGE_EXTENSIONS = {".gif", ".png", ".jpg", ".jpeg", ".webp", ".bmp"}

NOTE_PATTERNS = [
    (
        re.compile(r"我(?:很|最|超|特别|挺)?喜欢([^，。！？；,.!?;\n]{1,40})"),
        "preference",
        "喜欢{0}",
    ),
    (
        re.compile(r"我(?:不喜欢|讨厌|不爱|受不了)([^，。！？；,.!?;\n]{1,40})"),
        "preference",
        "不喜欢{0}",
    ),
    (
        re.compile(
            r"我(?:一般|通常|平时|每天)?\s*([0-9一二三四五六七八九十两]{1,3})\s*点"
            r"(?:半|前|左右)?\s*(?:睡|睡觉|上床)"
        ),
        "schedule",
        "通常 {0} 点睡",
    ),
    (
        re.compile(
            r"我(?:一般|通常|平时|每天)?\s*([0-9一二三四五六七八九十两]{1,3})\s*点"
            r"(?:半|前|左右)?\s*(?:起床|起)"
        ),
        "schedule",
        "通常 {0} 点起床",
    ),
    (
        re.compile(r"我(?:习惯|经常|总是)([^，。！？；,.!?;\n]{1,40})"),
        "habit",
        "习惯{0}",
    ),
    (
        re.compile(r"我(?:对)([^，。！？；,.!?;\n]{1,20})过敏"),
        "fact",
        "对{0}过敏",
    ),
    (
        re.compile(r"我(?:的)?生日(?:是|在)?([^，。！？；,.!?;\n]{1,20})"),
        "fact",
        "生日{0}",
    ),
    (
        re.compile(r"叫我([^，。！？；,.!?;\n]{1,10})"),
        "fact",
        "称呼：{0}",
    ),
    (
        re.compile(r"记住([^，。！？；,.!?;\n]{1,60})"),
        "fact",
        "{0}",
    ),
]


def _now() -> datetime:
    """Return the current local time without microseconds."""
    return datetime.now().replace(microsecond=0)


def _clean(text: object, limit: int = 60) -> str:
    """Strip punctuation and surrounding whitespace.

    Args:
        text: Raw text.
        limit: Maximum characters.

    Returns:
        Cleaned text.
    """
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    value = value.strip("，。！？；、：,.!?;: ")
    return value[:limit]


def _normalize(text: object) -> str:
    """Normalize text for matching.

    Args:
        text: Raw text.

    Returns:
        Lowercase text without punctuation or spaces.
    """
    return re.sub(
        r"[\s，。！？、；：：“”‘’（）【】《》…—·,.!?;:\"'()\[\]{}<>~`|/\\-]+",
        "",
        str(text or "").lower(),
    )


class DafeiyuDrawerPlugin(Star):
    """表情包抽屉和主人小事抽屉。"""

    def __init__(self, context: Context, config: dict | None = None):
        super().__init__(context, config)
        self.cfg = config or {}
        self.data_root = Path(StarTools.get_data_dir(PLUGIN_NAME)).parents[1]
        self.plugin_dir = Path(StarTools.get_data_dir(PLUGIN_NAME))
        self.db_path = self.plugin_dir / "drawer.db"
        self.sticker_dir = self.plugin_dir / "stickers"
        self._db_ready = False

    # ------------------------------------------------------------------ setup
    async def _ensure_db(self) -> None:
        """Create the database and sticker directory on first use."""
        if self._db_ready:
            return
        self.plugin_dir.mkdir(parents=True, exist_ok=True)
        self.sticker_dir.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.db_path) as db:
            await db.executescript(SCHEMA)
            await db.commit()
        self._db_ready = True

    # ------------------------------------------------------------------ owner
    @staticmethod
    def _read_json(path: Path) -> dict:
        """Read a JSON object defensively."""
        try:
            if not path.is_file():
                return {}
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError, TypeError):
            return {}

    def _owner(self) -> tuple[str, str]:
        """Return the configured owner QQ and private-chat UMO.

        Returns:
            Tuple of QQ and UMO, either may be empty.
        """
        owner = self._read_json(self.data_root / "owner.json")
        qq = str(owner.get("qq") or "").strip()
        umo = str(owner.get("umo") or "").strip()
        if not qq:
            social = self._read_json(
                self.data_root
                / "config"
                / "astrbot_plugin_dafeiyu_social_config.json"
            )
            qq = str(social.get("admin_qq") or "").strip()
            umo = str(social.get("admin_umo") or "").strip()
        if not qq:
            qq = str(self.cfg.get("owner_qq") or "").strip()
            umo = str(self.cfg.get("owner_umo") or "").strip()
        return qq, umo

    def _is_owner(self, event: AstrMessageEvent) -> bool:
        """Return whether the event comes from the owner.

        When no owner is configured yet, private chats are treated as the
        owner's so the drawer still works before /认主.
        """
        qq, umo = self._owner()
        sender = str(event.get_sender_id() or "")
        current = str(getattr(event, "unified_msg_origin", "") or "")
        if qq and sender == qq:
            return True
        if umo and current == umo:
            return True
        if not qq and not umo:
            return event.is_private_chat()
        return False

    def _owner_umo(self, event: AstrMessageEvent) -> str:
        """Return the canonical owner UMO for note storage."""
        _qq, umo = self._owner()
        return umo or str(getattr(event, "unified_msg_origin", "") or "")

    # ------------------------------------------------------------------ notes
    def _extract_notes(self, text: str) -> list[tuple[str, str]]:
        """Extract small owner facts from one message.

        Args:
            text: User message.

        Returns:
            List of (content, category) tuples.
        """
        results: list[tuple[str, str]] = []
        for pattern, category, template in NOTE_PATTERNS:
            for match in pattern.finditer(text):
                value = _clean(match.group(1), 40)
                if not value:
                    continue
                note = _clean(template.format(value), 60)
                if len(_normalize(note)) < 2:
                    continue
                if (note, category) not in results:
                    results.append((note, category))
        return results

    async def _store_note(
        self,
        umo: str,
        content: str,
        category: str = "fact",
        source: str = "auto",
    ) -> int | None:
        """Insert or refresh one note.

        Args:
            umo: Owner UMO.
            content: Note text.
            category: preference, schedule, habit or fact.
            source: auto, self or manual.

        Returns:
            Note id, or None.
        """
        text = _clean(content, 120)
        if not umo or not text:
            return None
        now = _now().isoformat(timespec="seconds")
        await self._ensure_db()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO notes (umo, content, category, created_at, updated_at, source) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(umo, content) DO UPDATE SET "
                "updated_at = excluded.updated_at, category = excluded.category",
                (umo, text, category, now, now, source),
            )
            await db.commit()
            cursor = await db.execute(
                "SELECT id FROM notes WHERE umo = ? AND content = ?", (umo, text)
            )
            row = await cursor.fetchone()
            return int(row[0]) if row else None

    async def _notes_for(self, umo: str, limit: int = 50) -> list[aiosqlite.Row]:
        """Read recent notes for one owner.

        Args:
            umo: Owner UMO.
            limit: Maximum rows.

        Returns:
            Note rows ordered by update time.
        """
        await self._ensure_db()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM notes WHERE umo = ? ORDER BY updated_at DESC LIMIT ?",
                (umo, limit),
            )
            return list(await cursor.fetchall())

    def _pick_notes(
        self, notes: list[aiosqlite.Row], text: str, limit: int
    ) -> list[aiosqlite.Row]:
        """Pick notes relevant to the current message.

        Args:
            notes: Recent notes.
            text: Current message.
            limit: Maximum notes.

        Returns:
            Selected notes, most relevant first.
        """
        target = _normalize(text)
        grams = {target[i : i + 2] for i in range(max(0, len(target) - 1))}

        def score(row: aiosqlite.Row) -> tuple[int, str]:
            content = _normalize(row["content"])
            shared = sum(
                1 for i in range(max(0, len(content) - 1)) if content[i : i + 2] in grams
            )
            return shared, str(row["updated_at"] or "")

        ranked = sorted(notes, key=score, reverse=True)
        picked: list[aiosqlite.Row] = []
        total = 0
        for row in ranked:
            length = len(str(row["content"]))
            if picked and total + length > 200:
                continue
            picked.append(row)
            total += length
            if len(picked) >= limit:
                break
        return picked

    def _notes_block(self, rows: list[aiosqlite.Row]) -> str:
        """Build a short system prompt block from notes."""
        if not rows:
            return ""
        lines = [f"- {row['content']}" for row in rows]
        return (
            "【抽屉】\n"
            "你顺手记得主人的这些小事：\n"
            + "\n".join(lines)
            + "\n这些只是随口记下的，不要像背清单，也不要一次全说出来；"
            "不确定就别说，别向其他人透露。"
        )

    # ------------------------------------------------------------------ sticker
    async def _fetch_image(self, comp: Image) -> tuple[bytes, str] | None:
        """Read image bytes from a local path or URL."""
        source = str(comp.path or comp.file or comp.url or "").strip()
        if not source:
            return None
        limit = int(float(self.cfg.get("sticker_max_mb", 1.5) or 1.5) * 1024 * 1024)
        if source.startswith("file://"):
            source = source[7:]
            if re.match(r"^/[A-Za-z]:", source):
                source = source[1:]
        if source.startswith(("http://", "https://")):
            try:
                timeout = aiohttp.ClientTimeout(total=15)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.get(source) as resp:
                        if resp.status != 200:
                            return None
                        data = await resp.content.read(limit + 1)
                        mime = str(resp.headers.get("Content-Type", ""))
            except Exception:
                return None
            if len(data) > limit:
                return None
            ext = Path(source.split("?")[0]).suffix.lower()
            if ext not in IMAGE_EXTENSIONS:
                for marker, suffix in (
                    ("image/gif", ".gif"),
                    ("image/png", ".png"),
                    ("image/webp", ".webp"),
                    ("image/jpeg", ".jpg"),
                ):
                    if marker in mime:
                        ext = suffix
                        break
            return data, ext if ext in IMAGE_EXTENSIONS else ".jpg"
        path = Path(source)
        try:
            if not path.is_file() or path.stat().st_size > limit:
                return None
            return path.read_bytes(), path.suffix.lower()
        except OSError:
            return None

    async def _save_sticker(self, event: AstrMessageEvent, comp: Image) -> None:
        """Store one owner-sent sticker, deduplicated by sha256."""
        if not bool(self.cfg.get("sticker_collect", True)):
            return
        if not self._is_owner(event):
            return
        fetched = await self._fetch_image(comp)
        if fetched is None:
            return
        data, ext = fetched
        if ext not in IMAGE_EXTENSIONS:
            ext = ".jpg"
        if bool(self.cfg.get("sticker_only_gif", False)) and ext != ".gif":
            return
        digest = hashlib.sha256(data).hexdigest()
        await self._ensure_db()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT id FROM stickers WHERE sha256 = ?", (digest,)
            )
            if await cursor.fetchone():
                return
            folder = self.sticker_dir / _now().strftime("%Y%m")
            folder.mkdir(parents=True, exist_ok=True)
            name = f"{digest[:16]}{ext}"
            path = folder / name
            try:
                path.write_bytes(data)
            except OSError:
                return
            note = _clean(getattr(event, "message_str", "") or "来自聊天", 40)
            await db.execute(
                "INSERT INTO stickers (sha256, file, source_umo, sender, note, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    digest,
                    str(path.relative_to(self.sticker_dir)).replace("\\", "/"),
                    str(getattr(event, "unified_msg_origin", "") or ""),
                    str(event.get_sender_id() or ""),
                    note,
                    _now().isoformat(timespec="seconds"),
                ),
            )
            await db.commit()
            await self._trim_stickers(db)

    async def _trim_stickers(self, db: aiosqlite.Connection) -> None:
        """Delete the oldest stickers beyond the configured cap."""
        try:
            limit = int(self.cfg.get("sticker_max_count", 300) or 300)
        except (TypeError, ValueError):
            limit = 300
        limit = max(20, min(limit, 2000))
        cursor = await db.execute("SELECT COUNT(*) FROM stickers")
        total = int((await cursor.fetchone())[0] or 0)
        if total <= limit:
            return
        cursor = await db.execute(
            "SELECT id, file FROM stickers ORDER BY id ASC LIMIT ?", (total - limit,)
        )
        for row in await cursor.fetchall():
            path = self.sticker_dir / str(row[1])
            if path.is_file():
                try:
                    path.unlink()
                except OSError:
                    pass
            await db.execute("DELETE FROM stickers WHERE id = ?", (row[0],))

    async def _pick_sticker(self, keyword: str = "") -> aiosqlite.Row | None:
        """Pick a sticker, optionally matching a keyword."""
        await self._ensure_db()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            if keyword:
                pattern = f"%{keyword}%"
                cursor = await db.execute(
                    "SELECT * FROM stickers WHERE note LIKE ? OR file LIKE ? "
                    "ORDER BY RANDOM() LIMIT 1",
                    (pattern, pattern),
                )
            else:
                cursor = await db.execute(
                    "SELECT * FROM stickers ORDER BY RANDOM() LIMIT 1"
                )
            return await cursor.fetchone()

    def _sticker_path(self, row: aiosqlite.Row) -> Path | None:
        """Resolve a stored sticker path inside the sticker directory."""
        try:
            path = (self.sticker_dir / str(row["file"])).resolve()
            path.relative_to(self.sticker_dir.resolve())
        except (OSError, ValueError):
            return None
        return path if path.is_file() else None

    # ------------------------------------------------------------------- hooks
    @filter.on_llm_request()
    async def capture_and_inject(self, event: AstrMessageEvent, req: ProviderRequest):
        """Capture owner facts/stickers and inject a small drawer block."""
        if not bool(self.cfg.get("enabled", True)):
            return
        try:
            await self._ensure_db()
            umo = str(getattr(event, "unified_msg_origin", "") or "")
            if not umo:
                return
            text = str(getattr(event, "message_str", "") or "").strip()

            if (
                text
                and bool(self.cfg.get("auto_capture", True))
                and self._is_owner(event)
            ):
                for content, category in self._extract_notes(text):
                    await self._store_note(
                        self._owner_umo(event), content, category, "auto"
                    )

            if not event.get_extra("_drawer_stickers"):
                event.set_extra("_drawer_stickers", True)
                for comp in event.get_messages():
                    if isinstance(comp, Image):
                        await self._save_sticker(event, comp)

            if event.get_extra("_drawer_injected"):
                return
            event.set_extra("_drawer_injected", True)
            if not bool(self.cfg.get("inject_notes", True)):
                return
            if not event.is_private_chat() or not self._is_owner(event):
                return
            notes = await self._notes_for(self._owner_umo(event))
            picked = self._pick_notes(
                notes,
                text,
                int(self.cfg.get("max_inject_notes", 5) or 5),
            )
            block = self._notes_block(picked)
            if block:
                req.system_prompt = (req.system_prompt or "") + "\n\n" + block
        except Exception:
            logger.exception("[drawer] 抽屉注入失败")

    # ------------------------------------------------------------------ tools
    @filter.llm_tool(name="drawer_remember")
    async def drawer_remember(
        self,
        event: AstrMessageEvent,
        content: str = "",
        category: str = "fact",
    ) -> str:
        """把主人随口提到的小事立刻收进抽屉。

        Args:
            content(string): 一句小事，例如「喜欢火锅」「通常 23 点睡」。
            category(string): preference、schedule、habit 或 fact。

        Returns:
            str: 记录结果。
        """
        if not self._is_owner(event):
            return "只有主人能往抽屉里放东西。"
        text = _clean(content, 120)
        if not text:
            return "内容是空的。"
        safe_category = (
            category
            if category in {"preference", "schedule", "habit", "fact"}
            else "fact"
        )
        note_id = await self._store_note(
            self._owner_umo(event), text, safe_category, "self"
        )
        if note_id is None:
            return "这条没放进抽屉。"
        return f"收好了（n{note_id}）。"

    @filter.llm_tool(name="drawer_search")
    async def drawer_search(
        self, event: AstrMessageEvent, keyword: str = "", limit: int = 5
    ) -> str:
        """在抽屉里翻找主人的小事。

        Args:
            keyword(string): 关键词。
            limit(int): 最多返回几条，默认 5。

        Returns:
            str: 匹配的小事。
        """
        if not self._is_owner(event):
            return "只有主人能翻抽屉。"
        use_limit = max(1, min(int(limit or 5), 20))
        notes = await self._notes_for(self._owner_umo(event), 200)
        if keyword:
            rows = [row for row in notes if keyword in str(row["content"])]
        else:
            rows = notes
        if not rows:
            return f"抽屉里没找到「{keyword}」。"
        lines = [f"n{row['id']} {row['content']}" for row in rows[:use_limit]]
        return "抽屉里翻到：\n" + "\n".join(lines)

    @filter.llm_tool(name="drawer_sticker")
    async def drawer_sticker(
        self, event: AstrMessageEvent, keyword: str = ""
    ) -> str:
        """从抽屉里发一张收藏的表情包。

        Args:
            keyword(string): 可选关键词，按备注或文件名匹配。

        Returns:
            str: 发送结果。
        """
        if not self._is_owner(event):
            return "只有主人能翻抽屉。"
        row = await self._pick_sticker(str(keyword or "").strip())
        if row is None:
            return "抽屉里还没有表情包。"
        path = self._sticker_path(row)
        if path is None:
            return "这张表情包的文件找不到了。"
        await event.send(MessageChain([Image.fromFileSystem(str(path))]))
        return "表情包发了。"

    # ---------------------------------------------------------------- commands
    @filter.command("抽屉")
    async def cmd_drawer(self, event: AstrMessageEvent):
        """查看、记录、查找抽屉，或发送收藏的表情包。"""
        if not bool(self.cfg.get("enabled", True)):
            return
        if not self._is_owner(event):
            yield event.plain_result("这个只给主人用。")
            return
        umo = self._owner_umo(event)
        args = (event.message_str or "").strip()
        for prefix in ("/抽屉", "抽屉"):
            if args.startswith(prefix):
                args = args[len(prefix) :].strip()
                break

        if args.startswith("表情包"):
            keyword = args[len("表情包") :].strip()
            row = await self._pick_sticker(keyword)
            if row is None:
                yield event.plain_result("抽屉里还没有表情包。")
                return
            path = self._sticker_path(row)
            if path is None:
                yield event.plain_result("这张表情包的文件找不到了。")
                return
            yield event.chain_result([Image.fromFileSystem(str(path))])
            return

        if args.startswith("记"):
            content = args[len("记") :].strip()
            if not content:
                yield event.plain_result("要记什么？例如 /抽屉 记 喜欢火锅。")
                return
            note_id = await self._store_note(umo, content, "fact", "manual")
            yield event.plain_result(
                f"收好了（n{note_id}）。" if note_id else "这条没记上。"
            )
            return

        if args.startswith("找"):
            keyword = args[len("找") :].strip()
            notes = await self._notes_for(umo, 200)
            rows = (
                [row for row in notes if keyword in str(row["content"])]
                if keyword
                else notes
            )
            if not rows:
                yield event.plain_result(f"抽屉里没找到「{keyword}」。")
                return
            lines = [f"n{row['id']} {row['content']}" for row in rows[:10]]
            yield event.plain_result("抽屉里翻到：\n" + "\n".join(lines))
            return

        if args.startswith("删"):
            target = args[len("删") :].strip().lstrip("n#")
            if not target.isdigit():
                yield event.plain_result("要删哪条？例如 /抽屉 删 3。")
                return
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(
                    "DELETE FROM notes WHERE id = ? AND umo = ?",
                    (int(target), umo),
                )
                await db.commit()
            yield event.plain_result(f"n{target} 删掉了。")
            return

        notes = await self._notes_for(umo, 20)
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("SELECT COUNT(*) FROM stickers")
            sticker_count = int((await cursor.fetchone())[0] or 0)
        lines = [
            f"抽屉：小事 {len(notes)} 条 · 表情包 {sticker_count} 张",
        ]
        for row in notes[:8]:
            lines.append(f"n{row['id']} [{row['category']}] {row['content']}")
        lines.append("用 /抽屉 找 <关键词>、/抽屉 记 <内容>、/抽屉 表情包 [关键词] 管理。")
        yield event.plain_result("\n".join(lines))
