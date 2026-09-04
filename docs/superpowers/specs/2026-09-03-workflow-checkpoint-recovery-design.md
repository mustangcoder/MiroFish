# 全流程检查点与异常恢复设计

## 目标

让 MiroFishPlus 的四个主流程阶段在页面刷新、后端重启或 Docker 重启后，都能从最近的安全检查点自动恢复，而不是重复整个阶段：

1. 本体生成与图谱构建
2. 人设与模拟配置生成
3. OASIS 模拟与 Graphiti 动作写入
4. 报告生成

恢复保证是“已完成工作不重复、从最近安全边界继续”，不是对进程内存做快照。模拟恢复后的未来输出不保证与从未中断的运行逐 Token 完全一致。

## 恢复语义

### 页面刷新或离开后返回

- 不创建重复任务。
- 读取持久化工作流状态并进入最近节点。
- 继续轮询原 `task_id`。
- 展示检查点位置，例如“已恢复 58/150 个人设”“从第 21/40 轮继续”“复用 3/5 个报告章节”。

### 后端或 Docker 重启

- 启动时扫描可恢复任务。
- 通过 SQLite lease 保证每个资源只有一个恢复执行器。
- 原任务从 `interrupted` 恢复为 `processing`，继续使用原 `task_id`。
- 已完成检查点不回退；状态文件与 SQLite 不一致时，以通过完整性校验的最保守位置为准。
- 找不到项目、模拟、图谱或必要输入时，将任务标记为明确失败，不阻止其他任务恢复。

## 统一工作流状态

在 `mirofishplus.db` 增加两张通用表：

### `workflow_runs`

| 字段 | 说明 |
|------|------|
| `run_id` | 工作流运行 ID |
| `resource_type` | `project`、`simulation` 或 `report` |
| `resource_id` | 对应资源 ID |
| `task_id` | 原后台任务 ID |
| `stage` | `ontology`、`graph`、`prepare`、`simulation`、`graph_ingestion`、`report` |
| `status` | `pending`、`running`、`recovering`、`completed`、`failed`、`superseded` |
| `input_fingerprint` | 输入和配置摘要，用于防止错误复用 |
| `checkpoint_json` | 当前阶段的结构化检查点 |
| `lease_owner` | 当前进程唯一标识 |
| `lease_expires_at` | lease 到期时间 |
| `heartbeat_at` | 最近心跳 |
| `recovery_count` | 自动恢复次数 |
| `error` | 安全错误摘要 |
| 时间字段 | 创建、更新时间和完成时间 |

同一 `resource_type/resource_id/stage` 只允许一个活动运行。lease 使用条件更新获取，过期后才允许其他进程接管。

### `workflow_checkpoint_events`

保存只追加的检查点事件：运行 ID、序号、检查点类型、payload、创建时间。该表用于审计、恢复回退和排查，不能在正常启动时清理。

现有 `simulation_prepare_runs` 和 `simulation_prepare_profiles` 暂时保留，准备阶段通过适配器同步通用工作流状态，避免一次迁移改变已验证的人设恢复逻辑。

## 阶段一：本体与图谱

### 本体生成

本体生成没有可安全拆分的内部 LLM 边界，因此使用“阶段级自动重试”：

- 持久化项目 ID、文档摘要、模型配置快照和任务 ID。
- 完成后保存本体及输入指纹，标记检查点完成。
- 重启时若项目尚无有效本体，则用相同输入重新执行。
- 已保存且指纹一致的本体直接复用。

### 图谱构建

将文本切块结果稳定编号，并持久化：

- 语料指纹、块总数与块摘要。
- 每个提交批次的起止块、内容摘要、后端、远端 batch/operation ID。
- `pending/submitted/processed/failed` 状态。
- 已确认的 Episode UUID 或确定性 Episode ID。

Zep Cloud 恢复时继续轮询已提交 batch；本地 Graphiti 使用确定性 Episode ID 查询是否存在，只提交缺失批次。若结果不明确，则先查询再决定，禁止盲目重放。

## 阶段二：人设与配置

沿用现有逐人设事务检查点，并增加配置检查点：

- 人设按实体 UUID 和固定 `user_id` 恢复。
- 全部人设完成后记录 Profile 文件摘要。
- 模拟配置生成成功后保存配置摘要、模型快照版本和完成事件。
- 若在配置阶段中断，重启后直接复用人设并只重新生成配置。
- 启动恢复继续由 `SimulationPreparationRunner` 执行，但 lease 由通用工作流存储控制。

## 阶段三：模拟与图谱写入

### 轮次边界

每个平台只在一轮完整结束后提交检查点：

