"""Conservative request-context folding for AstrBot.

The plugin only rewrites the in-flight ``ProviderRequest.contexts``.  The
original conversation history is restored on the agent-done hook so AstrBot
persists the untouched messages instead of the folded view.

Design rules:
- Never raise into the chat pipeline.  Unknown or malformed input is passed
  through unchanged.
- Never split an assistant tool call from its tool result.
- Never fold system messages when ``keep_system`` is enabled.
- Never call an LLM and never delete or rewrite database rows.
"""

from __future__ import annotations

import copy
import json
import re
from collections import OrderedDict
from datetime import datetime
from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import Context, Star

FOLD_MARKER_TEMPLATE = "【较早的 {count} 条对话已折叠；原文仍保存在会话记录中】"
WAKE_FOLD_MARKER = "【较早的唤醒提示已折叠】"
TOOL_TRUNCATION_SUFFIX = "…[工具结果已折叠]"

_ALLOWED_ROLES = {"system", "user", "assistant", "tool", "_checkpoint"}
_TEXT_PART_TYPES = {"text", "input_text", "output_text"}
_TOOL_RESULT_PART_TYPES = {"tool_result"}
_TOOL_USE_PART_TYPES = {"tool_use"}

# A wake prompt usually starts with a clock time (optionally wrapped in
# brackets, optionally preceded by a date).  The remainder must either be
# empty or contain a narrow set of wake-only keywords, otherwise normal chat
# such as "12:34 我们去吃饭吧" is left untouched.
_WAKE_TIME_RE = re.compile(
    r"^\s*(?:【|\[|\(|（)?\s*"
    r"(?:(?:\d{4}[-/年]\d{1,2}[-/月]\d{1,2}日?)\s*)?"
    r"(?:\d{1,2}[：:]\d{2}(?:[：:]\d{2})?)"
    r"\s*(?:】|\]|\)|）)?"
)
_WAKE_PREFIXES = (
    "【唤醒",
    "[唤醒",
    "唤醒提示",
)
_WAKE_TIME_PREFIXES = ("当前时间", "现在时间", "现在是", "时间：", "时间:")
_WAKE_KEYWORDS = (
    "唤醒",
    "主动",
    "该不该",
    "要不要",
    "距离上次",
    "距上次",
    "沉默",
    "空闲",
    "定时",
    "提醒你",
    "叫你",
    "找你",
    "开口",
    "说句话",
    "聊聊天",
    "回复一下",
    "该说话",
)

def _role(item: dict[str, Any]) -> str:
    """Return a message role, or an empty string when the field is invalid.

    Args:
        item: OpenAI-style message dictionary.

    Returns:
        The role string when it is valid, otherwise an empty string.
    """
    role = item.get("role")
    return role if isinstance(role, str) else ""


def _content_text_length(value: Any) -> int:
    """Count the visible text characters in a content value.

    Non-text parts such as images and audio are intentionally ignored.

    Args:
        value: A content value (string, list of parts, or nested tool result).

    Returns:
        Number of visible text characters.
    """
    if isinstance(value, str):
        return len(value)
    if isinstance(value, list):
        return sum(_content_text_length(part) for part in value)
    if isinstance(value, dict):
        part_type = value.get("type")
        if part_type in _TEXT_PART_TYPES:
            text = value.get("text")
            return len(text) if isinstance(text, str) else 0
        if part_type == "think":
            think = value.get("think")
            return len(think) if isinstance(think, str) else 0
        if part_type in _TOOL_RESULT_PART_TYPES:
            return _content_text_length(value.get("content"))
        return 0
    return 0


def _message_chars(item: dict[str, Any]) -> int:
    """Count the visible text characters of one message.

    Args:
        item: OpenAI-style message dictionary.

    Returns:
        Text characters in content plus serialized tool-call arguments.
    """
    total = _content_text_length(item.get("content"))
    tool_calls = item.get("tool_calls")
    if isinstance(tool_calls, list) and tool_calls:
        try:
            total += len(json.dumps(tool_calls, ensure_ascii=False, default=str))
        except Exception:
            pass
    return total


def _messages_chars(items: list[dict[str, Any]]) -> int:
    """Count visible text characters for a list of messages.

    Args:
        items: Message dictionaries.

    Returns:
        Total visible text characters.
    """
    return sum(_message_chars(item) for item in items)


