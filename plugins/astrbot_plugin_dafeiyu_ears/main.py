"""大肥鱼耳朵：本地音乐库、点歌、听歌和分享歌曲。

默认只读本地 music 目录，最稳、不依赖第三方接口。
配置 meting_api 后可以可选地在线搜索和分享。
"""

from __future__ import annotations

import random
import re
import time
from pathlib import Path
from urllib.parse import quote

import aiohttp

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.message_components import File, Music, Plain, Record
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import Context, Star, StarTools

PLUGIN_NAME = "astrbot_plugin_dafeiyu_ears"

AUDIO_EXTENSIONS = {".mp3", ".flac", ".m4a", ".wav", ".ogg", ".aac", ".opus"}


def _normalize(text: object) -> str:
    """Normalize a song name for matching."""
    return re.sub(r"[\s\-_—·.]+", "", str(text or "").lower())


class DafeiyuEarsPlugin(Star):
    """本地音乐点播和分享。"""

    def __init__(self, context: Context, config: dict | None = None):
        super().__init__(context, config)
        self.cfg = config or {}
        self.plugin_dir = Path(StarTools.get_data_dir(PLUGIN_NAME))
        self.music_dir = self.plugin_dir / str(self.cfg.get("music_dir", "music"))
        self.music_dir.mkdir(parents=True, exist_ok=True)
        self._cache: list[Path] = []
        self._cache_at = 0.0
        self._last_play: dict[str, float] = {}
        self._online_cache: dict[str, tuple[float, list[dict]]] = {}

    # ------------------------------------------------------------------ access
    def _is_admin(self, event: AstrMessageEvent) -> bool:
        """Return whether the sender may use music commands."""
        if not bool(self.cfg.get("enabled", True)):
            return False
        admin_qq = str(self.cfg.get("admin_qq") or "").strip()
        admin_umo = str(self.cfg.get("admin_umo") or "").strip()
        if admin_qq and str(event.get_sender_id() or "") == admin_qq:
            return True
        if admin_umo and event.unified_msg_origin == admin_umo:
            return True
        if admin_qq or admin_umo:
            return False
        if event.get_group_id() and not bool(self.cfg.get("allow_groups", False)):
            return False
        return True

    # ------------------------------------------------------------------ library
    def _scan(self) -> list[Path]:
        """Scan the local music directory, cached for one minute."""
        now = time.monotonic()
        if self._cache and now - self._cache_at < 60:
            return self._cache
        files: list[Path] = []
        try:
            for path in self.music_dir.rglob("*"):
                if path.is_file() and path.suffix.lower() in AUDIO_EXTENSIONS:
                    files.append(path)
        except OSError:
            files = []
        files.sort(key=lambda item: item.name.lower())
        self._cache = files
        self._cache_at = now
        return files

    def _search_local(self, keyword: str) -> list[Path]:
        """Search local files by filename substring."""
        target = _normalize(keyword)
        if not target:
            return []
        return [
            path
            for path in self._scan()
            if target in _normalize(path.stem)
        ]

    def _cooldown_left(self, umo: str) -> int:
        """Return remaining cooldown seconds for a session."""
        try:
            cooldown = int(self.cfg.get("cooldown_seconds", 10) or 10)
        except (TypeError, ValueError):
            cooldown = 10
        last = self._last_play.get(umo, 0.0)
        return max(0, int(cooldown - (time.monotonic() - last)))

    def _mark_play(self, umo: str) -> None:
        """Record a successful play."""
        self._last_play[umo] = time.monotonic()

    # ------------------------------------------------------------------ online
    @property
    def meting_api(self) -> str:
        """Return the configured Meting API base URL."""
        return str(self.cfg.get("meting_api") or "").strip()

    async def _online_search(self, keyword: str) -> list[dict]:
        """Search songs through an optional Meting API.

        Args:
            keyword: Song or artist keyword.

        Returns:
            List of dicts with name, artist, url and pic.
        """
        api = self.meting_api
        if not api or not keyword:
            return []
        cached = self._online_cache.get(keyword)
        if cached and time.monotonic() - cached[0] < 300:
            return cached[1]
        separator = "&" if "?" in api else "?"
        url = f"{api}{separator}type=search&id={quote(keyword)}"
        try:
            timeout = aiohttp.ClientTimeout(total=12)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        return []
                    data = await resp.json(content_type=None)
        except Exception:
            logger.warning("[ears] 在线搜索失败", exc_info=True)
            return []
        items = data if isinstance(data, list) else (
            data.get("data", []) if isinstance(data, dict) else []
        )
        if not isinstance(items, list):
            return []
        results: list[dict] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            audio = str(item.get("url") or "")
            name = str(item.get("name") or "").strip()
            if not audio.startswith(("http://", "https://")) or not name:
                continue
            results.append(
                {
                    "name": name,
                    "artist": str(item.get("artist") or "").strip(),
                    "url": audio,
                    "pic": str(item.get("pic") or "").strip(),
                }
            )
        self._online_cache[keyword] = (time.monotonic(), results)
        return results

    # ------------------------------------------------------------------ sending
    def _local_chain(self, path: Path, caption: str = "") -> list:
        """Build a message chain for a local song."""
        size_mb = path.stat().st_size / (1024 * 1024)
        limit = float(self.cfg.get("max_file_mb", 30) or 30)
        if size_mb > limit:
            raise ValueError(f"这首歌有 {size_mb:.1f}MB，超过发送上限 {limit:g}MB。")
        mode = str(self.cfg.get("send_mode", "file") or "file").lower()
        chain: list = []
        text = caption or f"《{path.stem}》"
        chain.append(Plain(text))
        if mode == "record":
            chain.append(Record.fromFileSystem(str(path)))
        else:
            chain.append(File(name=path.name, file=str(path)))
        return chain

    def _online_chain(self, item: dict, share: bool = False) -> list:
        """Build a message chain for an online song."""
        name = item.get("name") or "未知歌曲"
        artist = item.get("artist") or ""
        url = item.get("url") or ""
        caption = f"《{name}》" + (f" - {artist}" if artist else "")
        mode = str(self.cfg.get("send_mode", "file") or "file").lower()
        chain: list = [Plain(caption)]
        if mode == "record":
            chain.append(Record.fromURL(url))
        elif mode == "card" or share:
            chain.append(
                Music(
                    _type="custom",
                    url=url,
                    audio=url,
                    title=name,
                    content=artist,
                    image=item.get("pic") or "",
                )
            )
            if url:
                chain.append(Plain(url))
        elif mode == "file":
            chain.append(File(name=f"{name}.mp3", url=url))
        else:
            chain.append(Plain(url))
        return chain

    # ------------------------------------------------------------------ hooks
    @filter.on_llm_request()
    async def notice_shared_music(
        self, event: AstrMessageEvent, req: ProviderRequest
    ):
        """Tell the model when the user shares a music card."""
        if not bool(self.cfg.get("enabled", True)):
            return
        if event.get_extra("_ears_music_noticed"):
            return
        event.set_extra("_ears_music_noticed", True)
        try:
            for comp in event.get_messages():
                if not isinstance(comp, Music):
                    continue
                title = str(getattr(comp, "title", "") or "").strip()
                artist = str(getattr(comp, "content", "") or "").strip()
                if not title:
                    continue
                block = (
                    "【耳朵】\n"
                    f"主人分享了一首歌：《{title}》"
                    + (f"（{artist}）" if artist else "")
                    + "。你可以自然接一句，但不要假装自己真的听过音频。"
                )
                req.system_prompt = (req.system_prompt or "") + "\n\n" + block
                break
        except Exception:
            logger.exception("[ears] 注入分享歌曲信息失败")

    # ------------------------------------------------------------------ tools
    @filter.llm_tool(name="local_music_play")
    async def local_music_play(self, event: AstrMessageEvent, keyword: str = "") -> str:
        """从本地音乐库点一首歌并发送给主人。

        Args:
            keyword(string): 歌名或关键词；留空随机放一首。

        Returns:
            str: 播放结果。
        """
        if not self._is_admin(event):
            return "没有权限。"
        umo = str(getattr(event, "unified_msg_origin", "") or "")
        left = self._cooldown_left(umo)
        if left:
            return f"刚放过，等 {left} 秒再点。"
        try:
            if keyword:
                matches = self._search_local(keyword)
                if matches:
                    await event.send(
                        MessageChain(self._local_chain(matches[0], f"《{matches[0].stem}》"))
                    )
                    self._mark_play(umo)
                    return "放了。"
                online = await self._online_search(keyword)
                if online:
                    await event.send(
                        MessageChain(self._online_chain(online[0]))
                    )
                    self._mark_play(umo)
                    return "放了。"
                return f"没找到「{keyword}」。"
            files = self._scan()
            if not files:
                return "音乐库是空的，先把 mp3 放进 music 目录。"
            picked = random.choice(files)
            await event.send(
                MessageChain(self._local_chain(picked, f"《{picked.stem}》"))
            )
            self._mark_play(umo)
            return "放了。"
        except ValueError as exc:
            return str(exc)
        except Exception:
            logger.exception("[ears] 点歌失败")
            return "放歌时出了点小问题。"

    @filter.llm_tool(name="local_music_search")
    async def local_music_search(
        self, event: AstrMessageEvent, keyword: str = "", limit: int = 5
    ) -> str:
        """搜索本地或在线歌曲。

        Args:
            keyword(string): 关键词。
            limit(int): 最多几条。

        Returns:
            str: 搜索结果。
        """
        if not self._is_admin(event):
            return "没有权限。"
        use_limit = max(1, min(int(limit or 5), 20))
        local = self._search_local(keyword)[:use_limit]
        lines = [f"本地：{path.stem}" for path in local]
        if len(lines) < use_limit:
            online = await self._online_search(keyword)
            lines.extend(
                f"在线：{item['name']} - {item['artist']}"
                for item in online[: use_limit - len(lines)]
            )
        if not lines:
            return f"没搜到「{keyword}」。"
        return "\n".join(lines)

    # ---------------------------------------------------------------- commands
    @filter.command("本地点歌")
    async def cmd_song(self, event: AstrMessageEvent):
        """Search and send a local song."""
        if not self._is_admin(event):
            yield event.plain_result("这个只给主人用。")
            return
        umo = str(getattr(event, "unified_msg_origin", "") or "")
        left = self._cooldown_left(umo)
        if left:
            yield event.plain_result(f"刚放过，等 {left} 秒再点。")
            return
        args = (event.message_str or "").strip()
        for prefix in ("/本地点歌", "本地点歌"):
            if args.startswith(prefix):
                args = args[len(prefix) :].strip()
                break
        if not args:
            yield event.plain_result("要听什么？例如 /本地点歌 晴天。")
            return
        try:
            matches = self._search_local(args)
            if matches:
                yield event.chain_result(self._local_chain(matches[0]))
                self._mark_play(umo)
                return
            online = await self._online_search(args)
            if online:
                yield event.chain_result(self._online_chain(online[0]))
                self._mark_play(umo)
                return
            yield event.plain_result(f"没找到「{args}」。")
        except ValueError as exc:
            yield event.plain_result(str(exc))
        except Exception:
            logger.exception("[ears] /点歌 失败")
            yield event.plain_result("放歌时出了点小问题。")

    @filter.command("本地听歌")
    async def cmd_listen(self, event: AstrMessageEvent):
        """Play a random local song."""
        if not self._is_admin(event):
            yield event.plain_result("这个只给主人用。")
            return
        umo = str(getattr(event, "unified_msg_origin", "") or "")
        left = self._cooldown_left(umo)
        if left:
            yield event.plain_result(f"刚放过，等 {left} 秒再点。")
            return
        files = self._scan()
        if not files:
            yield event.plain_result("音乐库是空的，先把 mp3 放进 music 目录。")
            return
        picked = random.choice(files)
        try:
            yield event.chain_result(self._local_chain(picked, f"《{picked.stem}》"))
            self._mark_play(umo)
        except ValueError as exc:
            yield event.plain_result(str(exc))

    @filter.command("本地歌单")
    async def cmd_playlist(self, event: AstrMessageEvent):
        """List local songs."""
        if not self._is_admin(event):
            yield event.plain_result("这个只给主人用。")
            return
        files = self._scan()
        if not files:
            yield event.plain_result("音乐库是空的。")
            return
        lines = [f"本地歌单（{len(files)} 首）"]
        for index, path in enumerate(files[:20], 1):
            lines.append(f"{index}. {path.stem}")
        if len(files) > 20:
            lines.append("……")
        lines.append("用 /本地点歌 <关键词> 播放。")
        yield event.plain_result("\n".join(lines))

    @filter.command("本地分享")
    async def cmd_share(self, event: AstrMessageEvent):
        """Share a local song."""
        if not self._is_admin(event):
            yield event.plain_result("这个只给主人用。")
            return
        args = (event.message_str or "").strip()
        for prefix in ("/本地分享", "本地分享"):
            if args.startswith(prefix):
                args = args[len(prefix) :].strip()
                break
        if not args:
            yield event.plain_result("要分享什么？例如 /本地分享 晴天。")
            return
        try:
            matches = self._search_local(args)
            if matches:
                yield event.chain_result(self._local_chain(matches[0], f"分享歌曲：《{matches[0].stem}》"))
                return
            online = await self._online_search(args)
            if online:
                yield event.chain_result(self._online_chain(online[0], share=True))
                return
            yield event.plain_result(f"没找到「{args}」。")
        except ValueError as exc:
            yield event.plain_result(str(exc))
        except Exception:
            logger.exception("[ears] /分享歌曲 失败")
            yield event.plain_result("分享歌曲时出了点小问题。")
