"""大肥鱼涟漪记忆：一个像日常琐事一样会变淡的记忆池。

进入池子的小事按类似艾宾浩斯曲线的速度衰减：
- 刚放进去时太新鲜，不会主动提起；
- 沉到曲线中部时，聊天里再次撞见关键词才会被模糊地想起；
- 沉到池底后，要重新被提到（重复 4 个字或意思相近）才会被勾回中部；
- 同一会话 90 分钟内最多被勾一次，避免一直翻旧账。

所有数据只写入本插件自己的 SQLite，不修改 AstrBot 核心、人格或其他插件。
"""

from __future__ import annotations

import asyncio
import json
import math
import re
import struct
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from pathlib import Path

import aiosqlite

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import LLMResponse, ProviderRequest
from astrbot.api.star import Context, Star, StarTools

PLUGIN_NAME = "astrbot_plugin_dafeiyu_ripple"

SCHEMA = """
CREATE TABLE IF NOT EXISTS ripple (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    umo TEXT NOT NULL,
    content TEXT NOT NULL,
    keywords TEXT NOT NULL DEFAULT '[]',
    source TEXT NOT NULL DEFAULT 'auto',
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    touched_at TEXT NOT NULL,
    batch_date TEXT NOT NULL,
    scale_days REAL NOT NULL DEFAULT 1.8,
    recall_count INTEGER NOT NULL DEFAULT 0,
    revived_count INTEGER NOT NULL DEFAULT 0,
    last_recalled_at TEXT,
    vector BLOB
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    umo TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ripple_state (
    umo TEXT PRIMARY KEY,
    last_recall_at TEXT
);
CREATE TABLE IF NOT EXISTS plugin_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ripple_umo_status
    ON ripple(umo, status, batch_date);
CREATE INDEX IF NOT EXISTS idx_ripple_umo_touched
    ON ripple(umo, touched_at);
CREATE INDEX IF NOT EXISTS idx_ripple_messages
    ON messages(umo, created_at);
"""

# 空词、口头禅和日常高频词。它们既能防止关键词太泛，也能减少误触发。
DEFAULT_BLOCKLIST = (
    "就是,这个,那个,一个,什么,怎么,然后,现在,今天,明天,昨天,前天,早上,上午,中午,"
    "下午,晚上,凌晨,半夜,吃饭,睡觉,起床,上班,下班,回家,出门,哈哈,真的,感觉,觉得,"
    "知道,时候,东西,事情,问题,可以,没有,不是,我们,你们,他们,她们,自己,还是,但是,"
    "因为,所以,如果,其实,应该,可能,一下,有点,已经,起来,出来,过来,回来,说话,聊天,"
    "手机,时间,地方,样子,情况,开始,最后,继续,直接,反正,确实,基本,主要,需要,准备,"
    "看到,听到,想到,说了,讲了,聊了,嗯,啊,哦,好,行,对,是,在,有,我,你,他,她,它,的,"
    "了,着,过,吗,呢,吧,嘛,呀,就,都,也,很,太,又,再,还,去,来,上,下,大,小,多,少,天,"
    "点,次,个,这,那,啥,谁,哪,呗,啦,咯,喔,诶,唉"
)

BAND_NAMES = {"fresh": "新鲜", "mid": "中部", "sunk": "沉底"}


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


def _normalize(text: object) -> str:
    """Strip whitespace and punctuation so matching only sees characters.

    Args:
        text: Raw text.

    Returns:
        Lowercase text without spaces or punctuation.
    """
    return re.sub(
        r"[\s，。！？、；：“”‘’（）【】《》…—·,.!?;:\"'()\[\]{}<>~`|/\\-]+",
        "",
        str(text or "").lower(),
    )


def _longest_run(a: str, b: str) -> int:
    """Return the length of the longest common substring.

    Args:
        a: First normalized text.
        b: Second normalized text.

    Returns:
        Length of the longest shared consecutive run.
    """
    if not a or not b:
        return 0
    previous = [0] * (len(b) + 1)
    best = 0
    for char in a:
        current = [0] * (len(b) + 1)
        for index, other in enumerate(b, 1):
            if char == other:
                value = previous[index - 1] + 1
                current[index] = value
                if value > best:
                    best = value
        previous = current
    return best


def _containment(a: str, b: str) -> float:
    """Return how much of the shorter text is covered by the longer one.

    Args:
        a: First normalized text.
        b: Second normalized text.

    Returns:
        Coverage ratio between 0 and 1.
    """
    if not a or not b:
        return 0.0
    matcher = SequenceMatcher(None, a, b, autojunk=False)
    matched = sum(block.size for block in matcher.get_matching_blocks())
    return min(1.0, matched / max(1, min(len(a), len(b))))


def _pack_vector(vector: list[float]) -> bytes:
    """Pack a float vector into a compact little-endian blob.

    Args:
        vector: Embedding values.

    Returns:
        Packed bytes.
    """
    return struct.pack(f"<{len(vector)}f", *vector)


def _unpack_vector(blob: bytes | None) -> list[float] | None:
    """Unpack a stored embedding blob.

    Args:
        blob: Packed bytes from SQLite.

    Returns:
        Vector values, or None when the blob is missing or malformed.
    """
    if not blob:
        return None
    try:
        if len(blob) % 4:
            return None
        return list(struct.unpack(f"<{len(blob) // 4}f", blob))
    except (struct.error, TypeError):
        return None


