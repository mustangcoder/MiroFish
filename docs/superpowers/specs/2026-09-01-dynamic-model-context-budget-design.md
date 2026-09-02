# Dynamic Model Context Budget Design

## Goal

Persist the maximum context window for each configured text-model role and use it to keep long-running OASIS agent requests within the selected model's real token limit. The budget must be derived from model configuration rather than a hard-coded GPT-5.6-specific threshold.

## Configuration Ownership

`context_window_tokens` belongs to a model-role assignment, not to a provider connection. A single connection can expose several models with different limits, while an assignment identifies the exact connection, protocol, and model used for one responsibility.

The field is stored in the existing JSON documents in:

- `model_role_drafts.config_json`
- `model_config_versions.assignments_json`
- `project_model_snapshots.assignments_json`

No SQLite schema migration is required. Embedding assignments do not use this field.

## Known Model Metadata

Add a backend-owned model metadata catalog that maps exact model IDs and documented aliases to context-window tokens. Initial entries are:

- `gpt-5.6-luna`: `1_050_000`
- `gpt-5.6-terra`: `1_050_000`
- `gpt-5.6-sol`: `1_050_000`
- `gpt-5.6`: `1_050_000`

The catalog is an autofill source, not an immutable policy. Users may override an automatically populated value because gateways and compatible providers can impose a different effective limit.

For an unknown text model, `context_window_tokens` is required and must be a positive integer. Configuration can be edited as a draft, but applying a draft with an assigned unknown model and no context window fails with an actionable validation error.

## Configuration Center UI

The advanced parameters for `high_capability` and `high_throughput` add a “最大上下文 Tokens” numeric field. Embedding does not display it.

When a user selects or manually enters a known model, the UI requests or uses backend-provided metadata and fills the context window. Automatic values remain editable. When the selected model changes, an existing manually edited value is preserved only when it was explicitly entered for that model; otherwise the known default is refreshed.

The API remains authoritative: frontend behavior improves the interaction, while backend validation and autofill protect non-UI clients.

## Dynamic Token Budget

For a configured context window `C`, calculate:

```text
reserve = clamp(floor(C * 0.10), 16_000, 128_000)
input_budget = C - reserve
```

The reserve covers visible output, reasoning tokens, and small serialization differences between the local counter and the upstream service. For a 1,050,000-token GPT-5.6 window, the input budget is 945,000 tokens.

Reject configurations where the resulting input budget is non-positive.

## Runtime Propagation

The selected assignment's `context_window_tokens` is propagated through the model router and simulation configuration into `ResponsesModelBackend`. Project snapshots retain the value so a running or historical project does not silently change when global settings are edited.

The backend must not infer a default context window at runtime. Missing context metadata is a configuration error caught before a simulation starts.

## Token Accounting

Before every OASIS text-generation request, calculate the token cost of:

- system and developer messages;
- user, assistant, and tool messages;
- assistant function calls and their arguments;
- tool results;
- serialized tool definitions and JSON Schemas;
- a small per-message framing allowance required by the protocol.

Use CAMEL's `OpenAITokenCounter` for message content where supported. Count serialized tool definitions with the same tokenizer or a conservative fallback. Token accounting logs contain only counts, model ID, budget, and the number of removed history groups; they never contain prompts, tool arguments, or credentials.

## History Compaction

If the complete request is within `input_budget`, send it unchanged.

Otherwise:

1. Preserve every system and developer message, including the full Agent persona.
2. Partition remaining history into atomic groups.
3. An assistant message containing tool calls and its corresponding tool-result messages form one atomic group.
4. Ordinary user or assistant messages form individual groups.
5. Drop the oldest removable groups until the request fits.
6. Preserve chronological order for all retained groups.

If fixed system/developer content plus tool definitions already exceeds the budget, fail before the network request with an actionable error. Do not truncate a persona silently.

This phase deliberately drops old history rather than using an LLM summary. Summarization would add latency, cost, failure modes, and another context-dependent request.

## Responses API Safety Net

Set `truncation: "auto"` on OpenAI Responses requests after local compaction. Local compaction is the primary policy because it preserves tool-call pairs and personas. Server-side automatic truncation is only a final guard against tokenizer or serialization differences.

Standard OpenAI Responses providers receive this field. The ChatGPT Subscription OAuth gateway accepts it from MiroFish for protocol compatibility but deliberately omits it from the private ChatGPT Codex upstream payload, because that endpoint rejects the field with HTTP 400. Local compaction remains the primary protection for OAuth requests.

## Error Classification

`context_length_exceeded`, `invalid_request_error`, invalid tool schemas, unsupported values, missing models, and permission errors are deterministic and must not be retried.

Network disconnects, timeouts, HTTP 429, HTTP 5xx, incomplete SSE streams, and provider server errors remain retryable with bounded backoff.

The gateway returns the provider error code without prompt contents so the simulation log can distinguish an invalid request from a transient outage.

## Scope

The configuration field is protocol-neutral and available for OpenAI Responses, OpenAI Chat Completions, and Anthropic text roles. This implementation applies compaction to the OASIS OpenAI Responses path first because that is the failing runtime path. Other protocol adapters consume the persisted field in later work rather than inventing independent defaults.

Graphiti episode controls remain unchanged:

- `BATCH_SIZE = 20`
- `MAX_EPISODE_CHARS = 9_500`
- same-platform, same-round grouping

Those controls limit graph-extraction Episodes and are separate from Agent conversation compaction.

## Testing

- Known GPT-5.6 model IDs autofill `1_050_000`.
- Unknown text models require an explicit positive context window at apply time.
- Draft, applied version, and project snapshot preserve the value.
- The configuration center renders and edits the field for text roles only.
- Dynamic reserve calculation covers small, normal, and very large windows.
- Requests below budget are unchanged.
- Requests above budget drop the oldest groups.
- Personas and system/developer messages are retained.
- Function calls and matching tool results are retained or removed together.
- Tool schemas are included in token accounting.
- Oversized fixed content fails locally.
- Responses requests include `truncation: "auto"`.
- `context_length_exceeded` is not retried.
- Existing transient retry behavior remains intact.
- Full backend, Direct Gateway, frontend build, and Docker runtime verification pass.

## Migration

On initialization, existing known GPT-5.6 assignments without `context_window_tokens` are backfilled in drafts, applied versions, and project snapshots. Unknown existing assignments remain unset and must be completed before the next apply or simulation start. Existing running processes are not mutated; the value takes effect after deployment and restart.
