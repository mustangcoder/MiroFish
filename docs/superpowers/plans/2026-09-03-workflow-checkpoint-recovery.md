# Workflow Checkpoint Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让本体、图谱、准备、模拟、图谱写入和报告阶段在服务重启后从最近安全检查点自动恢复。

**Architecture:** `WorkflowRunStore` 在 `mirofishplus.db` 保存活动运行、lease、心跳和只追加事件；`WorkflowRecoveryCoordinator` 在启动时按依赖顺序恢复。各业务运行器只在自身安全边界提交检查点，并复用原 task/resource ID。

**Tech Stack:** Python 3.11/3.12、SQLite WAL、Flask、OASIS/CAMEL、Graphiti/Neo4j、Vue 3、pytest。

**Spec:** `docs/superpowers/specs/2026-09-03-workflow-checkpoint-recovery-design.md`

## Global Constraints

- 所有检查点写入 `backend/uploads/mirofishplus.db`。
- 不保存 API Key、OAuth Token 或完整模型请求。
- 不删除旧检查点、报告章节、OASIS 数据库和动作日志。
- 恢复复用原 task/resource ID，已完成工作不得重复。
- 模拟只保证轮次边界恢复，不保证未来输出逐 Token 一致。

---

### Task 1: 通用运行、事件与 lease 存储

**Files:**
- Create: `backend/app/services/workflow_run_store.py`
- Create: `backend/tests/test_workflow_run_store.py`
- Modify: `backend/scripts/bootstrap_local.py`

**Interfaces:**
- `create_or_get_run(resource_type, resource_id, task_id, stage, input_fingerprint, checkpoint) -> dict`
- `acquire_lease(run_id, owner, ttl_seconds) -> bool`
- `heartbeat(run_id, owner, checkpoint=None) -> bool`
- `append_checkpoint(run_id, event_type, payload) -> int`
- `complete/fail/supersede/release_lease`
- `list_recoverable() -> list[dict]`

- [ ] 写失败测试：活动唯一性、并发 lease、过期接管、事件序号、恢复列表和敏感键拒绝。
- [ ] 实现两张表、部分唯一索引和 `BEGIN IMMEDIATE` 条件更新。
- [ ] 将表加入 bootstrap 校验并运行聚焦测试。

### Task 2: 恢复协调器与任务复活

**Files:**
- Create: `backend/app/services/workflow_recovery_coordinator.py`
- Create: `backend/tests/test_workflow_recovery_coordinator.py`
- Modify: `backend/app/__init__.py`
- Modify: `backend/app/models/task.py`

**Interfaces:**
- `register(stage, handler)`
- `recover_pending() -> RecoverySummary`
- `TaskManager.revive_task(task_id, message, progress_detail)`

- [ ] 写失败测试：阶段顺序、单任务失败隔离、原 task ID 复活和重复协调器不能双启动。
- [ ] 实现协调器、默认并发 2 和启动注册。
- [ ] 验证正常启动不会重跑已完成运行。

### Task 3: 本体与图谱批次恢复

**Files:**
- Modify: `backend/app/api/graph.py`
- Modify: `backend/app/services/graph_builder.py`
- Create: `backend/tests/test_graph_workflow_recovery.py`

**Interfaces:**
- 本体 checkpoint 保存输入指纹和项目参数。
- 图谱 checkpoint 保存块摘要、批次范围、远端 ID 和状态。
- `resume_graph_workflow(run) -> None`

- [ ] 写失败测试：本体重启自动重试、Zep 已提交 batch 继续轮询、Graphiti 只提交缺失确定性 Episode。
- [ ] 接入通用 Store，并在每批确认后提交事件。
- [ ] 注册 ontology/graph 恢复处理器并运行聚焦测试。

### Task 4: 准备配置检查点整合

