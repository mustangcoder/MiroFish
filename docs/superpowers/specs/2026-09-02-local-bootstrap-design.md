# 本地一键启动与数据库初始化设计

## 目标

提供一个稳定、幂等且容易排错的本地 Docker 启动入口。开发者执行：

```bash
npm run docker:up
```

即可完成环境文件准备、依赖服务启动、MiroFish 数据库初始化、应用启动和健康检查。流程不得删除数据库、上传文件、Docker 卷或其他用户数据。

## 范围

本次包含：

- 自动创建缺失的 `.env`。
- 统一组合主 Compose 文件和本地 Neo4j Compose 文件。
- 启动 Neo4j 与 Direct OAuth Gateway，并等待健康检查。
- 通过一次性初始化容器创建和迁移 `mirofish.db`。
- 初始化模型配置、任务历史、图谱服务配置和环境准备检查点所需表。
- 幂等导入旧 `model-config/models.db` 与 `tasks/tasks.db`，保留源文件。
- 初始化环境变量提供的必要默认配置。
- 初始化完成后启动 MiroFish，并等待 HTTP 健康检查。
- 输出访问地址和服务状态；失败时输出具体阶段和诊断命令。

本次不包含：

- 自动下载或填写 API Key。
- 删除、覆盖或重置现有配置。
- 自动启动一项推演任务。
- 修改 OASIS 独立的 Twitter/Reddit SQLite 数据库。
- 替代现有 `npm run dev` 非 Docker 开发流程。

## 入口与用户体验

根目录 `package.json` 新增：

```json
{
  "scripts": {
    "docker:up": "bash scripts/start-local.sh"
  }
}
```

`scripts/start-local.sh` 是面向用户的唯一编排入口：

1. 检查 `docker`、`docker compose` 是否可用。
2. 若 `.env` 不存在，从 `.env.example` 复制并提示用户已创建。
3. 确保 `backend/uploads/` 存在，但不清理其中内容。
4. 使用以下 Compose 组合构建并启动：

   ```bash
   docker compose -f docker-compose.yml -f docker-compose.local.yml up -d --build
   ```

5. 等待一次性初始化服务成功退出。
6. 等待 `http://localhost:5001/health` 成功。
7. 打印前端、后端、Neo4j Browser 地址。

脚本使用严格错误模式。失败时保留现场，并打印失败服务的状态及对应日志查看命令，不执行自动清理或回滚。

## 初始化服务

新增后端 CLI：

```bash
uv run --project backend python backend/scripts/bootstrap_local.py
```

该命令只执行初始化，不启动 Flask：

1. 创建 `backend/uploads/mirofish.db` 的父目录。
2. 创建 ModelConfigStore 和 TaskStore 的当前表结构。
3. 执行统一数据库的旧库幂等迁移。
4. 再次运行模型表的版本迁移，确保导入的旧记录升级到当前结构。
5. 初始化环境准备检查点表。
6. 当图谱后端配置尚不存在时，从环境变量写入默认配置；已有配置保持不变。
7. 校验关键表存在且 SQLite 可执行读写事务。
8. 输出不含密钥的 JSON 摘要并以状态码 `0` 退出。

任何步骤失败时以非零状态码退出，Compose 不得启动主应用。

## Compose 依赖顺序

主 Compose 新增一次性服务 `bootstrap`，复用 MiroFish 镜像和 uploads 挂载：

```text
Neo4j healthy ─┐
               ├─> bootstrap completed successfully ─> mirofish healthy
Gateway healthy┘
```

约束如下：

- `bootstrap` 使用 `restart: "no"`。
- `bootstrap` 和 `mirofish` 挂载同一个 `./backend/uploads:/app/backend/uploads`。
- `mirofish.depends_on.bootstrap.condition` 使用 `service_completed_successfully`。
- 本地覆盖文件为 `bootstrap` 和 `mirofish` 注入容器内 Neo4j 地址 `bolt://neo4j:7687`。
- Neo4j 版本继续统一使用项目当前的 `5.26.0`。
- MiroFish 增加 HTTP healthcheck，供启动脚本确认最终可用状态。

## 配置策略

`.env` 自动创建后允许应用继续启动，因为模型与图谱配置可以在配置中心补充。初始化阶段只校验格式和数据库可用性，不要求所有模型凭据已经填写。

默认数据遵循“仅在不存在时创建”原则：

- 已存在的模型 Provider、模型职责和图谱后端配置不覆盖。
- 环境变量只作为首次初始化来源。
- 密钥继续通过现有 CredentialCipher 加密后写入 SQLite。
- 旧库迁移标记继续使用 `app_schema_migrations`，保证重复启动不重复导入。

## 错误处理

- Docker 不可用：启动前立即退出并提示启动 Docker Desktop。
- `.env.example` 缺失：不创建空文件，直接退出。
- Neo4j/Gateway 健康检查失败：Compose 保持现场，主服务不启动。
- SQLite 迁移失败：`bootstrap` 非零退出，主服务不启动，旧数据库保持不变。
- MiroFish 健康检查超时：打印 `docker compose ps` 和日志查看命令，不删除容器。
- 重复执行：重新构建有变更的镜像，复用健康依赖和持久化数据。

## 测试与验收

自动测试包括：

- 缺失 `.env` 时从模板创建，已有 `.env` 不覆盖。
- 初始化 CLI 在空 uploads 目录创建全部关键表。
- 初始化 CLI 可重复运行且行数不增加。
- 旧模型库和任务库成功导入并保留。
- 已有图谱后端配置不被环境变量覆盖。
- Compose 配置包含正确的健康依赖和共享挂载。
- 启动脚本在 Docker 不可用、初始化失败和健康超时时返回非零状态。

端到端验收：

1. 在保留现有数据的前提下执行 `npm run docker:up`。
2. 确认 Neo4j、Gateway、MiroFish 均健康。
3. 确认 `bootstrap` 以状态码 `0` 完成。
4. 确认 `mirofish.db` 包含模型、任务和准备检查点表。
5. 再次执行同一命令，确认配置及数据行数保持一致。
6. 确认前端 `http://localhost:3000`、后端 `/health` 和 Neo4j Browser 可访问。