def _text_only_content(value: Any) -> str | None:
    """Return text if a content value contains text parts only.

    Args:
        value: A content value to inspect.

    Returns:
        Joined text, or None when the content contains a non-text part.
    """
    if isinstance(value, str):
        return value
    if not isinstance(value, list):
        return None
    texts: list[str] = []
    for part in value:
        if isinstance(part, str):
            texts.append(part)
        elif isinstance(part, dict) and part.get("type") in _TEXT_PART_TYPES:
            text = part.get("text")
            if isinstance(text, str):
                texts.append(text)
            else:
                return None
        else:
            return None
    return "\n".join(texts)


def _tool_call_ids(item: dict[str, Any]) -> set[str]:
    """Collect tool-call ids from assistant message formats.

    Args:
        item: OpenAI-style or Anthropic-style message dictionary.

    Returns:
        Set of non-empty tool-call ids.
    """
    ids: set[str] = set()
    tool_calls = item.get("tool_calls")
    if isinstance(tool_calls, list):
        for call in tool_calls:
            if isinstance(call, dict):
                call_id = call.get("id")
                if isinstance(call_id, str) and call_id:
                    ids.add(call_id)
    content = item.get("content")
    if isinstance(content, list):
        for part in content:
            if isinstance(part, dict) and part.get("type") in _TOOL_USE_PART_TYPES:
                call_id = part.get("id") or part.get("tool_use_id")
                if isinstance(call_id, str) and call_id:
                    ids.add(call_id)
    return ids


def _tool_result_id(item: dict[str, Any]) -> str | None:
    """Return the tool-call id answered by a tool message.

    Args:
        item: OpenAI-style or Anthropic-style message dictionary.

    Returns:
        The referenced tool-call id, or None when absent.
    """
    call_id = item.get("tool_call_id")
    if isinstance(call_id, str) and call_id:
        return call_id
    content = item.get("content")
    if isinstance(content, list):
        for part in content:
            if isinstance(part, dict) and part.get("type") in _TOOL_RESULT_PART_TYPES:
                result_id = part.get("tool_use_id") or part.get("tool_call_id")
                if isinstance(result_id, str) and result_id:
                    return result_id
    return None


def _is_tool_message(item: dict[str, Any]) -> bool:
    """Return whether a message is a tool result.

    Args:
        item: Message dictionary.

    Returns:
        True for role=tool, a tool_call_id, or a tool_result content part.
    """
    if _role(item) == "tool":
        return True
    call_id = item.get("tool_call_id")
    if isinstance(call_id, str) and call_id:
        return True
    content = item.get("content")
    if isinstance(content, list):
        for part in content:
            if isinstance(part, dict) and part.get("type") in _TOOL_RESULT_PART_TYPES:
                return True
    return False


def _has_tool_calls(item: dict[str, Any]) -> bool:
    """Return whether an assistant message contains tool calls.

    Args:
        item: Message dictionary.

    Returns:
        True when tool_calls is non-empty or content contains tool_use.
    """
    tool_calls = item.get("tool_calls")
    if isinstance(tool_calls, list) and tool_calls:
        return True
    content = item.get("content")
    if isinstance(content, list):
        for part in content:
            if isinstance(part, dict) and part.get("type") in _TOOL_USE_PART_TYPES:
                return True
    return False


def _is_wake_prompt(item: dict[str, Any]) -> bool:
    """Detect a narrow, high-confidence wake prompt.

    Args:
        item: Message dictionary.

    Returns:
        True when the message is a user text message that looks like a
        time-prefixed wake prompt.
    """
    if _role(item) != "user" or item.get("_no_save"):
        return False
    if item.get("tool_calls") or item.get("tool_call_id"):
        return False
    text = _text_only_content(item.get("content"))
    if text is None:
        return False
    text = text.strip()
    if not text or len(text) > 400:
        return False
    if text.startswith(_WAKE_PREFIXES):
        return True
    if text.startswith(_WAKE_TIME_PREFIXES):
        clock = re.search(r"\d{1,2}[：:]\d{2}(?:[：:]\d{2})?", text)
        if clock is None:
            return False
        remainder = text[clock.end() :].strip(" \t\r\n，,。:：-—|")
        if not remainder:
            return True
        return any(keyword in text for keyword in _WAKE_KEYWORDS)
    match = _WAKE_TIME_RE.match(text)
    if match is None:
        return False
    remainder = text[match.end() :].strip(" \t\r\n，,。:：-—|")
    if not remainder:
        return True
    return any(keyword in text for keyword in _WAKE_KEYWORDS)