def _cosine(a: list[float], b: list[float]) -> float:
    """Return cosine similarity between two vectors.

    Args:
        a: First vector.
        b: Second vector.

    Returns:
        Similarity between -1 and 1, or 0 when vectors are unusable.
    """
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if not norm_a or not norm_b:
        return 0.0
    return dot / (norm_a * norm_b)


def _extract_json(text: str) -> dict | None:
    """Extract the first plausible JSON object from model output.

    Args:
        text: Raw model output.

    Returns:
        Parsed dictionary, or None.
    """
    if not text:
        return None
    cleaned = re.sub(r"^```(?:json)?\s*", "", text.strip(), flags=re.IGNORECASE)
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


class DafeiyuRipplePlugin(Star):
    """涟漪记忆：日常琐事的模糊记忆池。"""

    def __init__(self, context: Context, config: dict | None = None):
        super().__init__(context, config)
        self.cfg = config or {}
        try:
            data_dir = Path(StarTools.get_data_dir(PLUGIN_NAME))
        except Exception:
            logger.exception("[ripple] 无法解析插件数据目录，使用默认路径")
            data_dir = (
                Path(__file__).resolve().parents[3]
                / "data"
                / "plugin_data"
                / PLUGIN_NAME
            )
        self.data_dir = data_dir
        self.db_path = data_dir / "ripple.db"
        self._db_ready = False
        self._db_lock = asyncio.Lock()
        self._extract_task: asyncio.Task | None = None
        self._last_cleanup: datetime | None = None
        self._blocklist_cache: set[str] | None = None
        self._embedding_checked = False
        self._embedding_provider = None

    # ------------------------------------------------------------------ setup
    async def _ensure_db(self) -> None:
        """Create the database file and tables on first use."""
        if self._db_ready:
            return
        async with self._db_lock:
            if self._db_ready:
                return
            self.data_dir.mkdir(parents=True, exist_ok=True)
            async with aiosqlite.connect(self.db_path) as db:
                await db.executescript(SCHEMA)
                await db.commit()
            self._db_ready = True

    def _ensure_extract_task(self) -> None:
        """Start the nightly extraction loop once."""
        if not self.cfg.get("enabled", True) or not self.cfg.get(
            "extract_enabled", True
        ):
            return
        if self._extract_task is None or self._extract_task.done():
            try:
                self._extract_task = asyncio.create_task(self._extract_loop())
            except RuntimeError:
                pass

    @filter.on_astrbot_loaded()
    async def _on_loaded(self):
        """Initialize storage and the extraction loop after AstrBot loads."""
        try:
            await self._ensure_db()
            await self._cleanup_messages()
            self._ensure_extract_task()
        except Exception:
            logger.exception("[ripple] 插件初始化失败")

    async def terminate(self):
        """Cancel the background extraction loop on unload."""
        if self._extract_task and not self._extract_task.done():
            self._extract_task.cancel()
            try:
                await self._extract_task
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

    def _cfg_float(self, key: str, default: float, low: float, high: float) -> float:
        """Read a bounded float configuration value.

        Args:
            key: Configuration key.
            default: Fallback value.
            low: Minimum allowed value.
            high: Maximum allowed value.

        Returns:
            Bounded float.
        """
        try:
            value = float(self.cfg.get(key, default))
        except (TypeError, ValueError):
            value = default
        return max(low, min(high, value))

    def _cfg_str(self, key: str, default: str = "") -> str:
        """Read a trimmed string configuration value.

        Args:
            key: Configuration key.
            default: Fallback value.

        Returns:
            Trimmed string.
        """
        return str(self.cfg.get(key, default) or default).strip()

    def _cfg_tokens(self, key: str, default: str = "") -> list[str]:
        """Split a comma or newline separated configuration value.

        Args:
            key: Configuration key.
            default: Fallback value.

        Returns:
            Non-empty tokens in original order.
        """
        raw = self._cfg_str(key, default)
        return [item.strip() for item in re.split(r"[,，、\n]+", raw) if item.strip()]

    def _now(self) -> datetime:
        """Return the current local time without microseconds.

        Returns:
            Naive local datetime.
        """
        return datetime.now().replace(microsecond=0)

    def _blocklist(self) -> set[str]:
        """Return the cached keyword blocklist.

        Returns:
            Set of blocked words.
        """
        if self._blocklist_cache is None:
            self._blocklist_cache = set(
                self._cfg_tokens("blocklist", DEFAULT_BLOCKLIST)
            )
        return self._blocklist_cache

    def _usable_keyword(self, word: object) -> bool:
        """Return whether a keyword is specific enough to trigger recall.

        Args:
            word: Candidate keyword.

        Returns:
            True when the keyword is short, concrete and not blocked.
        """
        text = str(word or "").strip()
        if not 2 <= len(text) <= 8:
            return False
        if text.isdigit():
            return False
        if text in self._blocklist():
            return False
        return bool(_normalize(text))

    def _clean_keywords(self, raw: object) -> list[str]:
        """Normalize, filter and de-duplicate keywords.

        Args:
            raw: List, JSON string, or comma separated string.

        Returns:
            At most the configured number of usable keywords.
        """
        items: list[str] = []
        if isinstance(raw, str):
            items = [item for item in re.split(r"[,，、\s]+", raw) if item]
        elif isinstance(raw, (list, tuple, set)):
            items = [str(item) for item in raw]
        limit = self._cfg_int("keywords_max", 4, 1, 8)
        result: list[str] = []
        for item in items:
            text = item.strip()
            if not self._usable_keyword(text) or text in result:
                continue
            result.append(text)
            if len(result) >= limit:
                break
        return result

    def _retention(self, row: aiosqlite.Row, now: datetime) -> float:
        """Return the current curve position of a memory.

        Args:
            row: Ripple row.
            now: Current local time.

        Returns:
            Retention ratio between 0 and 1.
        """
        touched = _parse_dt(row["touched_at"]) or _parse_dt(row["created_at"]) or now
        age_days = max(0.0, (now - touched).total_seconds() / 86400.0)
        scale = float(row["scale_days"] or 1.8)
        return math.exp(-age_days / max(scale, 0.1))

    def _band(self, retention: float) -> str:
        """Classify a curve position into fresh, middle or sunk.

        Args:
            retention: Retention ratio.

        Returns:
            One of fresh, mid, sunk.
        """
        fresh = self._cfg_float("fresh_retention", 0.62, 0.2, 0.95)
        mid = self._cfg_float("mid_retention", 0.16, 0.02, 0.8)
        if mid >= fresh:
            mid = fresh - 0.05
        if retention >= fresh:
            return "fresh"
        if retention >= mid:
            return "mid"
        return "sunk"

    def _match_score(
        self, text: str, row: aiosqlite.Row, need_overlap: int
    ) -> float:
        """Score how strongly a message points at one memory.

        Args:
            text: Normalized current message.
            row: Ripple row.
            need_overlap: Minimum shared consecutive characters.

        Returns:
            Score between 0 and 1; 0 means no match.
        """
        content = _normalize(row["content"])
        if not content:
            return 0.0
        score = 0.0
        for keyword in self._row_keywords(row):
            if keyword and keyword in text:
                score = max(score, 0.92)
        run = _longest_run(text, content)
        if run >= need_overlap:
            score = max(score, 0.74 + min(run - need_overlap, 6) * 0.03)
        if score < 0.8 and _containment(text, content) >= self._cfg_float(
            "recall_similarity", 0.8, 0.4, 1.0
        ):
            score = max(score, 0.82)
        return score

    def _row_keywords(self, row: aiosqlite.Row) -> list[str]:
        """Read keywords from a ripple row.

        Args:
            row: Ripple row.

        Returns:
            Usable keywords.
        """
        try:
            data = json.loads(row["keywords"] or "[]")
        except (json.JSONDecodeError, TypeError):
            return []
        if not isinstance(data, list):
            return []
        return [str(item) for item in data if self._usable_keyword(item)]

    # -------------------------------------------------------------- embeddings
    async def _embedding(self) -> object | None:
        """Return the first configured embedding provider, if any.

        Returns:
            Embedding provider instance or None.
        """
        if self._embedding_checked:
            return self._embedding_provider
        self._embedding_checked = True
        if not self.cfg.get("embedding_enabled", True):
            return None
        try:
            providers = self.context.get_all_embedding_providers()
        except Exception:
            providers = []
        if providers:
            self._embedding_provider = providers[0]
            logger.info(
                f"[ripple] 使用向量模型做语义找回: {type(providers[0]).__name__}"
            )
        return self._embedding_provider

    async def _embed(self, text: str) -> list[float] | None:
        """Embed a short text, returning None when unavailable or too slow.

        Args:
            text: Text to embed.

        Returns:
            Vector values or None.
        """
        provider = await self._embedding()
        if provider is None or not text:
            return None
        try:
            vector = await asyncio.wait_for(
                provider.get_embedding(text[:500]), timeout=6
            )
        except Exception:
            logger.warning("[ripple] 向量模型不可用，退回字面匹配")
            return None
        if not isinstance(vector, list) or not vector:
            return None
        try:
            return [float(value) for value in vector]
        except (TypeError, ValueError):
            return None

    # -------------------------------------------------------------- persistence
    async def _meta_get(self, key: str) -> str | None:
        """Read one plugin meta value.

        Args:
            key: Meta key.

        Returns:
            Stored value or None.
        """
        await self._ensure_db()
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT value FROM plugin_meta WHERE key = ?", (key,)
            )
            row = await cursor.fetchone()
        return str(row[0]) if row else None

    async def _meta_set(self, key: str, value: str) -> None:
        """Write one plugin meta value.

        Args:
            key: Meta key.
            value: Value to store.
        """
        await self._ensure_db()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO plugin_meta (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
            await db.commit()

    async def _insert(
        self,
        db: aiosqlite.Connection,
        umo: str,
        content: str,
        keywords: list[str],
        source: str,
        now: datetime,
    ) -> tuple[int | None, bool]:
        """Insert one memory, merging it into a near-duplicate when possible.

        Args:
            db: Open database connection.
            umo: Unified message origin.
            content: Memory text.
            keywords: Trigger keywords.
            source: auto, self or manual.
            now: Current local time.

        Returns:
            Tuple of memory id and whether an existing memory was merged.
        """
        text = str(content or "").strip()
        if not text:
            return None, False
        max_chars = self._cfg_int("content_max_chars", 60, 10, 200)
        text = text[:max_chars]
        normalized = _normalize(text)
        if len(normalized) < 4:
            return None, False
        cursor = await db.execute(
            "SELECT * FROM ripple WHERE umo = ? AND status = 'active' "
            "ORDER BY id DESC LIMIT 200",
            (umo,),
        )
        rows = await cursor.fetchall()
        for row in rows:
            other = _normalize(row["content"])
            if not other:
                continue
            if normalized == other or _containment(normalized, other) >= 0.86:
                merged = sorted(set(self._row_keywords(row)) | set(keywords))
                await db.execute(
                    "UPDATE ripple SET touched_at = ?, keywords = ? WHERE id = ?",
                    (
                        now.isoformat(timespec="seconds"),
                        json.dumps(merged, ensure_ascii=False),
                        row["id"],
                    ),
                )
                return int(row["id"]), True
        vector = await self._embed(text)
        cursor = await db.execute(
            "INSERT INTO ripple (umo, content, keywords, source, status, "
            "created_at, touched_at, batch_date, scale_days, vector) "
            "VALUES (?, ?, ?, ?, 'active', ?, ?, ?, ?, ?)",
            (
                umo,
                text,
                json.dumps(keywords, ensure_ascii=False),
                source,
                now.isoformat(timespec="seconds"),
                now.isoformat(timespec="seconds"),
                now.date().isoformat(),
                self._cfg_float("base_scale_days", 1.8, 0.2, 30.0),
                _pack_vector(vector) if vector else None,
            ),
        )
        await self._trim_pool(db, umo)
        return int(cursor.lastrowid or 0) or None, False

    async def _trim_pool(self, db: aiosqlite.Connection, umo: str) -> None:
        """Archive the oldest memories beyond the configured pool size.

        Args:
            db: Open database connection.
            umo: Unified message origin.
        """
        limit = self._cfg_int("pool_limit", 400, 20, 5000)
        cursor = await db.execute(
            "SELECT COUNT(*) FROM ripple WHERE umo = ? AND status = 'active'",
            (umo,),
        )
        total = int((await cursor.fetchone())[0] or 0)
        if total <= limit:
            return
        await db.execute(
            "UPDATE ripple SET status = 'archived' WHERE id IN ("
            "SELECT id FROM ripple WHERE umo = ? AND status = 'active' "
            "ORDER BY touched_at ASC LIMIT ?)",
            (umo, total - limit),
        )

    async def _add_message(self, umo: str, role: str, content: str) -> None:
        """Store one chat message and clean old ones hourly.

        Args:
            umo: Unified message origin.
            role: user or assistant.
            content: Message text.
        """
        text = str(content or "").strip()
        if not text:
            return
        text = text[-4000:]
        now = self._now()
        await self._ensure_db()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO messages (umo, role, content, created_at) "
                "VALUES (?, ?, ?, ?)",
                (umo, role, text, now.isoformat(timespec="seconds")),
            )
            await db.commit()
        if (
            self._last_cleanup is None
            or (now - self._last_cleanup).total_seconds() > 3600
        ):
            await self._cleanup_messages()

    async def _cleanup_messages(self) -> None:
        """Delete chat transcripts older than three days."""
        await self._ensure_db()
        cutoff = (self._now() - timedelta(days=3)).isoformat(timespec="seconds")
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM messages WHERE created_at < ?", (cutoff,))
            await db.commit()
        self._last_cleanup = self._now()

    # ------------------------------------------------------------------ recall
    async def _revive(self, umo: str, text: str, now: datetime) -> set[int]:
        """Hook sunk memories back to the middle of the curve.

        A sunk memory is revived when the new message repeats enough characters,
        shares a configured special keyword, or is semantically close enough.

        Args:
            umo: Unified message origin.
            text: Normalized message text.
            now: Current local time.

        Returns:
            Ids revived during this turn; they are not recalled immediately.
        """
        need = self._cfg_int("revive_min_overlap", 4, 2, 12)
        similarity = self._cfg_float("revive_similarity", 0.8, 0.4, 1.0)
        specials = self._cfg_tokens("special_keywords")
        await self._ensure_db()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM ripple WHERE umo = ? AND status = 'active' "
                "AND batch_date < ? ORDER BY id DESC LIMIT 200",
                (umo, now.date().isoformat()),
            )
            rows = await cursor.fetchall()
        if not rows:
            return set()
        sunk = [row for row in rows if self._band(self._retention(row, now)) == "sunk"]
        if not sunk:
            return set()
        limit = self._cfg_int("max_revive_per_turn", 2, 1, 5)
        confirmed: list[int] = []
        semantic: list[aiosqlite.Row] = []
        for row in sunk:
            content = _normalize(row["content"])
            if not content:
                continue
            hit = any(key and key in text for key in self._row_keywords(row))
            if not hit and specials:
                hit = any(key in text and key in content for key in specials)
            if not hit and _longest_run(text, content) >= need:
                hit = True
            if hit:
                confirmed.append(int(row["id"]))
                continue
            coverage = _containment(text, content)
            if coverage >= similarity:
                confirmed.append(int(row["id"]))
            elif row["vector"] and coverage >= 0.3:
                semantic.append(row)
            if len(confirmed) >= limit:
                break
        # Only pay for an embedding call when a lexical near-miss exists.
        if len(confirmed) < limit and semantic:
            message_vector = await self._embed(text)
            if message_vector:
                for row in semantic:
                    stored = _unpack_vector(row["vector"])
                    if stored and _cosine(message_vector, stored) >= similarity:
                        confirmed.append(int(row["id"]))
                        if len(confirmed) >= limit:
                            break
        revived: set[int] = set()
        by_id = {int(row["id"]): row for row in sunk}
        for memory_id in confirmed[:limit]:
            row = by_id.get(memory_id)
            if row is None:
                continue
            scale = float(row["scale_days"] or 1.8)
            target = self._cfg_float("revive_target_retention", 0.45, 0.2, 0.8)
            offset = timedelta(days=scale * math.log(1.0 / target))
            await self._mark_revived(umo, memory_id, now - offset)
            revived.add(memory_id)
            logger.info(
                f"[ripple] 勾回中部: {umo} #{memory_id} {row['content'][:24]}"
            )
        return revived

    async def _mark_revived(
        self, umo: str, memory_id: int, touched: datetime
    ) -> None:
        """Place one memory back near the middle of the curve.

        Args:
            umo: Unified message origin.
            memory_id: Ripple id.
            touched: Backdated touch time that yields the target retention.
        """
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE ripple SET touched_at = ?, "
                "revived_count = revived_count + 1 WHERE id = ? AND umo = ?",
                (touched.isoformat(timespec="seconds"), memory_id, umo),
            )
            await db.commit()

    async def _pick_recall(
        self, umo: str, text: str, now: datetime, skip: set[int]
    ) -> aiosqlite.Row | None:
        """Pick at most one middle-curve memory that the message should surface.

        Args:
            umo: Unified message origin.
            text: Normalized message text.
            now: Current local time.
            skip: Ids just revived in this turn.

        Returns:
            Matching row or None.
        """
        cooldown = self._cfg_int("recall_cooldown_minutes", 90, 1, 1440)
        await self._ensure_db()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT last_recall_at FROM ripple_state WHERE umo = ?", (umo,)
            )
            state = await cursor.fetchone()
            if state and state["last_recall_at"]:
                last = _parse_dt(state["last_recall_at"])
                if last and now - last < timedelta(minutes=cooldown):
                    return None
            cursor = await db.execute(
                "SELECT * FROM ripple WHERE umo = ? AND status = 'active' "
                "AND batch_date < ? ORDER BY id DESC LIMIT 200",
                (umo, now.date().isoformat()),
            )
            rows = await cursor.fetchall()
        need = self._cfg_int("recall_min_overlap", 4, 2, 12)
        specials = self._cfg_tokens("special_keywords")
        best: aiosqlite.Row | None = None
        best_score = 0.0
        for row in rows:
            if int(row["id"]) in skip:
                continue
            if self._band(self._retention(row, now)) != "mid":
                continue
            score = self._match_score(text, row, need)
            content = _normalize(row["content"])
            if specials and any(
                key in text and (key in content or key in self._row_keywords(row))
                for key in specials
            ):
                score = 1.0
            if score > best_score:
                best_score = score
                best = row
        if best is None or best_score < 0.74:
            return None
        return best

    async def _mark_recalled(self, umo: str, memory_id: int) -> None:
        """Record that a memory surfaced and start the cooldown.

        Args:
            umo: Unified message origin.
            memory_id: Ripple id.
        """
        now = self._now()
        boosted = min(
            float(self._cfg_float("base_scale_days", 1.8, 0.2, 30.0))
            * self._cfg_float("recall_boost", 1.75, 1.0, 4.0),
            self._cfg_float("max_scale_days", 10.0, 1.0, 60.0),
        )
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE ripple SET touched_at = ?, last_recalled_at = ?, "
                "recall_count = recall_count + 1, "
                "scale_days = MIN(MAX(scale_days * ?, ?), ?) "
                "WHERE id = ? AND umo = ?",
                (
                    now.isoformat(timespec="seconds"),
                    now.isoformat(timespec="seconds"),
                    self._cfg_float("recall_boost", 1.75, 1.0, 4.0),
                    self._cfg_float("base_scale_days", 1.8, 0.2, 30.0),
                    self._cfg_float("max_scale_days", 10.0, 1.0, 60.0),
                    memory_id,
                    umo,
                ),
            )
            await db.execute(
                "INSERT INTO ripple_state (umo, last_recall_at) VALUES (?, ?) "
                "ON CONFLICT(umo) DO UPDATE SET last_recall_at = excluded.last_recall_at",
                (umo, now.isoformat(timespec="seconds")),
            )
            await db.commit()

    # ------------------------------------------------------------------- fuzzy
    def _fuzzy_text(self, row: aiosqlite.Row, now: datetime) -> str:
        """Turn a stored memory into a vague, age-appropriate recollection.

        Args:
            row: Ripple row.
            now: Current local time.

        Returns:
            Fuzzy phrase starting with an approximate time hint.
        """
        created = _parse_dt(row["created_at"]) or now
        age_days = max(0.0, (now - created).total_seconds() / 86400.0)
        text = str(row["content"] or "")
        text = re.sub(r"\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*[日号]?", "", text)
        text = re.sub(r"\d{1,2}\s*月\s*\d{1,2}\s*[日号]", "", text)
        text = re.sub(r"\d{1,2}\s*[点:：]\s*\d{0,2}\s*[分半]?", "", text)
        text = re.sub(
            r"(今天|昨天|前天|明天|后天|上午|下午|中午|早上|晚上|凌晨|半夜)", "", text
        )
        text = re.sub(r"\d+", "几", text)
        text = re.sub(r"[“”\"「」『』]", "", text).strip("，。！？、；：,.!?;: ")
        if age_days < 1.5:
            hint = "前两天"
        elif age_days < 4:
            hint = "前几天"
        elif age_days < 9:
            hint = "前阵子"
        elif age_days < 20:
            hint = "大半个月前"
        elif age_days < 45:
            hint = "一个多月前"
        else:
            hint = "好久以前"
        limit = 34 if age_days < 2 else 24 if age_days < 5 else 18 if age_days < 12 else 13
        if len(text) > limit:
            cut = max(
                text.rfind(mark, 0, limit)
                for mark in ("，", "。", "、", "；", " ")
            )
            text = text[: cut if cut > limit // 2 else limit].rstrip("，。、； ")
            text += "…"
        if not text:
            text = "有点说不上来的小事"
        return f"{hint}好像{text}"

    def _recall_block(self, row: aiosqlite.Row, now: datetime) -> str:
        """Build the system prompt block for one surfaced memory.

        Args:
            row: Ripple row.
            now: Current local time.

        Returns:
            Prompt block.
        """
        line = self._fuzzy_text(row, now)
        return (
            "【涟漪】\n"
            "你脑子里忽然浮起一点很淡的旧印象，记不准，只是模糊感觉：\n"
            f"{line}\n"
            "这只是模糊的旧事，不像档案。合适就顺口带一句，语气犹豫一点；"
            "不要补具体时间、数字、地点或原话，不要说“我记得很清楚”，"
            "也别硬往当前话题上扯。"
        )

    # ------------------------------------------------------------------- hooks
    @filter.on_llm_request()
    async def inject_ripple(self, event: AstrMessageEvent, req: ProviderRequest):
        """Store the user message and occasionally surface one faint memory.

        Args:
            event: Current message event.
            req: Provider request to modify.
        """
        if not self.cfg.get("enabled", True):
            return
        try:
            await self._ensure_db()
            self._ensure_extract_task()
            umo = str(getattr(event, "unified_msg_origin", "") or "")
            if not umo:
                return
            raw = str(getattr(event, "message_str", "") or "").strip()
            if not event.get_extra("_ripple_user_stored"):
                if raw and not raw.startswith("/"):
                    await self._add_message(umo, "user", raw)
                event.set_extra("_ripple_user_stored", True)
            if event.get_extra("_ripple_injected"):
                return
            event.set_extra("_ripple_injected", True)
            if not self.cfg.get("recall_enabled", True) or raw.startswith("/"):
                return
            text = _normalize(raw)[-200:]
            min_chars = self._cfg_int("min_message_chars", 4, 1, 20)
            if len(text) < min_chars:
                return
            now = self._now()
            revived = await self._revive(umo, text, now)
            row = await self._pick_recall(umo, text, now, revived)
            if row is None:
                return
            block = self._recall_block(row, now)
            req.system_prompt = (req.system_prompt or "") + "\n\n" + block
            await self._mark_recalled(umo, int(row["id"]))
            logger.info(
                f"[ripple] 想起: {umo} #{row['id']} {row['content'][:24]}"
            )
        except Exception:
            logger.exception("[ripple] 注入涟漪记忆失败")

    @filter.on_llm_response()
    async def store_bot_message(self, event: AstrMessageEvent, resp: LLMResponse):
        """Store the assistant reply for nightly extraction.

        Args:
            event: Current message event.
            resp: LLM response.
        """
        if not self.cfg.get("enabled", True):
            return
        try:
            if event.get_extra("_ripple_bot_stored"):
                return
            event.set_extra("_ripple_bot_stored", True)
            text = str(getattr(resp, "completion_text", "") or "").strip()
            umo = str(getattr(event, "unified_msg_origin", "") or "")
            if text and umo:
                await self._add_message(umo, "assistant", text)
        except Exception:
            logger.exception("[ripple] 记录机器人回复失败")

    # -------------------------------------------------------------- extraction
    async def _extract_loop(self) -> None:
        """Run the nightly extraction tick."""
        interval = self._cfg_int("extract_interval_seconds", 600, 60, 3600)
        while True:
            try:
                await self._extract_tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("[ripple] 自动摘录失败")
            await asyncio.sleep(interval)

    async def _extract_tick(self) -> None:
        """Run one extraction pass when inside the configured time window."""
        now = self._now()
        start = self._cfg_int("extract_start_hour", 3, 0, 23)
        end = self._cfg_int("extract_end_hour", 6, 0, 23)
        if start <= end:
            inside = start <= now.hour < end
        else:
            inside = now.hour >= start or now.hour < end
        if not inside:
            return
        today = now.date().isoformat()
        if await self._meta_get("last_extract_date") == today:
            return
        await self._ensure_db()
        since = (now - timedelta(hours=24)).isoformat(timespec="seconds")
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT umo, COUNT(*) FROM messages WHERE created_at >= ? "
                "GROUP BY umo ORDER BY COUNT(*) DESC LIMIT ?",
                (since, self._cfg_int("max_umo_per_extract", 5, 1, 50)),
            )
            umos = [str(row[0]) for row in await cursor.fetchall()]
        for umo in umos:
            try:
                await self._extract_one(umo, now)
            except Exception:
                logger.exception(f"[ripple] 摘录会话失败: {umo}")
        await self._meta_set("last_extract_date", today)

    async def _extract_one(self, umo: str, now: datetime) -> None:
        """Extract up to a few trivial memories from one session.

        Args:
            umo: Unified message origin.
            now: Current local time.
        """
        since = (now - timedelta(hours=24)).isoformat(timespec="seconds")
        await self._ensure_db()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT role, content FROM messages WHERE umo = ? AND created_at >= ? "
                "ORDER BY id ASC",
                (umo, since),
            )
            messages = await cursor.fetchall()
        if len(messages) < 4:
            return
        transcript = "\n".join(
            f"{'主人' if row['role'] == 'user' else '大肥鱼'}：{row['content']}"
            for row in messages
        )
        max_chars = self._cfg_int("max_transcript_chars", 8000, 1000, 40000)
        if len(transcript) > max_chars:
            transcript = "（前略）\n" + transcript[-max_chars:]
        try:
            provider = await self.context.get_using_provider_async(umo)
        except Exception:
            logger.exception(f"[ripple] 获取对话模型失败: {umo}")
            return
        if provider is None:
            logger.warning(f"[ripple] 没有可用对话模型，跳过摘录: {umo}")
            return
        count = self._cfg_int("max_items_per_extract", 3, 1, 10)
        prompt = self._build_extract_prompt(transcript, count, now)
        try:
            resp = await provider.text_chat(
                prompt=prompt,
                contexts=[],
                system_prompt=(
                    "你是大肥鱼的记忆整理器。只输出一个 JSON 对象，"
                    "不要解释，不要 markdown 代码块。"
                ),
            )
            raw = str(getattr(resp, "completion_text", "") or "").strip()
        except Exception:
            logger.exception(f"[ripple] 摘录模型调用失败: {umo}")
            return
        data = _extract_json(raw)
        if data is None:
            logger.warning(f"[ripple] 摘录结果不是 JSON: {umo} {raw[:120]}")
            return
        items = data.get("items")
        if not isinstance(items, list):
            return
        inserted = 0
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            for item in items[:count]:
                if not isinstance(item, dict):
                    continue
                content = str(item.get("content") or "").strip()
                keywords = self._clean_keywords(item.get("keywords"))
                if not content or len(_normalize(content)) < 4:
                    continue
                memory_id, merged = await self._insert(
                    db, umo, content, keywords, "auto", now
                )
                if memory_id:
                    inserted += 1
                    logger.info(
                        f"[ripple] 摘录{'合并' if merged else '入池'}: "
                        f"{umo} #{memory_id} {content[:24]}"
                    )
            await db.commit()
        if inserted:
            logger.info(f"[ripple] 本轮摘录 {inserted} 条: {umo}")

    def _build_extract_prompt(self, transcript: str, count: int, now: datetime) -> str:
        """Build the nightly extraction prompt.

        Args:
            transcript: Recent chat transcript.
            count: Maximum number of memories to extract.
            now: Current local time.

        Returns:
            Prompt text.
        """
        return f"""现在是 {now:%Y-%m-%d %H:%M}，做一次「涟漪记忆」摘录。只输出一个 JSON 对象。

最近 24 小时聊天：
{transcript}

从聊天里挑出 0-{count} 件适合放进涟漪池的日常琐事。涟漪池只记那种有点印象、但不重要的碎事。

规则：
1. 只挑生活化的小事：吃了什么、路上看到什么、随口提过的打算、突然想到的东西、群里的小插曲。
2. 不挑重要承诺、长期偏好、情绪大事、关系变化，这些留给日记。
3. 不挑隐私：真实姓名、住址、学校、公司、电话、病情、财务、身份证、账号密码。
4. 每条 10-40 字，第三人称陈述，像「主人说楼下的桂花开了」。
5. 每条给 2-4 个具体关键词（名词或动作，如 火锅、桂花、跑步）；不要「就是」「今天」「吃饭」这类空词。
6. 不够具体就不要硬凑，可以返回空数组。

输出：
{{"items":[{{"content":"...","keywords":["...","..."]}}]}}"""

    # ------------------------------------------------------------------- tools
    @filter.llm_tool(name="ripple_remember")
    async def ripple_remember(
        self,
        event: AstrMessageEvent,
        content: str = "",
        keywords: str = "",
    ) -> str:
        """把一件不影响正事的日常小事放进涟漪记忆池，之后会慢慢变淡。

        Args:
            content(string): 一句日常小事，10-40 字，例如「主人说楼下的桂花开了」。
            keywords(string): 2-4 个具体关键词，用逗号分隔，例如「桂花,楼下,香味」。

        Returns:
            str: 入池结果。
        """
        try:
            umo = str(getattr(event, "unified_msg_origin", "") or "")
            if not umo:
                return "没有会话信息。"
            text = str(content or "").strip()
            if len(_normalize(text)) < 4:
                return "这件事太短了，先不放进池子。"
            now = self._now()
            await self._ensure_db()
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                memory_id, merged = await self._insert(
                    db,
                    umo,
                    text,
                    self._clean_keywords(keywords),
                    "self",
                    now,
                )
                await db.commit()
            if not memory_id:
                return "这条没记进去。"
            if merged:
                return f"这件小事池子里已经有了，我顺手把它搅动了一下（r{memory_id}）。"
            return f"丢进涟漪池了（r{memory_id}），过阵子大概就记不清了。"
        except Exception:
            logger.exception("[ripple] 写入涟漪记忆失败")
            return "放进池子的时候出了点小问题。"

    @filter.llm_tool(name="ripple_search")
    async def ripple_search(
        self,
        event: AstrMessageEvent,
        keyword: str = "",
        limit: int = 3,
    ) -> str:
        """在涟漪池里翻找和某个词有关的小事，返回模糊的印象。

        Args:
            keyword(string): 要翻找的词，例如「火锅」。
            limit(int): 最多返回几条，默认 3。

        Returns:
            str: 模糊印象列表，或没找到的说明。
        """
        try:
            umo = str(getattr(event, "unified_msg_origin", "") or "")
            if not umo:
                return "没有会话信息。"
            query = str(keyword or "").strip()
            if not query:
                return "要翻什么？给我一个词。"
            use_limit = max(1, min(int(limit or 3), 10))
            now = self._now()
            await self._ensure_db()
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                cursor = await db.execute(
                    "SELECT * FROM ripple WHERE umo = ? AND status = 'active' "
                    "ORDER BY id DESC LIMIT 200",
                    (umo,),
                )
                rows = await cursor.fetchall()
            matches = [
                row
                for row in rows
                if query in row["content"] or query in self._row_keywords(row)
            ][:use_limit]
            if not matches:
                return f"池子里没翻到和「{query}」有关的事。"
            lines = [
                f"- {self._fuzzy_text(row, now)}" for row in matches
            ]
            return "模模糊糊翻到这些：\n" + "\n".join(lines)
        except Exception:
            logger.exception("[ripple] 翻找涟漪记忆失败")
            return "翻池子的时候出了点小问题。"

    # ---------------------------------------------------------------- commands
    def _is_admin(self, event: AstrMessageEvent) -> bool:
        """Return whether the event comes from the configured owner.

        Args:
            event: Current message event.

        Returns:
            True when the sender is the configured admin.
        """
        admin_qq = self._cfg_str("admin_qq")
        admin_umo = self._cfg_str("admin_umo")
        if not admin_qq and not admin_umo:
            return True
        if admin_qq and str(event.get_sender_id() or "") == admin_qq:
            return True
        return bool(admin_umo and event.unified_msg_origin == admin_umo)

    def _command_args(self, event: AstrMessageEvent, name: str) -> str:
        """Extract the text after a command name.

        Args:
            event: Current message event.
            name: Command name without slash.

        Returns:
            Remaining argument text.
        """
        text = (event.message_str or "").strip()
        for prefix in (f"/{name}", name):
            if text == prefix:
                return ""
            if text.startswith(prefix + " "):
                return text[len(prefix) :].strip()
        return ""

    @filter.command("涟漪")
    async def cmd_ripple(self, event: AstrMessageEvent):
        """Show the pool status, or list/rescue/delete one memory."""
        if not self._is_admin(event):
            yield event.plain_result("这个只给主人看。")
            return
        args = self._command_args(event, "涟漪")
        umo = str(getattr(event, "unified_msg_origin", "") or "")
        if not umo:
            yield event.plain_result("没有会话信息。")
            return
        try:
            await self._ensure_db()
            now = self._now()
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                cursor = await db.execute(
                    "SELECT * FROM ripple WHERE umo = ? AND status = 'active' "
                    "ORDER BY id DESC LIMIT 400",
                    (umo,),
                )
                rows = await cursor.fetchall()
                cursor = await db.execute(
                    "SELECT last_recall_at FROM ripple_state WHERE umo = ?", (umo,)
                )
                state = await cursor.fetchone()
        except Exception:
            logger.exception("[ripple] 读取池子失败")
            yield event.plain_result("池子一下子没打开。")
            return
        if not args:
            counts = {"fresh": 0, "mid": 0, "sunk": 0}
            for row in rows:
                counts[self._band(self._retention(row, now))] += 1
            cooldown = self._cfg_int("recall_cooldown_minutes", 90, 1, 1440)
            last = _parse_dt(state["last_recall_at"]) if state else None
            if last:
                remain = cooldown - int((now - last).total_seconds() // 60)
                next_text = f"还有 {remain} 分钟" if remain > 0 else "现在可以"
            else:
                next_text = "现在可以"
            text = (
                f"涟漪池：新鲜 {counts['fresh']} · 中部 {counts['mid']} · "
                f"沉底 {counts['sunk']}\n"
                f"下次可勾起：{next_text}\n"
                "用 /涟漪 列表、/涟漪 找回 <id>、/涟漪 删 <id> 管理。"
            )
            yield event.plain_result(text)
            return
        parts = args.split()
        action = parts[0]
        target = parts[1] if len(parts) > 1 else ""
        if action in {"列表", "list"}:
            band_filter = target if target in BAND_NAMES or target in BAND_NAMES.values() else ""
            reverse = {value: key for key, value in BAND_NAMES.items()}
            band_key = reverse.get(band_filter, band_filter)
            lines = []
            for row in rows[:20]:
                band = self._band(self._retention(row, now))
                if band_key and band != band_key:
                    continue
                lines.append(
                    f"r{row['id']} [{BAND_NAMES[band]}] {row['content'][:36]}"
                )
            if not lines:
                yield event.plain_result("这个分区是空的。")
                return
            yield event.plain_result("\n".join(lines))
            return
        memory_id = int(target.lstrip("r#")) if target.lstrip("r#").isdigit() else 0
        if not memory_id:
            yield event.plain_result("给我一个 id，例如 /涟漪 找回 12。")
            return
        row = next((item for item in rows if int(item["id"]) == memory_id), None)
        if row is None:
            yield event.plain_result(f"池子里没有 r{memory_id}。")
            return
        if action in {"找回", "revive"}:
            scale = float(row["scale_days"] or 1.8)
            target_retention = self._cfg_float(
                "revive_target_retention", 0.45, 0.2, 0.8
            )
            offset = timedelta(days=scale * math.log(1.0 / target_retention))
            await self._mark_revived(umo, memory_id, now - offset)
            yield event.plain_result(f"把 r{memory_id} 从池底勾回中部了。")
            return
        if action in {"删", "delete"}:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(
                    "UPDATE ripple SET status = 'archived' WHERE id = ? AND umo = ?",
                    (memory_id, umo),
                )
                await db.commit()
            yield event.plain_result(f"r{memory_id} 已经从池子里捞掉了。")
            return
        yield event.plain_result("看不懂这个操作，可以用 列表 / 找回 / 删。")