**Files:**
- Modify: `backend/app/services/simulation_preparation_runner.py`
- Modify: `backend/app/services/simulation_manager.py`
- Modify: `backend/tests/test_simulation_preparation_runner.py`

**Interfaces:**
- 现有逐人设表保留。
- 通用 checkpoint 区分 `profiles`、`config`、`completed`。

- [ ] 写失败测试：配置阶段中断后复用全部人设，只重新生成配置。
- [ ] 用通用 lease 包装现有准备执行器。
- [ ] 验证旧准备记录仍可恢复。

### Task 5: OASIS 轮次边界恢复

**Files:**
- Modify: `backend/scripts/run_parallel_simulation.py`
- Modify: `backend/scripts/run_twitter_simulation.py`
- Modify: `backend/scripts/run_reddit_simulation.py`
- Modify: `backend/app/services/simulation_runner.py`
- Create: `backend/tests/test_simulation_round_recovery.py`

**Interfaces:**
- CLI `--resume-from-round N`。
- `round_checkpoint.json` 保存完整轮次、日志偏移、数据库完整性和摘要。
- `SimulationRunner.resume_simulation(simulation_id)`。

- [ ] 写失败测试：现有数据库不重复初始化、从 N+1 轮执行、截断不完整 JSONL 尾部、原 task ID 保持。
- [ ] 每轮按数据库事务→日志 fsync→原子 checkpoint 顺序提交。
- [ ] 启动时重建 Agent 与有界历史摘要，注册 simulation 恢复处理器。
- [ ] 使用小型双平台模拟执行进程重启测试。

### Task 6: Graphiti 动作写入恢复

**Files:**
- Modify: `backend/app/services/zep_graph_memory_updater.py`
- Modify: `backend/app/services/zep_graphiti_impl.py`
- Modify: `backend/scripts/reconcile_simulation_graph.py`
- Create: `backend/tests/test_graph_ingestion_checkpoint.py`

**Interfaces:**
- 确定性 `episode_id`。
- SQLite batch 状态 `pending/writing/written/failed_retryable/failed_ambiguous`。
- `resume_graph_ingestion(run) -> None`。

- [ ] 写失败测试：成功不重复、429 自动补写、模糊失败先查后写、服务重启恢复 pending。
- [ ] 将现有内存失败列表改为 SQLite 事实来源。
- [ ] 注册 graph_ingestion 恢复处理器并验证最终完成屏障。

### Task 7: 报告章节恢复

**Files:**
- Create: `backend/app/services/report_generation_runner.py`
- Modify: `backend/app/services/report_agent.py`
- Modify: `backend/app/api/report.py`
- Create: `backend/tests/test_report_checkpoint_recovery.py`

**Interfaces:**
- `ReportAgent.generate_report(..., resume=True)`。
- 大纲和章节 SHA-256 检查点。
- `ReportGenerationRunner.start/recover_pending/wait`。

- [ ] 写失败测试：复用大纲、跳过摘要匹配章节、损坏章节重生、原 report/task ID 恢复。
- [ ] 原子保存章节并从已验证章节构造上下文。
- [ ] 抽取 API 后台线程为运行器并注册 report 恢复处理器。

### Task 8: 页面状态与完整验证

**Files:**
- Modify: `frontend/src/components/Step2EnvSetup.vue`
- Modify: `frontend/src/components/Step3Simulation.vue`
- Modify: `frontend/src/views/Process.vue`
- Modify: `frontend/src/views/ReportView.vue`
- Modify: `locales/zh.json`
- Modify: `locales/en.json`
- Modify: `README.md`
- Modify: `README-EN.md`

- [ ] 页面识别 `recovering`，显示检查点阶段/current/total/recovery_count 并复用原任务。
- [ ] 运行后端、Gateway 全量测试和前端构建。
- [ ] 对每个阶段执行 Docker 重启探针，验证无重复副作用。
- [ ] 运行 `git diff --check`，记录仍无法恢复的第三方状态限制。
