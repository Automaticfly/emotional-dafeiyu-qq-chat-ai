"""大肥鱼心脏模块：主观认知、心绪手札、心情底色、动态心情。"""

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

SCHEMA = """
CREATE TABLE IF NOT EXISTS heart_state (
    umo TEXT PRIMARY KEY,
    relationship TEXT,
    relationship_updated_at TEXT,
    baseline_mood TEXT,
    baseline_reason TEXT,
    baseline_started_at TEXT,
    dynamic_mood TEXT,
    dynamic_reason TEXT,
    dynamic_started_at TEXT,
    last_settled_date TEXT,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS mood_journal (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    umo TEXT NOT NULL,
    text TEXT NOT NULL,
    tag TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    umo TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_umo_time ON messages(umo, created_at);
CREATE INDEX IF NOT EXISTS idx_journal_umo_exp ON mood_journal(umo, expires_at);
"""


class DafeiyuHeartPlugin(Star):
    def __init__(self, context: Context, config: dict | None = None):
        super().__init__(context)
        self.context = context
        self.cfg = config or {}
        self.data_dir = Path(StarTools.get_data_dir("astrbot_plugin_dafeiyu_heart"))
        self.db_path = self.data_dir / "heart.db"
        self._db_ready = False
        self._db_lock = asyncio.Lock()
        self._settle_task: asyncio.Task | None = None

    # ------------------------------------------------------------------ setup
    async def _ensure_db(self) -> None:
        if self._db_ready:
            return
        async with self._db_lock:
            if self._db_ready:
                return
            async with aiosqlite.connect(self.db_path) as db:
                await db.executescript(SCHEMA)
                await db.commit()
            self._db_ready = True

    def _ensure_settle_task(self) -> None:
        if not self.cfg.get("enabled", True):
            return
        if self._settle_task is None or self._settle_task.done():
            try:
                self._settle_task = asyncio.create_task(self._settle_loop())
            except RuntimeError:
                pass

    @filter.on_astrbot_loaded()
    async def _on_loaded(self):
        await self._ensure_db()
        self._ensure_settle_task()

    async def terminate(self):
        if self._settle_task and not self._settle_task.done():
            self._settle_task.cancel()
            try:
                await self._settle_task
            except asyncio.CancelledError:
                pass

    # ------------------------------------------------------------- chat hooks
    @filter.on_llm_request()
    async def inject_heart_context(self, event: AstrMessageEvent, req: ProviderRequest):
        if not self.cfg.get("enabled", True):
            return
        await self._ensure_db()
        self._ensure_settle_task()
        umo = event.unified_msg_origin

        if not event.get_extra("_heart_user_stored"):
            text = (event.message_str or "").strip()
            if text and not text.startswith("/"):
                await self._add_message(umo, "user", text)
            event.set_extra("_heart_user_stored", True)

        if event.get_extra("_heart_injected"):
            return
        context_text = await self._build_heart_context(umo)
        if context_text:
            req.system_prompt = (req.system_prompt or "") + "\n\n" + context_text
        event.set_extra("_heart_injected", True)

    @filter.on_llm_response()
    async def store_bot_message(self, event: AstrMessageEvent, resp: LLMResponse):
        if not self.cfg.get("enabled", True):
            return
        if event.get_extra("_heart_bot_stored"):
            return
        text = (resp.completion_text or "").strip()
        if text:
            await self._add_message(event.unified_msg_origin, "assistant", text)
        event.set_extra("_heart_bot_stored", True)

    async def _add_message(self, umo: str, role: str, content: str) -> None:
        content = content.strip()
        if not content:
            return
        if len(content) > 4000:
            content = content[-4000:]
        now = datetime.now().isoformat(timespec="seconds")
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO messages (umo, role, content, created_at) VALUES (?, ?, ?, ?)",
                (umo, role, content, now),
            )
            await db.commit()

    async def _build_heart_context(self, umo: str) -> str:
        now = datetime.now().isoformat(timespec="seconds")
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            row = await (
                await db.execute(
                    "SELECT relationship, baseline_mood, baseline_reason, "
                    "dynamic_mood, dynamic_reason FROM heart_state WHERE umo = ?",
                    (umo,),
                )
            ).fetchone()
            journal = await (
                await db.execute(
                    "SELECT tag, COUNT(*) AS n FROM mood_journal "
                    "WHERE umo = ? AND expires_at > ? GROUP BY tag "
                    "ORDER BY n DESC, tag ASC LIMIT 5",
                    (umo, now),
                )
            ).fetchall()
            recent = await (
                await db.execute(
                    "SELECT text, tag FROM mood_journal "
                    "WHERE umo = ? AND expires_at > ? ORDER BY id DESC LIMIT 2",
                    (umo, now),
                )
            ).fetchall()

        parts = ["【心】"]
        relationship = (row["relationship"] if row else "") or "还在慢慢认识主人"
        parts.append(f"关系：{relationship}")

        if row and row["baseline_mood"]:
            reason = row["baseline_reason"] or ""
            parts.append(f"底色：{row['baseline_mood']}（{reason}）")
        if row and row["dynamic_mood"]:
            reason = row["dynamic_reason"] or ""
            parts.append(f"现在：{row['dynamic_mood']}（{reason}）")
        if recent:
            parts.append(
                "手札：" + "；".join(f"[{r['tag']}] {r['text']}" for r in recent)
            )
        if journal:
            parts.append(
                "情绪：" + "；".join(f"{r['tag']} ×{r['n']}" for r in journal)
            )
        parts.append(
            "心情会影响你想起什么：心情好时更容易想起主人好的地方，"
            "心情糟时同样一句话更容易翻旧账；允许自己会错意，别演。"
        )
        return "\n".join(parts)

    # ----------------------------------------------------------------- tools
    @filter.llm_tool(name="update_relationship_cognition")
    async def update_relationship_cognition(self, event: AstrMessageEvent, text: str):
        """更新你对主人和你们关系的主观认知。只有当你真的觉得关系变了才调用，不要频繁改。

        Args:
            text(string): 新的主观认知，最多 150 字，写你对主人的印象和你们现在的关系。
        """
        text = (text or "").strip()[:150]
        if not text:
            return "没有更新：内容为空。"
        now = datetime.now().isoformat(timespec="seconds")
        await self._ensure_db()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO heart_state (umo, relationship, relationship_updated_at, updated_at) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(umo) DO UPDATE SET relationship = excluded.relationship, "
                "relationship_updated_at = excluded.relationship_updated_at, "
                "updated_at = excluded.updated_at",
                (event.unified_msg_origin, text, now, now),
            )
            await db.commit()
        return "已经悄悄把对主人的认知更新好了。"

    @filter.llm_tool(name="set_dynamic_mood")
    async def set_dynamic_mood(self, event: AstrMessageEvent, mood: str, reason: str):
        """当你现在的心情真的发生变化时调用；不要每轮都调用，也不要为了配合台词硬改。

        Args:
            mood(string): 一个词，如 开心、平静、低落、难过、担心、吃醋、不安。
            reason(string): 最多 40 字，说明为什么现在是这个心情。
        """
        mood = (mood or "").strip()[:20]
        reason = (reason or "").strip()[:40]
        if not mood:
            return "没有更新：心情为空。"
        now = datetime.now().isoformat(timespec="seconds")
        await self._ensure_db()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO heart_state (umo, dynamic_mood, dynamic_reason, dynamic_started_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(umo) DO UPDATE SET dynamic_mood = excluded.dynamic_mood, "
                "dynamic_reason = excluded.dynamic_reason, "
                "dynamic_started_at = excluded.dynamic_started_at, "
                "updated_at = excluded.updated_at",
                (event.unified_msg_origin, mood, reason, now, now),
            )
            await db.commit()
        return f"心情现在是：{mood}。"

    # --------------------------------------------------------------- commands
    @filter.command("心")
    async def heart_status(self, event: AstrMessageEvent):
        """查看当前关系、心情和手札。"""
        await self._ensure_db()
        now = datetime.now().isoformat(timespec="seconds")
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            row = await (
                await db.execute(
                    "SELECT * FROM heart_state WHERE umo = ?",
                    (event.unified_msg_origin,),
                )
            ).fetchone()
            journal = await (
                await db.execute(
                    "SELECT text, tag, created_at FROM mood_journal "
                    "WHERE umo = ? AND expires_at > ? ORDER BY id DESC LIMIT 6",
                    (event.unified_msg_origin, now),
                )
            ).fetchall()

        lines = ["【大肥鱼的心】"]
        lines.append("关系：" + ((row["relationship"] if row else "") or "还在慢慢认识主人"))
        if row and row["baseline_mood"]:
            lines.append(f"底色：{row['baseline_mood']}（{row['baseline_reason'] or ''}）")
        if row and row["dynamic_mood"]:
            lines.append(f"现在：{row['dynamic_mood']}（{row['dynamic_reason'] or ''}）")
        if row and row["last_settled_date"]:
            lines.append(f"最近结算：{row['last_settled_date']}")
        if journal:
            lines.append("手札：")
            for item in journal:
                lines.append(f"· [{item['tag']}] {item['text']}")
        else:
            lines.append("手札：这三天没有留下什么。")
        yield event.plain_result("\n".join(lines))

    @filter.command("手札")
    async def journal_status(self, event: AstrMessageEvent):
        """查看三天内的心绪手札。"""
        await self._ensure_db()
        now = datetime.now().isoformat(timespec="seconds")
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            rows = await (
                await db.execute(
                    "SELECT text, tag, created_at FROM mood_journal "
                    "WHERE umo = ? AND expires_at > ? ORDER BY id DESC LIMIT 20",
                    (event.unified_msg_origin, now),
                )
            ).fetchall()
        if not rows:
            yield event.plain_result("这三天的手札是空的。")
            return
        lines = ["【三天内的心绪手札】"]
        for item in rows:
            lines.append(f"· [{item['tag']}] {item['text']}")
        yield event.plain_result("\n".join(lines))

    # ------------------------------------------------------------- settlement
    async def _settle_loop(self) -> None:
        while True:
            try:
                if self.cfg.get("enabled", True):
                    now = datetime.now()
                    start = int(self.cfg.get("settle_start_hour", 4))
                    end = int(self.cfg.get("settle_end_hour", 8))
                    if start <= now.hour < end:
                        await self._settle_due()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("[dafeiyu_heart] 结算循环异常")
            await asyncio.sleep(600)

    async def _settle_due(self) -> None:
        await self._ensure_db()
        today = datetime.now().date().isoformat()
        main_umo = str(self.cfg.get("main_umo") or "").strip()
        if main_umo:
            umos = [main_umo]
        else:
            since = (datetime.now() - timedelta(hours=24)).isoformat(timespec="seconds")
            async with aiosqlite.connect(self.db_path) as db:
                rows = await (
                    await db.execute(
                        "SELECT DISTINCT umo FROM messages WHERE created_at >= ?",
                        (since,),
                    )
                ).fetchall()
            umos = [r[0] for r in rows]

        for umo in umos:
            try:
                async with aiosqlite.connect(self.db_path) as db:
                    db.row_factory = aiosqlite.Row
                    state = await (
                        await db.execute(
                            "SELECT last_settled_date FROM heart_state WHERE umo = ?",
                            (umo,),
                        )
                    ).fetchone()
                if state and state["last_settled_date"] == today:
                    continue
                await self._settle_one(umo, today)
            except Exception:
                logger.exception(f"[dafeiyu_heart] 结算失败: {umo}")

    async def _settle_one(self, umo: str, today: str) -> None:
        since = (datetime.now() - timedelta(hours=24)).isoformat(timespec="seconds")
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            rows = await (
                await db.execute(
                    "SELECT role, content FROM messages WHERE umo = ? AND created_at >= ? "
                    "ORDER BY id ASC",
                    (umo, since),
                )
            ).fetchall()
        if not rows:
            return

        lines = []
        for row in rows:
            name = "主人" if row["role"] == "user" else "大肥鱼"
            lines.append(f"{name}：{row['content']}")
        transcript = "\n".join(lines)
        max_chars = int(self.cfg.get("max_transcript_chars", 12000))
        if len(transcript) > max_chars:
            transcript = transcript[-max_chars:]

        provider = await self.context.get_using_provider_async(umo)
        if provider is None:
            logger.warning(f"[dafeiyu_heart] 没有可用的对话模型，跳过结算: {umo}")
            return

        prompt = f"""请回顾下面这段聊天，做一次凌晨心情结算。只输出一个 JSON 对象，不要解释，不要 markdown 代码块。

聊天记录：
{transcript}

请输出：
{{
  "journal_text": "今天最硌在心里的一件小事，最多40字",
  "journal_tag": "从 被冷落/被夸/不安/吃醋/开心/担心/平静 里选一个",
  "baseline_mood": "明天的心情底色，一个词",
  "baseline_reason": "为什么是这个底色，最多60字",
  "relationship_update": "如果这几天你对主人的印象或关系真的变了，写一句最多150字的新认知；没变就填 null"
}}
"""
        system_prompt = (
            "你是「大肥鱼」，一只温柔、活泼、爱笑的蓝色鲸鱼娘。"
            "现在是凌晨心情结算，你要诚实地看看自己和主人这几天的相处。"
        )
        try:
            resp = await provider.text_chat(
                prompt=prompt,
                contexts=[],
                system_prompt=system_prompt,
            )
            text = (resp.completion_text or "").strip()
        except Exception:
            logger.exception(f"[dafeiyu_heart] 结算模型调用失败: {umo}")
            return

        match = re.search(r"\{.*\}", text, re.S)
        if not match:
            logger.warning(f"[dafeiyu_heart] 结算没有返回 JSON: {text[:200]}")
            return
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            logger.warning(f"[dafeiyu_heart] 结算 JSON 解析失败: {text[:200]}")
            return

        journal_text = str(data.get("journal_text") or "").strip()[:40]
        journal_tag = str(data.get("journal_tag") or "平静").strip()[:10]
        baseline_mood = str(data.get("baseline_mood") or "平静").strip()[:20]
        baseline_reason = str(data.get("baseline_reason") or "").strip()[:60]
        relationship_update = data.get("relationship_update")
        if relationship_update is not None:
            relationship_update = str(relationship_update).strip()[:150]
            if relationship_update.lower() == "null":
                relationship_update = ""

        now = datetime.now().isoformat(timespec="seconds")
        expires = (datetime.now() + timedelta(days=3)).isoformat(timespec="seconds")
        async with aiosqlite.connect(self.db_path) as db:
            if journal_text:
                await db.execute(
                    "INSERT INTO mood_journal (umo, text, tag, created_at, expires_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (umo, journal_text, journal_tag, now, expires),
                )
            await db.execute(
                "INSERT INTO heart_state (umo, baseline_mood, baseline_reason, "
                "baseline_started_at, dynamic_mood, dynamic_reason, dynamic_started_at, "
                "last_settled_date, updated_at) VALUES (?, ?, ?, ?, NULL, NULL, NULL, ?, ?) "
                "ON CONFLICT(umo) DO UPDATE SET baseline_mood = excluded.baseline_mood, "
                "baseline_reason = excluded.baseline_reason, "
                "baseline_started_at = excluded.baseline_started_at, "
                "dynamic_mood = NULL, dynamic_reason = NULL, dynamic_started_at = NULL, "
                "last_settled_date = excluded.last_settled_date, updated_at = excluded.updated_at",
                (umo, baseline_mood, baseline_reason, now, today, now),
            )
            if relationship_update:
                await db.execute(
                    "UPDATE heart_state SET relationship = ?, relationship_updated_at = ? "
                    "WHERE umo = ?",
                    (relationship_update, now, umo),
                )
            await db.execute(
                "DELETE FROM mood_journal WHERE expires_at < ?", (now,)
            )
            await db.commit()

        logger.info(
            f"[dafeiyu_heart] 结算完成: {umo} journal={journal_text!r} "
            f"mood={baseline_mood!r} relationship_updated={bool(relationship_update)}"
        )