def _truncate_string(value: str, limit: int) -> tuple[str, bool]:
    """Truncate a single string and add the fold suffix.

    Args:
        value: Original string.
        limit: Maximum number of characters to keep.

    Returns:
        Tuple of the new string and whether it changed.
    """
    if value.endswith(TOOL_TRUNCATION_SUFFIX):
        return value, False
    if len(value) <= limit:
        return value, False
    return value[:limit] + TOOL_TRUNCATION_SUFFIX, True


def _truncate_content(value: Any, limit: int) -> tuple[Any, bool]:
    """Truncate text inside a content value while preserving non-text parts.

    Args:
        value: String, list of content parts, or nested tool-result value.
        limit: Maximum total text characters to keep.

    Returns:
        Tuple of the new content value and whether it changed.
    """
    if isinstance(value, str):
        return _truncate_string(value, limit)
    if isinstance(value, dict):
        part_type = value.get("type")
        if part_type in _TEXT_PART_TYPES:
            text = value.get("text")
            if isinstance(text, str):
                new_text, changed = _truncate_string(text, limit)
                if changed:
                    value = {**value, "text": new_text}
                return value, changed
            return value, False
        if part_type in _TOOL_RESULT_PART_TYPES:
            new_content, changed = _truncate_content(value.get("content"), limit)
            if changed:
                value = {**value, "content": new_content}
            return value, changed
        return value, False
    if not isinstance(value, list):
        return value, False

    out: list[Any] = []
    budget = max(limit, 0)
    changed = False
    suffix_added = False
    for part in value:
        if isinstance(part, str):
            if suffix_added:
                if part:
                    changed = True
                out.append("")
                continue
            new_part, part_changed = _truncate_string(part, budget)
            if part_changed:
                changed = True
                suffix_added = True
                budget = 0
                out.append(new_part)
            else:
                budget = max(budget - len(part), 0)
                out.append(part)
            continue
        if not isinstance(part, dict):
            out.append(part)
            continue

        part_type = part.get("type")
        if part_type in _TEXT_PART_TYPES and isinstance(part.get("text"), str):
            if suffix_added:
                if part["text"]:
                    changed = True
                    out.append({**part, "text": ""})
                else:
                    out.append(part)
                continue
            new_text, text_changed = _truncate_string(part["text"], budget)
            if text_changed:
                changed = True
                suffix_added = True
                budget = 0
                out.append({**part, "text": new_text})
            else:
                budget = max(budget - len(part["text"]), 0)
                out.append(part)
            continue

        if part_type in _TOOL_RESULT_PART_TYPES:
            nested = part.get("content")
            nested_len = _content_text_length(nested)
            if suffix_added:
                new_nested, nested_changed = _truncate_content(nested, 0)
                if nested_changed:
                    changed = True
                    out.append({**part, "content": new_nested})
                else:
                    out.append(part)
                continue
            new_nested, nested_changed = _truncate_content(nested, budget)
            if nested_changed:
                changed = True
                suffix_added = True
                budget = 0
                out.append({**part, "content": new_nested})
            else:
                budget = max(budget - nested_len, 0)
                out.append(part)
            continue

        out.append(part)
    return out, changed


