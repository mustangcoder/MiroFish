"""Token-budget compaction for long-running OASIS conversations."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
from typing import Any

from app.services.model_metadata import input_token_budget


@dataclass(frozen=True)
class CompactionResult:
    messages: list[dict[str, Any]]
    original_tokens: int
    compacted_tokens: int
    removed_groups: int
    input_budget: int


def _count_tokens(messages, tools, token_counter) -> int:
    total = token_counter.count_tokens_from_messages(messages)
    if tools:
        serialized_tools = json.dumps(tools, ensure_ascii=False, sort_keys=True)
        total += token_counter.count_tokens_from_messages([
            {"role": "user", "content": serialized_tools}
        ])
    return total


def _removable_groups(messages: list[dict[str, Any]]) -> list[list[int]]:
    groups = []
    index = 0
    while index < len(messages):
        message = messages[index]
        if message.get("role") in {"system", "developer"}:
            index += 1
            continue

        group = [index]
        tool_calls = message.get("tool_calls") or []
        if message.get("role") == "assistant" and tool_calls:
            call_ids = {call.get("id") for call in tool_calls if call.get("id")}
            following = index + 1
            while following < len(messages):
                candidate = messages[following]
                if candidate.get("role") != "tool" or candidate.get("tool_call_id") not in call_ids:
                    break
                group.append(following)
                following += 1
            index = following
        else:
            index += 1
        groups.append(group)
    return groups


def compact_messages(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
    token_counter,
    context_window_tokens: int,
) -> CompactionResult:
    """Drop oldest removable message groups until the dynamic budget is met."""
    source = deepcopy(messages)
    tools = deepcopy(tools or [])
    budget = input_token_budget(context_window_tokens)
    original_tokens = _count_tokens(source, tools, token_counter)
    if original_tokens <= budget:
        return CompactionResult(source, original_tokens, original_tokens, 0, budget)

    fixed = [message for message in source if message.get("role") in {"system", "developer"}]
    fixed_tokens = _count_tokens(fixed, tools, token_counter)
    if fixed_tokens > budget:
        raise ValueError(
            f"模型固定上下文超过输入预算: fixed_tokens={fixed_tokens}, budget={budget}"
        )

    removed_indexes = set()
    removed_groups = 0
    compacted_tokens = original_tokens
    for group in _removable_groups(source):
        removed_indexes.update(group)
        removed_groups += 1
        retained = [message for index, message in enumerate(source) if index not in removed_indexes]
        compacted_tokens = _count_tokens(retained, tools, token_counter)
        if compacted_tokens <= budget:
            return CompactionResult(
                retained, original_tokens, compacted_tokens, removed_groups, budget
            )

    raise ValueError(
        f"模型固定上下文超过输入预算: fixed_tokens={fixed_tokens}, budget={budget}"
    )