- `completed_round`
- OASIS 数据库路径、大小和 SQLite `integrity_check`
- actions JSONL 的字节偏移和最后事件摘要
- 模拟配置摘要和 Profile 摘要
- 每轮确定性随机种子
- 已提交图谱批次的内容摘要与 Episode ID

写入顺序是：完成平台数据库事务 → 刷新并 fsync actions 日志 → 保存轮次检查点。中断发生在顺序中间时，该轮视为未完成，恢复时从上一轮重新执行。

### OASIS 恢复

脚本增加 `--resume-from-round N`：

- 打开已有 Twitter/Reddit SQLite，不重复初始化账号、帖子或关注关系。
- 从 Profile 和配置重建 Agent 对象。
- 从数据库及历史动作生成有界记忆摘要，作为恢复上下文。
- 使用 `simulation_id + platform + round` 派生随机种子。
- 从 `N + 1` 轮继续执行。

无法稳定序列化的 CAMEL/OASIS Python 对象和模型内部上下文不写入磁盘。因此恢复后已完成轮次和数据库状态保持一致，但未来生成文本允许变化。

### Graphiti 动作写入

每个 Episode 使用以下字段生成确定性摘要：模拟 ID、平台、轮次、动作范围和正文摘要。SQLite 保存：

- `pending/writing/written/failed`
- Episode ID、尝试次数和最近错误
- actions 日志起止偏移

启动时扫描 `pending/writing/failed-retryable`，先按 Episode ID 或正文摘要查询图谱；存在则确认完成，不存在才补写。确定的 429/熔断可以退避重试，结果不明确的网络错误必须先查后写。

## 阶段四：报告生成

报告恢复以文件和 SQLite 双重校验：

- 大纲生成完成后保存大纲摘要。
- 每章保存到临时文件，完成后原子替换 `section_NN.md`，并记录内容摘要。
- 恢复时加载原大纲，按章节序号验证文件摘要。
- 已验证章节按原顺序加载为后续章节上下文，只生成缺失或损坏章节。
- 全部章节完成后重新组装 `full_report.md`。
- 如果大纲未完成，只重新规划大纲；如果输入指纹变化，创建新报告运行并将旧运行标记为 `superseded`。

服务启动时 `ReportGenerationRunner` 自动恢复未完成报告，复用原 `report_id` 和 `task_id`。

## 启动恢复协调器

新增 `WorkflowRecoveryCoordinator`，在数据库和配置初始化完成、Flask 开始接收请求前运行：

1. 将未过期但属主进程不存在的 lease 标记为可接管。
2. 按 `ontology → graph → prepare → simulation → graph_ingestion → report` 顺序扫描。
3. 为每个资源获取 lease 后启动守护线程。
4. 单个任务失败只更新自身状态。
5. 定期刷新 heartbeat；正常完成后释放 lease。
6. 应用关闭只停止接收新任务并保存最新安全检查点，不伪造完成状态。

恢复并发默认限制为 2；同一图谱或同一模拟的任务串行，避免恢复时压垮模型 Provider。

## API 与页面

现有接口保持兼容，状态响应增加：

- `recovering`
- `recovery_count`
- `checkpoint_stage`
- `checkpoint_current`
- `checkpoint_total`
- `checkpoint_message`

页面将 `recovering` 视为活动状态，显示恢复来源和进度，不自动创建新任务。历史记录根据工作流检查点选择最近可进入节点。

## 安全与数据完整性

- 检查点不保存 API Key、OAuth Token、完整模型请求或完整采访内容。
- 错误字段只保存安全摘要。
- SQLite 使用 WAL、busy timeout 和短事务。
- JSONL 偏移必须落在完整换行边界。
- 写入 Profile、配置、章节和状态文件使用临时文件 + 原子替换。
- 不删除旧检查点、旧报告章节、OASIS 数据库或动作日志。

## 验收标准

为每个阶段构造 Docker 重启测试：

1. 本体生成中断后自动重试并进入图谱构建。
2. 图谱第 N 批中断后只处理缺失批次。
3. 人设第 N 个中断后只生成剩余人设；配置中断后不重生人设。
4. 模拟第 N 轮中断后从 N+1 轮继续，OASIS 数据库无重复初始化数据。
5. Graphiti 写入中断后只补写缺失 Episode。
6. 报告第 N 章中断后复用前 N 章。
7. 页面刷新和历史记录均进入最新节点。
8. 任意任务最多只有一个有效 lease 和一个执行线程。

所有恢复测试必须同时验证原 `task_id`、项目/模拟/报告 ID 不变，已完成检查点没有重复副作用。
