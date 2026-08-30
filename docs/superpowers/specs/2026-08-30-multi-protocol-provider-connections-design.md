# Multi-Protocol Provider Connections Design

## Goal

Allow one provider connection to expose multiple HTTP protocols and capabilities. A single LM Studio or custom service connection may therefore serve text-generation and embedding roles without duplicating its URL, authentication, or secret.

## Domain Model

`model_connections` owns shared transport configuration:

- connection ID and display name
- vendor
- authentication type
- Base URL
- encrypted credential and masked credential
- enabled state and timestamps

Protocol support moves to a new `model_connection_protocols` table:

- connection ID
- protocol (`openai_responses`, `openai_chat_completions`, `anthropic_messages`, `openai_embeddings`)
- derived capability (`text_generation` or `embedding`)
- source (`detected` or `manual`)
- verification status (`passed`, `failed`, or `untested`)
- last test timestamp and stable error code

The `(connection_id, protocol)` pair is unique. A connection must have at least one enabled protocol.

Model role assignments store all three selections explicitly:

- `connection_id`
- `protocol`
- `model`

The selected protocol must belong to the connection and have the capability required by the role.

## SQLite Migration

Migration is idempotent and runs during model-store initialization.

1. Create `model_connection_protocols` if it does not exist.
2. For each existing connection without protocol rows, copy its legacy `protocol` and `capability` columns into one protocol row.
3. Preserve the legacy columns during the compatibility period; new runtime reads use protocol rows only.
4. Add the selected protocol to existing draft and immutable-version assignments when it is absent. The value comes from the referenced connection’s migrated protocol row.
5. Record a schema-version state key so subsequent starts are no-ops.

Migration never rewrites encrypted credentials and never drops legacy data.

## Protocol Detection

The unsaved-connection test becomes protocol discovery. It receives Base URL, vendor, authentication data, and an optional manually selected protocol set. It does not persist any submitted secret.

Detection proceeds in bounded steps:

1. Validate URL, authentication, and vendor constraints.
2. Request the model list when the provider exposes one.
3. Probe candidate endpoints with deliberately invalid or minimal non-generating requests.
4. Classify endpoint existence from HTTP status and structured error shape rather than assuming every compatible server follows one exact status convention.
5. Infer text and embedding capability from endpoint evidence and model metadata.

Known vendor presets restrict the candidate set to documented protocols. Custom connections may probe all four protocols.

Each candidate returns:

- protocol and capability
- `passed`, `failed`, or `untested`
- source `detected`
- stable error code and a user-readable message when applicable

Detection is advisory. Users may enable or disable protocols before saving. A manually enabled protocol that did not pass detection is stored as `source=manual`, `verification_status=untested`. The UI visibly distinguishes it from a verified protocol.

OAuth Gateway connections remain system-managed: Base URL and authentication are locked, and candidate protocols come from the ChatGPT Subscription preset.

## Connection Creation UI

The modal retains shared connection fields and replaces the single protocol selector with a protocol capability panel.

- “探测协议” is shown for API Key and no-auth connections.
- The result list shows protocol name, capability, detected status, and a checkbox.
- Users may correct the selected set after detection.
- At least one protocol must be selected before creation.
- API Key and no-auth connections require either a successful detection or an explicit manual selection acknowledgement.
- OAuth connections use their managed preset and OAuth login state.
- Editing URL, authentication, or secret clears detection results.

No model is chosen or stored in this modal.

## Model Role UI

Each role card uses a three-stage selection:

1. Provider connection
2. Compatible protocol
3. Concrete model

Changing an earlier selection clears all dependent selections and model-list state.

For Embedding, the connection list includes every connection with an enabled `openai_embeddings` protocol. The protocol selector then contains embedding protocols only.

For high-capability and high-throughput roles, the connection list includes connections with at least one enabled text protocol. The protocol selector contains Responses, Chat Completions, and Anthropic Messages as supported by that connection.

Selecting a protocol loads models through that protocol. When a service cannot list models, manual model entry remains available and is clearly marked.

## Runtime Routing and Snapshots

`ModelRouter` resolves the role assignment’s protocol, verifies it still belongs to the connection, and returns connection transport settings plus the selected protocol and model.

Applying a draft creates an immutable version containing connection ID, protocol, model, and advanced role settings. Existing project snapshots remain unchanged. New projects snapshot the active version.

Graphiti, OASIS/CAMEL, report generation, and direct LLM calls continue to consume the protocol returned by `ModelRouter`; they no longer assume a connection has one protocol.

## API Changes

- Connection responses expose `protocols` as a list of protocol-state objects instead of top-level `protocol` and `capability` fields.
- Connection creation accepts a `protocols` list.
- Unsaved connection testing returns protocol discovery results.
- Model listing requires both connection ID and protocol, plus the target role.
- Draft and apply payloads require `connection_id`, `protocol`, and `model` for every configured role.

During migration, the backend accepts legacy single-protocol connection payloads only for internal environment import. The public UI and API use the new shape.

## Validation and Errors

The backend is authoritative for:

- vendor/protocol compatibility
- OAuth-managed fields
- at least one enabled protocol
- protocol membership on a connection
- role/protocol capability compatibility
- connection existence and enabled state
- complete role assignments at apply time

Stable detection and runtime error codes are preserved without exposing credentials or raw upstream response bodies.

## Testing

Automated coverage includes:

- idempotent migration from every legacy protocol
- existing drafts and versions gaining their protocol field
- multi-protocol connection CRUD and credential secrecy
- detection results and manual corrections
- one connection serving text and embedding roles
- role cascading selection behavior
- protocol-specific model discovery
- router and project snapshot isolation
- Graphiti and OASIS protocol routing regression
- API validation and error mapping
- responsive and accessible modal behavior

Browser verification covers a local LM Studio connection that exposes Chat Completions and Embeddings, confirming that the same connection appears in both text and Embedding role cards and that each role receives the correct filtered model list.
