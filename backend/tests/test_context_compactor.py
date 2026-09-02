import json

import pytest

from scripts.context_compactor import compact_messages


class CharacterCounter:
    def count_tokens_from_messages(self, messages):
        total = 0
        for message in messages:
            total += len(str(message.get("content", "")))
            total += len(json.dumps(message.get("tool_calls", []), ensure_ascii=False))
        return total


def test_under_budget_history_is_unchanged():
    messages = [{"role": "system", "content": "persona"}, {"role": "user", "content": "recent"}]
    result = compact_messages(messages, [], CharacterCounter(), 20_000)
    assert result.messages == messages
    assert result.removed_groups == 0


def test_oldest_history_is_removed_before_recent_history():
    messages = [
        {"role": "system", "content": "persona"},
        {"role": "user", "content": "old" * 1_500},
        {"role": "assistant", "content": "middle" * 500},
        {"role": "user", "content": "recent"},
    ]
    result = compact_messages(messages, [], CharacterCounter(), 20_000)
    contents = [message["content"] for message in result.messages]
    assert "old" * 1_500 not in contents
    assert contents[-1] == "recent"


def test_system_persona_is_never_removed():
    messages = [
        {"role": "system", "content": "persona" * 100},
        {"role": "user", "content": "history" * 1_000},
    ]
    result = compact_messages(messages, [], CharacterCounter(), 20_000)
    assert result.messages[0] == messages[0]


def test_tool_call_and_matching_outputs_are_removed_atomically():
    tool_calls = [
        {"id": "call-1", "type": "function", "function": {"name": "post", "arguments": "x" * 2_500}},
        {"id": "call-2", "type": "function", "function": {"name": "like", "arguments": "y" * 2_500}},
    ]
    messages = [
        {"role": "system", "content": "persona"},
        {"role": "assistant", "content": "", "tool_calls": tool_calls},
        {"role": "tool", "tool_call_id": "call-1", "content": "result" * 500},
        {"role": "tool", "tool_call_id": "call-2", "content": "result" * 500},
        {"role": "user", "content": "recent"},
    ]
    result = compact_messages(messages, [], CharacterCounter(), 20_000)
    assert all(message.get("tool_call_id") not in {"call-1", "call-2"} for message in result.messages)
    assert all(not message.get("tool_calls") for message in result.messages)
    assert result.messages[-1]["content"] == "recent"


def test_tool_schema_tokens_count_toward_budget():
    tools = [{"type": "function", "function": {"name": "post", "description": "x" * 5_000, "parameters": {}}}]
    result = compact_messages([{"role": "system", "content": "persona"}], tools, CharacterCounter(), 30_000)
    assert result.original_tokens > len("persona")


def test_oversized_fixed_context_fails_locally():
    with pytest.raises(ValueError, match="固定上下文"):
        compact_messages(
            [{"role": "system", "content": "x" * 5_000}],
            [], CharacterCounter(), 20_000,
        )
