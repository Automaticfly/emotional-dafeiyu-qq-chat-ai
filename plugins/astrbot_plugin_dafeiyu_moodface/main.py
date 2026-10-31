"""Add mood-aware native QQ faces to a portion of LLM replies."""

from __future__ import annotations

import random
import re
import time
from pathlib import Path

import aiosqlite

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Face, Plain
from astrbot.api.star import Context, Star, StarTools

PLUGIN_NAME = "astrbot_plugin_dafeiyu_moodface"

# Mood keywords are checked in order; the first match wins.
MOOD_FACES: list[tuple[tuple[str, ...], tuple[int, ...]]] = [
    (
        ("开心", "高兴", "快乐", "愉快", "幸福", "喜欢", "爱", "甜", "兴奋", "期待"),
        (13, 21, 20, 14, 28),
    ),
    (("害羞", "脸红", "心动"), (6, 21, 52)),
    (
        ("难过", "伤心", "低落", "失落", "委屈", "哭", "郁闷"),
        (50, 15, 49, 5),
    ),
    (("生气", "愤怒", "发怒", "吃醋", "不满", "烦"), (11, 2, 15, 44)),
    (("担心", "不安", "焦虑", "害怕", "紧张"), (26, 15, 27)),
    (("困", "累", "疲惫", "想睡"), (8, 47, 25)),
    (("惊讶", "震惊", "意外"), (1, 32)),
    (("调皮", "坏笑", "得意", "傲娇"), (12, 44, 4)),
    (("想念", "思念", "惦记"), (66, 52, 14)),
    (("平静", "安心", "放松"), (14, 21, 140)),
]
DEFAULT_FACES = (14, 21, 13)


class DafeiyuMoodFacePlugin(Star):
    """Append one native QQ face to some replies based on the heart mood."""

    def __init__(self, context: Context, config: dict | None = None):
        super().__init__(context)
        self.context = context
        self.cfg = config or {}
        self._mood_cache: dict[str, tuple[float, str]] = {}

    async def _get_mood(self, event: AstrMessageEvent) -> str:
        """Read the current mood for the event session, with a short cache."""
        umo = str(self.cfg.get("mood_umo", "") or event.unified_msg_origin)
        now = time.monotonic()
        cached = self._mood_cache.get(umo)
        if cached and now - cached[0] < float(self.cfg.get("mood_cache_seconds", 60)):
            return cached[1]
        plugin_name = str(
            self.cfg.get("heart_plugin", "astrbot_plugin_dafeiyu_heart")
        )
        db_path = Path(StarTools.get_data_dir(plugin_name)) / "heart.db"
        mood = ""
        if db_path.is_file():
            try:
                async with aiosqlite.connect(db_path) as db:
                    db.row_factory = aiosqlite.Row
                    row = await (
                        await db.execute(
                            "SELECT dynamic_mood, dynamic_reason, baseline_mood "
                            "FROM heart_state WHERE umo = ?",
                            (umo,),
                        )
                    ).fetchone()
                    if row:
                        parts = [
                            str(row[key] or "").strip()
                            for key in ("dynamic_mood", "baseline_mood")
                        ]
                        mood = "；".join(part for part in parts if part)
            except Exception:
                logger.exception("Failed to read mood for mood face")
        self._mood_cache[umo] = (now, mood)
        return mood

    def _face_for_mood(self, mood: str) -> int:
        """Choose a QQ face that reflects the current mood."""
        for keywords, faces in MOOD_FACES:
            if any(keyword in mood for keyword in keywords):
                return random.choice(faces)
        return random.choice(DEFAULT_FACES)

    @filter.on_decorating_result()
    async def add_mood_face(self, event: AstrMessageEvent):
        """Sometimes append a native QQ face to an LLM text reply."""
        if not bool(self.cfg.get("enabled", True)):
            return
        result = event.get_result()
        if result is None or not result.is_llm_result():
            return
        chain = list(result.chain or [])
        if not chain:
            return
        if any(isinstance(component, Face) for component in chain):
            return
        text_parts = [
            component.text
            for component in chain
            if isinstance(component, Plain) and component.text
        ]
        if not text_parts:
            return
        text = "".join(text_parts)
        # Leave explicit bracket faces to face_convert to avoid double faces.
        if re.search(r"\[[^\]\n]{1,8}\]", text):
            return
        probability = float(self.cfg.get("face_probability", 0.5) or 0.5)
        if random.random() >= max(0.0, min(1.0, probability)):
            return
        mood = await self._get_mood(event)
        face_id = self._face_for_mood(mood)
        face = Face(id=face_id)
        inline_probability = float(
            self.cfg.get("inline_probability", 0.5) or 0.5
        )
        inline = random.random() < max(0.0, min(1.0, inline_probability))
        if inline:
            object.__setattr__(face, "_inline_face", True)
        chain.append(face)
        result.chain = chain
        logger.info(
            "Added mood face id=%s mood=%s mode=%s umo=%s",
            face_id,
            mood or "unknown",
            "inline" if inline else "separate",
            event.unified_msg_origin,
        )
