"""Known model limits and context-budget calculations."""

KNOWN_CONTEXT_WINDOWS = {
    "gpt-5.6": 1_050_000,
    "gpt-5.6-luna": 1_050_000,
    "gpt-5.6-terra": 1_050_000,
    "gpt-5.6-sol": 1_050_000,
}


def known_context_window(model: str) -> int | None:
    return KNOWN_CONTEXT_WINDOWS.get((model or "").strip().lower())


def input_token_budget(context_window_tokens: int) -> int:
    if isinstance(context_window_tokens, bool) or not isinstance(context_window_tokens, int):
        raise ValueError("模型最大上下文必须是正整数")
    reserve = min(128_000, max(16_000, int(context_window_tokens * 0.10)))
    budget = context_window_tokens - reserve
    if budget <= 0:
        raise ValueError("模型最大上下文必须大于预留输出空间")
    return budget
