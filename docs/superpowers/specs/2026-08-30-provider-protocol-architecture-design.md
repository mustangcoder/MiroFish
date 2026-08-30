# Provider 与协议分层设计

## 目标

重构配置中心的模型连接抽象，将“模型厂商”“接口协议”“认证方式”和“模型能力”拆成独立维度。所有能力均通过 HTTP API 接入，不区分本地或在线部署。

重构后支持：

- OpenAI Responses API
- OpenAI Chat Completions API
- Anthropic Messages API
- OpenAI-compatible Embeddings API
- OpenAI、Anthropic、DeepSeek、Kimi、ChatGPT Subscription 和自定义厂商
- API Key、OAuth Gateway 和无认证三种认证方式

## 核心概念

### 厂商

厂商用于提供名称、默认 Base URL、支持的协议和模型发现策略，不决定底层调用代码。

首批厂商：

| 标识 | 名称 | 默认地址 | 支持协议 |
|---|---|---|---|
| `openai` | OpenAI API | `https://api.openai.com/v1` | Responses、Chat Completions、Embeddings |
| `anthropic` | Anthropic API | `https://api.anthropic.com` | Anthropic Messages |
| `deepseek` | DeepSeek | `https://api.deepseek.com` | Chat Completions、Anthropic Messages |
| `kimi` | Kimi | `https://api.moonshot.cn/v1` | Chat Completions |
| `chatgpt_subscription` | ChatGPT Subscription | Direct OAuth Gateway 地址 | Chat Completions |
| `custom` | 自定义 | 无 | 用户选择任一受支持协议 |

厂商预设只提供默认值。用户可以修改 Base URL，以支持代理、企业网关或自行部署的兼容服务。

### 协议

协议决定 HTTP 请求结构、响应解析、错误处理和能力探测。

文本生成协议：

- `openai_responses`：`POST /responses`
- `openai_chat_completions`：`POST /chat/completions`
- `anthropic_messages`：`POST /v1/messages`，Base URL 已含版本前缀时避免重复拼接

向量协议：

- `openai_embeddings`：`POST /embeddings`

协议与厂商是多对多关系。DeepSeek 可以选择 Chat Completions 或 Anthropic Messages；自定义厂商可以选择任一协议。

### 认证方式

- `api_key`：服务端加密保存 API Key
- `oauth_gateway`：通过内部 OAuth Gateway 使用订阅凭据
- `none`：无需认证的 HTTP 服务

OpenAI API Key 表示 OpenAI API 接入，不等同于 ChatGPT 订阅。ChatGPT Subscription 使用 `oauth_gateway`。

### 能力

- `text_generation`：可分配给高能力模型和高吞吐模型
- `embedding`：只可分配给 Embedding 角色

能力由协议确定：三个文本协议提供 `text_generation`，`openai_embeddings` 提供 `embedding`。

## 数据模型

`model_connections` 的目标字段：

| 字段 | 说明 |
|---|---|
| `connection_id` | 连接 ID |
| `name` | 用户可见名称 |
| `vendor` | 厂商标识 |
| `protocol` | 协议标识 |
| `auth_type` | 认证方式 |
| `capability` | 文本生成或 Embedding |
| `base_url` | HTTP API 基础地址 |
| `api_key_encrypted` | 加密后的 API Key |
| `api_key_masked` | 脱敏显示值 |
| `enabled` | 是否启用 |
| `created_at` | 创建时间 |
| `updated_at` | 更新时间 |

不保留“本地/在线”业务概念。旧表的 `is_local` 字段在迁移后不再读取；SQLite 无需为了删除单列而重建整表，可以保留为废弃物理字段，后续数据库大版本迁移时再清理。

## 旧数据迁移

迁移必须幂等，并在单个 SQLite 事务中完成。

| 旧 `connection_type` | 新厂商 | 新协议 | 新认证 | 新能力 |
|---|---|---|---|---|
| `openai_compatible` | 根据 Base URL 推断，无法推断则 `custom` | `openai_chat_completions` | 有密钥为 `api_key`，否则 `none` | `text_generation` |
| `local_openai` | 根据 Base URL 推断，无法推断则 `custom` | `openai_chat_completions` | 有密钥为 `api_key`，否则 `none` | `text_generation` |
| `embedding` | 根据 Base URL 推断，无法推断则 `custom` | `openai_embeddings` | 有密钥为 `api_key`，否则 `none` | `embedding` |
| `direct_oauth_gateway` | `chatgpt_subscription` | `openai_chat_completions` | `oauth_gateway` | `text_generation` |

Base URL 厂商识别仅匹配明确域名：

- `api.openai.com` → OpenAI
- `api.anthropic.com` → Anthropic
- `api.deepseek.com` → DeepSeek
- `api.moonshot.cn`、`api.moonshot.ai`、`api.kimi.com` → Kimi

迁移不会自动把旧 Chat Completions 连接升级为 Responses，避免破坏现有兼容服务。

