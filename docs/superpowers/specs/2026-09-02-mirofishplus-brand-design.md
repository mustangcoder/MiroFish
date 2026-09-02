# MiroFishPlus 品牌与数据库迁移设计

## 目标

将本项目的对外品牌从 `MiroFish-Local` 统一为 `MiroFishPlus`，体现其能力已经覆盖本地图谱、模型协议、OAuth、持久化恢复和可靠性增强，而不只是“本地版”。同时将本项目独有的统一 SQLite 数据库从 `mirofish.db` 改为 `mirofishplus.db`，并自动、无损兼容现有安装。

本项目明确同时基于 [官方 MiroFish](https://github.com/666ghj/MiroFish) 与社区项目 [tt-a1i/MiroFish-local](https://github.com/tt-a1i/MiroFish-local)，不代表任一上游维护团队。Graphiti + Neo4j、本地/云端双模式和本地部署基础归功于 MiroFish-local；MiroFishPlus 在此基础上继续实现模型协议、OAuth、持久化恢复、本体类型和可靠性增强。

## 官方数据库边界

截至 2026-09-02，官方 `main` 源码未使用 `mirofish.db`。官方仍使用 OASIS 每个模拟目录内的：

- `twitter_simulation.db`
- `reddit_simulation.db`

这两个 OASIS 数据库不改名、不迁移。`mirofish.db` 是本项目新增的配置、任务和准备检查点数据库，因此可安全调整品牌名称。

## 改名范围

### 对外统一为 MiroFishPlus

- 中文、英文 README 的标题、介绍、差异说明和示例命令。
- 前端浏览器标题、页面可见品牌文案和 Logo 替代文本。
- `package.json` 的描述性元数据。
- Demo、贡献指南和面向用户的启动输出。
- Flask 健康检查中的服务显示名和启动日志显示名。
- 新数据库文件名 `mirofishplus.db`。

### 为兼容性保留 mirofish

- Python 包路径 `app` 及现有模块结构。
- `/api/...` HTTP 路径和 JSON 字段。
- 日志 logger 名称，例如 `mirofish.simulation`。
- 环境变量名称，例如 `MIROFISH_*`、`DIRECT_GATEWAY_TOKEN`。
- 已发布的图谱 ID、项目 ID、模拟 ID 和报告 ID 前缀。
- OASIS 的 `twitter_simulation.db`、`reddit_simulation.db`。

仓库托管平台上的仓库名不由代码修改。README 使用当前真实仓库地址，待仓库实际重命名后再更新链接，避免提前引用不存在的地址。

## 数据库迁移

新默认路径为：

```text
backend/uploads/mirofishplus.db
```

旧路径为：

```text
backend/uploads/mirofish.db
```

启动初始化按以下顺序执行：

1. 如果 `mirofishplus.db` 不存在且 `mirofish.db` 存在，使用 Python SQLite Backup API 将旧库一致性复制为新库。
2. 在新库中校验 `PRAGMA integrity_check` 返回 `ok`。
3. 比较新旧库所有用户表的行数；不一致则报错并阻止应用启动。
4. 在新库的 `app_schema_migrations` 写入 `legacy_mirofish_database_v1` 标记。
5. 保留旧 `mirofish.db`、WAL/SHM 文件及所有内容，不删除、不重命名。
6. 继续执行现有 `model-config/models.db` 和 `tasks/tasks.db` 幂等导入，以及当前表结构升级。

如果新旧数据库同时存在，始终以 `mirofishplus.db` 为准，不自动合并或覆盖。这样可以避免旧库在迁移后继续残留时反向覆盖新数据。

迁移函数必须在任何 Store 创建新数据库文件之前执行。Flask 应用工厂和 `bootstrap_local.py` 都调用同一个迁移入口，避免 Docker 与源码启动行为不同。

## Docker 名称与卷迁移

Docker 对外名称全部改为 MiroFishPlus：

| 类型 | 旧名称 | 新名称 |
|------|--------|--------|
| Compose 项目 | `mirofish` | `mirofishplus` |
| 主容器 | `mirofish` | `mirofishplus` |
| 初始化容器 | `mirofish-bootstrap` | `mirofishplus-bootstrap` |
| OAuth Gateway 容器 | `mirofish-direct-oauth-gateway` | `mirofishplus-direct-oauth-gateway` |
| Hugging Face 预下载容器 | `mirofish-hf-prefetch` | `mirofishplus-hf-prefetch` |
| Neo4j 容器 | `mirofish-neo4j` | `mirofishplus-neo4j` |
| OAuth 凭据卷 | `mirofish_direct_oauth_credentials` | `mirofishplus_direct_oauth_credentials` |
| Hugging Face 缓存卷 | `mirofish_huggingface_cache` | `mirofishplus_huggingface_cache` |
| Neo4j 数据卷 | `mirofish_neo4j_data` | `mirofishplus_neo4j_data` |
| Neo4j 日志卷 | `mirofish_neo4j_logs` | `mirofishplus_neo4j_logs` |
| 生产上传卷 | `mirofish_mirofish_uploads` | `mirofishplus_uploads` |
| 生产 Embedding 缓存卷 | `mirofish_embedding_cache` | `mirofishplus_embedding_cache` |

Docker 没有直接重命名 Volume 的操作。`npm run docker:up` 在启动新项目之前执行一次兼容迁移：

1. 检查旧容器是否存在；存在时执行 `docker stop`，释放 3000、5001、7474 和 7687 端口，但不删除旧容器。
2. 对每组旧卷和新卷执行检查：
   - 旧卷不存在：创建或使用空的新卷，视为新安装。
   - 新卷不存在、旧卷存在：创建新卷，使用临时 Alpine 容器从只读旧卷复制全部内容。
   - 新卷已存在且带迁移完成标记：直接复用，不重复复制。
   - 新卷已有内容但没有完成标记：停止启动并提示人工检查，禁止覆盖。
3. 复制完成后比较源卷与目标卷的文件数量和总字节数；一致后写入 `.mirofishplus_migration_complete` 标记。
4. 启动 `mirofishplus` Compose 项目和新容器。
5. 旧容器、旧卷和其中数据永久保留，不自动删除。

Neo4j 在停止旧容器后复制，以避免复制过程中数据库继续写入。OAuth 凭据卷迁移确保已有 ChatGPT Subscription 登录态可继续使用；Hugging Face 缓存卷迁移避免重新下载大模型。首次迁移会临时额外占用与旧卷数据量近似的磁盘空间。

临时迁移容器仅用于复制，成功或失败后都可安全移除；数据卷本身不删除。卷迁移过程不读取或输出凭据内容。

## 启动与显示行为

- `npm run docker:up` 命令保持不变，避免破坏现有使用习惯。
- 启动成功文案改为 `MiroFishPlus 已启动`。
- 健康检查返回的 `service` 改为 `MiroFishPlus Backend`。
- Docker 项目、容器和卷统一使用 `mirofishplus` 前缀；首次启动先迁移并保留旧 Docker 数据。
- 首次迁移成功时日志只显示源路径、目标路径和表数量，不输出密钥或业务数据。

## README 调整

现有“与官方项目的区别”章节保留并更新：

- 项目列名由 `MiroFish-Local` 改为 `MiroFishPlus`。
- 统一数据库路径改为 `backend/uploads/mirofishplus.db`。
- 增加旧数据库自动迁移说明。
- 增加旧 Docker 容器停止、新卷复制和额外磁盘占用说明。
- 删除“完全本地/完全离线”等绝对说法，继续明确模型 Provider 可能是远程 HTTP 服务。
- 仓库链接使用当前真实地址，不假设托管平台已经完成重命名。

## 测试与验收

自动测试覆盖：

- 新安装默认创建 `mirofishplus.db`。
- 只有旧库时完整复制所有表和数据，旧文件继续存在。
- 迁移可重复执行，不重复数据。
- 新旧库同时存在时不覆盖新库。
- Flask 和本地 bootstrap 均使用新路径。
- OASIS 数据库名称保持不变。
- 对外文档和页面不再出现 `MiroFish-Local`。
- Compose 项目、容器和卷均使用 `mirofishplus` 前缀。
- 旧 Docker 卷复制后文件数量和总字节数一致，且旧卷仍存在。

端到端验收在现有 `backend/uploads/mirofish.db` 的副本和测试卷上执行，不直接破坏用户数据库。验证迁移后模型连接、任务、图谱配置和环境准备检查点行数一致；验证 Docker 卷文件计数与字节数一致；最后启动新 Compose 项目，确认应用读取 `mirofishplus.db`。旧容器和旧卷不得删除。