class DafeiyuContextPlugin(Star):
    """Fold old, duplicated, or oversized context messages per request."""

    def __init__(self, context: Context, config: dict | None = None) -> None:
        """Initialize the plugin.

        Args:
            context: AstrBot plugin context.
            config: Plugin configuration dictionary.
        """
        super().__init__(context)
        self.cfg = config or {}
        self._last_stats: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._last_global_stats: dict[str, Any] | None = None

    # ------------------------------------------------------------------ config
    def _cfg_bool(self, key: str, default: bool) -> bool:
        """Read a boolean config value defensively.

        Args:
            key: Configuration key.
            default: Fallback value.

        Returns:
            Parsed boolean value.
        """
        value = self.cfg.get(key, default)
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on", "是"}
        if isinstance(value, (int, float)):
            return bool(value)
        return default

    def _cfg_int(self, key: str, default: int, minimum: int, maximum: int) -> int:
        """Read an integer config value defensively and clamp it.

        Args:
            key: Configuration key.
            default: Fallback value.
            minimum: Minimum allowed value.
            maximum: Maximum allowed value.

        Returns:
            Parsed and clamped integer value.
        """
        value = self.cfg.get(key, default)
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            parsed = default
        return max(minimum, min(parsed, maximum))

    def _enabled(self) -> bool:
        """Return whether the plugin is enabled.

        Returns:
            True when context folding is enabled.
        """
        return self._cfg_bool("enabled", True)

    # ------------------------------------------------------------------ extras
    @staticmethod
    def _set_extra(event: Any, key: str, value: Any) -> None:
        """Set an event extra without ever raising.

        Args:
            event: AstrBot message event.
            key: Extra key.
            value: Extra value.
        """
        try:
            setter = getattr(event, "set_extra", None)
            if callable(setter):
                setter(key, value)
        except Exception:
            pass

    @staticmethod
    def _get_extra(event: Any, key: str, default: Any = None) -> Any:
        """Read an event extra without ever raising.

        Args:
            event: AstrBot message event.
            key: Extra key.
            default: Fallback value.

        Returns:
            Stored extra value or the fallback.
        """
        try:
            getter = getattr(event, "get_extra", None)
            if callable(getter):
                return getter(key, default)
        except Exception:
            pass
        return default

    # --------------------------------------------------------------- normalize
    def _normalize_contexts(
        self, contexts: list[Any]
    ) -> list[dict[str, Any]] | None:
        """Convert supported context items to dictionaries.

        Args:
            contexts: Raw ``ProviderRequest.contexts`` list.

        Returns:
            A list of validated message dictionaries, or None when any item is
            not a supported type or is structurally invalid.
        """
        normalized: list[dict[str, Any]] = []
        for item in contexts:
            if isinstance(item, dict):
                try:
                    # Shallow copy is enough: folding deep-copies before any
                    # mutation, so nested content stays shared and untouched.
                    message = copy.copy(item)
                except Exception:
                    return None
            elif hasattr(item, "model_dump") and hasattr(item, "role"):
                try:
                    dumped = item.model_dump()
                except Exception:
                    return None
                if not isinstance(dumped, dict):
                    return None
                message = dumped
                if getattr(item, "_no_save", False):
                    message["_no_save"] = True
                checkpoint_after = getattr(item, "_checkpoint_after", None)
                if checkpoint_after is not None:
                    normalized.append(message)
                    try:
                        normalized.append(
                            {
                                "role": "_checkpoint",
                                "content": checkpoint_after.model_dump(),
                            }
                        )
                    except Exception:
                        return None
                    continue
            else:
                return None

            role = _role(message)
            if role not in _ALLOWED_ROLES:
                return None
            tool_calls = message.get("tool_calls")
            if tool_calls is not None and not isinstance(tool_calls, list):
                return None
            tool_call_id = message.get("tool_call_id")
            if tool_call_id is not None and not isinstance(tool_call_id, str):
                return None
            content = message.get("content")
            if role == "_checkpoint":
                if content is not None and not isinstance(content, dict):
                    return None
            elif content is not None and not isinstance(content, (str, list)):
                return None
            elif content is None and not (
                role == "assistant" and isinstance(tool_calls, list) and tool_calls
            ):
                return None
            if isinstance(content, list):
                for part in content:
                    if not isinstance(part, (str, dict)):
                        return None
            normalized.append(message)
        return normalized

    # ----------------------------------------------------------------- folding
    def _build_blocks(
        self, work: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Group messages into atomic blocks.

        An assistant tool-call block absorbs the tool results that immediately
        follow it, so selection can never split a call/result pair.

        Args:
            work: Message dictionaries.

        Returns:
            Block dictionaries with indices, message count, and tool metadata.
        """
        blocks: list[dict[str, Any]] = []
        last_block: dict[str, Any] | None = None
        for index, item in enumerate(work):
            role = _role(item)
            if role == "_checkpoint":
                if last_block is not None:
                    last_block["indices"].append(index)
                continue
            if role == "system":
                continue
            if item.get("_no_save") and not _has_tool_calls(item):
                # Provider-only messages (for example persona begin dialogs)
                # are always kept and never counted toward max_messages.
                last_block = None
                continue

            is_tool = _is_tool_message(item)
            is_call = _has_tool_calls(item)
            if is_tool and last_block is not None and last_block["accepts_tool"]:
                last_block["indices"].append(index)
                last_block["count"] += 1
                continue

            last_block = {
                "indices": [index],
                "count": 1,
                "start": index,
                "accepts_tool": is_call,
            }
            blocks.append(last_block)
        return blocks

    @staticmethod
    def _protected_start_block(
        work: list[dict[str, Any]], blocks: list[dict[str, Any]]
    ) -> int | None:
        """Find the start of the latest complete turn.

        The latest user turn is always kept.  When it contains a tool round,
        that whole round is protected as well.  If no user message exists, the
        latest tool round is used as the protection boundary.

        Args:
            work: Message dictionaries.
            blocks: Atomic message blocks.

        Returns:
            Block index where protection starts, or None.
        """
        for block_index in range(len(blocks) - 1, -1, -1):
            if any(
                _role(work[index]) == "user"
                for index in blocks[block_index]["indices"]
            ):
                return block_index
        for block_index in range(len(blocks) - 1, -1, -1):
            if any(
                _is_tool_message(work[index])
                for index in blocks[block_index]["indices"]
            ):
                return block_index
        return None

    @staticmethod
    def _select_blocks(
        blocks: list[dict[str, Any]],
        max_messages: int,
        protected_from: int | None,
    ) -> set[int]:
        """Select a contiguous suffix of blocks from the tail.

        Args:
            blocks: Atomic message blocks.
            max_messages: Maximum number of non-system messages to keep.
            protected_from: First block of the latest turn that must be kept.

        Returns:
            Selected block indices.
        """
        selected: set[int] = set()
        total = 0
        start = len(blocks) - 1
        if protected_from is not None:
            for block_index in range(protected_from, len(blocks)):
                selected.add(block_index)
                total += blocks[block_index]["count"]
            start = protected_from - 1

        for block_index in range(start, -1, -1):
            count = blocks[block_index]["count"]
            if not selected or total + count <= max_messages:
                selected.add(block_index)
                total += count
            else:
                break
        return selected

    def _would_split_tool_pair(
        self,
        work: list[dict[str, Any]],
        blocks: list[dict[str, Any]],
        selected: set[int],
    ) -> bool:
        """Check whether a candidate selection separates a tool pair.

        This is a safety net for malformed histories where an assistant tool
        call and its result are not adjacent.  In that case folding is aborted
        rather than risking an invalid provider request.

        Args:
            work: Message dictionaries.
            blocks: Atomic message blocks.
            selected: Selected block indices.

        Returns:
            True when a retained call/result would lose its counterpart.
        """
        selected_indices: set[int] = set()
        for block_index in selected:
            selected_indices.update(blocks[block_index]["indices"])
        for index, item in enumerate(work):
            if item.get("_no_save") and not _has_tool_calls(item):
                selected_indices.add(index)

        all_call_ids: set[str] = set()
        all_result_ids: set[str] = set()
        selected_call_ids: set[str] = set()
        selected_result_ids: set[str] = set()
        for index, item in enumerate(work):
            call_ids = _tool_call_ids(item)
            result_id = _tool_result_id(item)
            all_call_ids.update(call_ids)
            if result_id:
                all_result_ids.add(result_id)
            if index in selected_indices:
                selected_call_ids.update(call_ids)
                if result_id:
                    selected_result_ids.add(result_id)

        missing_calls = (all_call_ids & selected_result_ids) - selected_call_ids
        missing_results = (all_result_ids & selected_call_ids) - selected_result_ids
        return bool(missing_calls or missing_results)

    def _fold_contexts(
        self,
        items: list[dict[str, Any]],
        max_messages: int,
        max_tool_result_chars: int,
        fold_tool_results: bool,
        fold_duplicate_wake_prompts: bool,
        keep_system: bool,
    ) -> tuple[list[dict[str, Any]], dict[str, int]]:
        """Fold old blocks, duplicate wake prompts, and long tool results.

        Args:
            items: Validated message dictionaries.
            max_messages: Maximum recent non-system messages to keep.
            max_tool_result_chars: Tool-result text budget.
            fold_tool_results: Whether to truncate long tool results.
            fold_duplicate_wake_prompts: Whether to fold older wake prompts.
            keep_system: Whether system messages must always be kept.

        Returns:
            Tuple of the folded message list and counters.
        """
        work = copy.deepcopy(items)
        counters = {
            "dropped_messages": 0,
            "truncated_tool_results": 0,
            "folded_wake_prompts": 0,
            "tool_pair_guard": 0,
        }

        blocks = self._build_blocks(work)
        protected_from = self._protected_start_block(work, blocks)
        selected = self._select_blocks(blocks, max_messages, protected_from)
        if blocks and self._would_split_tool_pair(work, blocks, selected):
            # Safer to send the original context than to send a broken pair.
            counters["tool_pair_guard"] = 1
            return work, counters

        dropped_blocks = [
            block for index, block in enumerate(blocks) if index not in selected
        ]
        if not dropped_blocks:
            out = work
        else:
            selected_indices: set[int] = set()
            for block_index in selected:
                selected_indices.update(blocks[block_index]["indices"])
            first_retained_index = min(
                (blocks[block_index]["start"] for block_index in selected),
                default=len(work),
            )
            dropped_count = sum(block["count"] for block in dropped_blocks)
            counters["dropped_messages"] = dropped_count
            marker = {
                "role": "user",
                "content": FOLD_MARKER_TEMPLATE.format(count=dropped_count),
            }

            out = []
            marker_inserted = False
            for index, item in enumerate(work):
                role = _role(item)
                if item.get("_no_save") and not _has_tool_calls(item):
                    out.append(item)
                    continue
                if role == "system":
                    if keep_system or index >= first_retained_index:
                        out.append(item)
                    continue
                if role == "_checkpoint":
                    if index in selected_indices:
                        out.append(item)
                    continue
                if index in selected_indices:
                    out.append(item)
                elif not marker_inserted:
                    out.append(marker)
                    marker_inserted = True
            if not marker_inserted:
                out.insert(0, marker)

        if fold_duplicate_wake_prompts:
            wake_indices = [
                index for index, item in enumerate(out) if _is_wake_prompt(item)
            ]
            if len(wake_indices) > 1:
                for index in wake_indices[:-1]:
                    out[index] = {**out[index], "content": WAKE_FOLD_MARKER}
                    counters["folded_wake_prompts"] += 1

        if fold_tool_results and max_tool_result_chars > 0:
            for item in out:
                if item.get("_no_save") or not _is_tool_message(item):
                    continue
                content = item.get("content")
                new_content, changed = _truncate_content(
                    content, max_tool_result_chars
                )
                if changed:
                    item["content"] = new_content
                    counters["truncated_tool_results"] += 1

        return out, counters

    # --------------------------------------------------------------- request
    def _process_request(self, event: Any, req: ProviderRequest) -> None:
        """Apply folding to a request and record statistics.

        Args:
            event: AstrBot message event.
            req: Provider request whose ``contexts`` should be folded.
        """
        if not self._enabled() or req is None:
            return
        contexts = getattr(req, "contexts", None)
        if not isinstance(contexts, list):
            return
        if self._get_extra(event, "_dafeiyu_ctx_active", False):
            # A second hook invocation for the same event would fold an
            # already-folded view.  Skip it to keep restoration exact.
            return

        normalized = self._normalize_contexts(contexts)
        if normalized is None:
            logger.debug(
                "dafeiyu context: unsupported contexts shape; passing through"
            )
            return

        max_messages = self._cfg_int("max_messages", 40, 1, 10000)
        max_tool_result_chars = self._cfg_int(
            "max_tool_result_chars", 300, 0, 1000000
        )
        folded, counters = self._fold_contexts(
            normalized,
            max_messages=max_messages,
            max_tool_result_chars=max_tool_result_chars,
            fold_tool_results=self._cfg_bool("fold_tool_results", True),
            fold_duplicate_wake_prompts=self._cfg_bool(
                "fold_duplicate_wake_prompts", True
            ),
            keep_system=self._cfg_bool("keep_system", True),
        )

        before_chars = _messages_chars(normalized)
        after_chars = _messages_chars(folded)
        changed = folded != normalized
        stats = {
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "before_messages": len(normalized),
            "after_messages": len(folded),
            "before_chars": before_chars,
            "after_chars": after_chars,
            "dropped_messages": counters["dropped_messages"],
            "truncated_tool_results": counters["truncated_tool_results"],
            "folded_wake_prompts": counters["folded_wake_prompts"],
            "tool_pair_guard": counters["tool_pair_guard"],
            "changed": changed,
            "history_restore": "pending" if changed else "not_needed",
        }

        if changed:
            self._set_extra(event, "_dafeiyu_ctx_original", normalized)
            self._set_extra(event, "_dafeiyu_ctx_folded", copy.deepcopy(folded))
            self._set_extra(event, "_dafeiyu_ctx_stats", stats)
            self._set_extra(event, "_dafeiyu_ctx_active", True)
            req.contexts = folded

        self._record_stats(event, stats)

    # ----------------------------------------------------------------- restore
    @staticmethod
    def _message_dump(message: Any) -> dict[str, Any] | None:
        """Return a comparable dump for a Message or dict.

        Args:
            message: Pydantic Message or dict.

        Returns:
            Message dictionary, or None when it cannot be dumped.
        """
        if isinstance(message, dict):
            return message
        try:
            dumped = message.model_dump()
        except Exception:
            return None
        return dumped if isinstance(dumped, dict) else None

    def _restore_history(self, event: Any, run_context: Any) -> None:
        """Restore the untouched context prefix before AstrBot saves history.

        The internal Agent runner saves ``run_context.messages`` back to the
        conversation store.  Replacing the folded prefix with the original
        messages keeps the database history complete while the provider still
        received the smaller folded view.

        Args:
            event: AstrBot message event.
            run_context: Agent run context containing ``messages``.
        """
        if not self._get_extra(event, "_dafeiyu_ctx_active", False):
            return

        stats = self._get_extra(event, "_dafeiyu_ctx_stats", None)
        original = self._get_extra(event, "_dafeiyu_ctx_original", None)
        folded = self._get_extra(event, "_dafeiyu_ctx_folded", None)
        messages = getattr(run_context, "messages", None)

        def finish(status: str) -> None:
            if isinstance(stats, dict):
                stats["history_restore"] = status
            self._set_extra(event, "_dafeiyu_ctx_active", False)
            self._set_extra(event, "_dafeiyu_ctx_original", None)
            self._set_extra(event, "_dafeiyu_ctx_folded", None)
            self._set_extra(event, "_dafeiyu_ctx_stats", None)

        if (
            not isinstance(original, list)
            or not isinstance(folded, list)
            or not isinstance(messages, list)
            or not messages
        ):
            finish("skipped")
            return

        try:
            from astrbot.core.agent.message import bind_checkpoint_messages
        except Exception:
            logger.debug("dafeiyu context: Message helpers unavailable", exc_info=True)
            finish("skipped")
            return

        try:
            expected = bind_checkpoint_messages(folded)
        except Exception:
            logger.debug("dafeiyu context: folded prefix is invalid", exc_info=True)
            finish("skipped")
            return
        if not expected:
            finish("skipped")
            return

        candidates: list[int] = []
        first_message = self._message_dump(messages[0])
        if first_message and first_message.get("role") == "system":
            expected_first = self._message_dump(expected[0])
            if expected_first == first_message:
                # The first message is likely part of the context itself.
                candidates.extend([0, 1])
            else:
                # A system prompt was inserted before the folded context.
                candidates.extend([1, 0])
        else:
            candidates.append(0)

        offset: int | None = None
        for candidate in candidates:
            if candidate + len(expected) > len(messages):
                continue
            matched = True
            for index, expected_message in enumerate(expected):
                actual = self._message_dump(messages[candidate + index])
                wanted = self._message_dump(expected_message)
                if actual is None or wanted is None or actual != wanted:
                    matched = False
                    break
            if matched:
                offset = candidate
                break

        if offset is None:
            # Built-in compaction or another plugin changed the context, so
            # restoration cannot be proven safe.  Leave the run untouched.
            logger.debug(
                "dafeiyu context: folded prefix no longer matches; skip restore"
            )
            finish("skipped")
            return

        try:
            replacement = bind_checkpoint_messages(original)
            messages[offset : offset + len(expected)] = replacement
        except Exception:
            logger.warning(
                "dafeiyu context: failed to restore original history",
                exc_info=True,
            )
            finish("failed")
            return

        finish("restored")

    # ------------------------------------------------------------------ hooks
    @filter.on_llm_request()
    async def on_llm_request(
        self, event: AstrMessageEvent, req: ProviderRequest
    ) -> None:
        """Fold contexts before the provider request is sent.

        Args:
            event: AstrBot message event.
            req: Provider request.
        """
        try:
            self._process_request(event, req)
        except Exception:
            logger.warning(
                "dafeiyu context: fold failed; leaving request unchanged",
                exc_info=True,
            )

    @filter.on_agent_done()
    async def on_agent_done(
        self, event: AstrMessageEvent, run_context: Any, response: Any
    ) -> None:
        """Restore the original history before AstrBot persists the run.

        Args:
            event: AstrBot message event.
            run_context: Agent run context.
            response: Final LLM response (unused).
        """
        try:
            self._restore_history(event, run_context)
        except Exception:
            logger.warning(
                "dafeiyu context: history restore failed", exc_info=True
            )

    # ------------------------------------------------------------------- stats
    def _record_stats(self, event: Any, stats: dict[str, Any]) -> None:
        """Store statistics for ``/上下文`` and event consumers.

        Args:
            event: AstrBot message event.
            stats: Statistics dictionary.
        """
        self._last_global_stats = stats
        umo = getattr(event, "unified_msg_origin", "") or ""
        if umo:
            self._last_stats[umo] = stats
            self._last_stats.move_to_end(umo)
            while len(self._last_stats) > 500:
                self._last_stats.popitem(last=False)

        self._set_extra(event, "dafeiyu_context_stats", stats)
        self._set_extra(
            event, "dafeiyu_context_before_messages", stats["before_messages"]
        )
        self._set_extra(
            event, "dafeiyu_context_after_messages", stats["after_messages"]
        )
        self._set_extra(
            event, "dafeiyu_context_before_chars", stats["before_chars"]
        )
        self._set_extra(
            event, "dafeiyu_context_after_chars", stats["after_chars"]
        )

    @staticmethod
    def _format_stats(stats: dict[str, Any]) -> str:
        """Format statistics for the ``/上下文`` command.

        Args:
            stats: Statistics dictionary.

        Returns:
            Human-readable report.
        """
        restore_labels = {
            "not_needed": "无需回填",
            "pending": "等待本轮结束",
            "restored": "已恢复完整历史",
            "skipped": "未回填（上下文已被其他机制调整）",
            "failed": "回填失败",
        }
        saved = stats["before_chars"] - stats["after_chars"]
        change_text = (
            f"减少：{saved} 字符"
            if saved >= 0
            else f"增加：{-saved} 字符"
        )
        lines = [
            "【最近一次请求的上下文】",
            f"原始：{stats['before_messages']} 条 / {stats['before_chars']} 字符",
            f"折叠后：{stats['after_messages']} 条 / {stats['after_chars']} 字符",
            change_text,
            f"折叠旧消息：{stats['dropped_messages']} 条",
            f"截断工具结果：{stats['truncated_tool_results']} 条",
            f"折叠唤醒提示：{stats['folded_wake_prompts']} 条",
            "历史回填：" + restore_labels.get(stats["history_restore"], "未知"),
        ]
        if stats.get("tool_pair_guard"):
            lines.append("注意：检测到工具调用对不完整，本轮已放弃折叠。")
        return "\n".join(lines)

    @filter.command("上下文")
    async def context_status(self, event: AstrMessageEvent):
        """Show the latest context folding statistics.

        Args:
            event: AstrBot message event.

        Yields:
            Plain text result.
        """
        try:
            umo = getattr(event, "unified_msg_origin", "") or ""
            stats = self._last_stats.get(umo) if umo else None
            if stats is None:
                stats = self._last_global_stats
            if stats is None:
                yield event.plain_result("还没有请求记录")
                return
            yield event.plain_result(self._format_stats(stats))
        except Exception:
            logger.warning("dafeiyu context: status command failed", exc_info=True)
            yield event.plain_result("读取上下文统计失败。")