## 统一协议适配层

新增协议中立接口，业务代码不直接依赖 OpenAI 或 Anthropic SDK：

```text
TextGenerationClient
├── generate(messages, model, options) -> TextGenerationResult
├── generate_json(messages, model, schema/options) -> object
└── test(model) -> ConnectionTestResult

EmbeddingClient
├── embed(inputs, model, dimensions/options) -> vectors
└── test(model) -> ConnectionTestResult
```

实现类：

- `OpenAIResponsesClient`
- `OpenAIChatCompletionsClient`
- `AnthropicMessagesClient`
- `OpenAIEmbeddingsClient`

统一结果对象包含文本、停止原因、原始模型名、用量和请求 ID。上层不得读取厂商 SDK 的原始响应结构。

## 调用链改造

### MiroFish 业务 LLM

`LLMClient` 根据模型路由返回的 `protocol` 创建对应适配器。现有 JSON 清洗、重试和安全错误逻辑保留在协议中立层上方。

Responses API 优先使用结构化输出能力；Chat Completions 继续使用 `response_format` 能力协商；Anthropic Messages 使用 JSON 提示和现有严格解析器，不假定所有兼容厂商支持 Anthropic 结构化输出扩展。

### Graphiti

Graphiti 当前版本依赖其自身 LLM Client 接口。新增一个桥接实现，将 Graphiti 请求转换到 `TextGenerationClient`，从而让三种文本协议都能用于图谱抽取，不直接依赖 Graphiti 是否内置 Anthropic Client。

Embedding 继续通过 `OpenAIEmbeddingsClient` 接入 Graphiti Embedder 接口。

### OASIS / CAMEL

OASIS 模拟必须使用项目选定的高吞吐模型：

- Anthropic Messages 可优先使用 CAMEL 的 Anthropic 平台支持。
- Chat Completions 可使用 CAMEL 的 OpenAI-compatible 平台支持。
- Responses API 通过项目内协议桥接后端适配为 CAMEL 可消费的模型后端，避免把 Responses 伪装成外部 Chat Completions 服务。

模拟子进程环境增加非敏感协议字段；密钥仍通过现有受控子进程环境传递，不写入模拟配置文件或日志。

## 模型发现与连接测试

模型发现按厂商策略执行：

- OpenAI、Kimi 和兼容厂商可尝试 `GET /models`。
- Anthropic 使用官方模型列表能力；不支持列表的兼容服务允许手工输入模型名。
- DeepSeek 和自定义服务若模型列表不可用，允许手工输入，不把列表失败等同于连接失败。
- ChatGPT Subscription 使用 Gateway 暴露的模型或固定默认模型。

连接测试必须调用所选协议的真实生成端点，不以 `GET /models` 作为成功依据。Embedding 测试调用真实 `/embeddings` 并验证返回向量非空、维度为正。

## 配置中心交互

Provider 表单顺序：

1. 连接名称
2. 厂商或接入方式
3. 协议
4. 认证方式
5. Base URL
6. API Key（认证方式需要时显示）

选择厂商后自动填充默认协议、Base URL 和认证方式；用户仍可修改厂商允许的协议。选择“自定义”时显示全部协议。

页面不显示“在线”“本地”字段。协议和厂商都显示为易读标签，不直接暴露内部枚举值。

测试连接后记录协议、模型、耗时和错误码。失败提示要区分认证失败、端点不存在、协议不匹配、模型不存在和网络错误。

## 安全与持久化

- 所有配置继续存入 `backend/uploads/model-config/models.db`。
- API Key 使用现有 Fernet 密钥加密，接口只返回掩码。
- OAuth 凭据仍只存在 Gateway 的独立凭据卷，MiroFish SQLite 不保存 OAuth Token。
- 请求日志继续脱敏 `api_key`、密码和 Token。
- Base URL 必须使用 HTTP 或 HTTPS；允许内网地址，不以部署位置判断安全性。

## 测试策略

- SQLite 迁移：每种旧类型、重复执行、回滚和已有项目快照。
- 协议契约：三种文本协议的请求结构、文本解析、JSON 解析、错误映射和请求 ID。
- Embedding：批量输入、空向量、维度校验和 OpenAI-compatible 服务。
- 模型路由：厂商、协议、认证、能力与三个模型角色的兼容性。
- Graphiti：三种文本协议桥接和 Embedding 桥接。
- OASIS：三种文本协议的最小模拟模型探测。
- API：创建、更新、测试、删除连接以及密钥不回显。
- 前端：厂商驱动的协议过滤、字段显隐、迁移后旧配置展示和移动端布局。
- 完整回归：后端测试、前端构建、Docker Compose 校验和浏览器端到端验证。

## 不在本次范围

- Cohere、Voyage、Google 等厂商专有 Embedding 协议。
- 图片、音频、Realtime 或 Batch 协议。
- 删除历史设计文档或网关源码资产。
- 将部署位置重新引入模型类型系统。
