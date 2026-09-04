# 已上传文件库与跨推演复用 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立可持久管理的全局上传文件库，并允许新推演任务组合引用已有文件和本地新文件。

**Architecture:** 新增基于统一 SQLite 数据库的 `UploadedFileStore`，负责文件元数据、物理存储、项目引用和旧数据迁移；文件 API 使用独立蓝图，项目创建接口消费文件 ID。前端新增文件管理页和文件选择弹窗，并保持现有 multipart 创建流程兼容。

**Tech Stack:** Python 3.11/3.12、Flask、SQLite、pytest、Vue 3、Vue Router、Vue i18n、Axios、Vite

**Spec:** `docs/superpowers/specs/2026-09-04-uploaded-file-library-design.md`

## Global Constraints

- 仅支持现有配置允许的 PDF、Markdown 和文本文件，上传请求继续受 50MB 上限约束。
- 文件仍被任一项目引用时禁止删除，并返回 HTTP 409 与引用项目摘要。
- 新建推演可混合提交 `file_ids` 与 `files`，合并后至少有一个有效文件。
- 旧项目迁移必须幂等，并保留原项目目录中的物理文件。
- 不引入新前端 UI 组件库，不提供预览、文件夹、标签、版本或权限系统。
- 未获得用户明确提交指令前，不执行计划中的 Git commit 步骤。

---

### Task 1: 文件库持久化模型

**Files:**
- Create: `backend/app/services/uploaded_file_store.py`
- Modify: `backend/app/models/database.py`
- Test: `backend/tests/test_uploaded_file_store.py`

**Interfaces:**
- Consumes: `unified_database_path() -> pathlib.Path`、`Config.UPLOAD_FOLDER`。
- Produces: `UploadedFileStore(database_path: Path | None = None, library_dir: Path | None = None)`；`save_upload(file_storage, display_name: str) -> dict`；`list_files(query: str = "", limit: int = 50, offset: int = 0) -> list[dict]`；`get_file(file_id: str) -> dict | None`；`rename_file(file_id: str, display_name: str) -> dict`；`add_project_references(project_id: str, file_ids: list[str]) -> None`；`remove_project_references(project_id: str) -> None`；`list_references(file_id: str) -> list[dict]`；`delete_file(file_id: str) -> None`。

- [ ] **Step 1: 写入存储层失败测试**

  在 `test_uploaded_file_store.py` 中用临时数据库和 `werkzeug.datastructures.FileStorage` 覆盖：上传元数据及 SHA-256、按名称搜索、重命名、重复项目引用幂等、引用列表、被引用删除抛出 `FileInUseError`、解除引用后物理文件和记录均删除。

- [ ] **Step 2: 运行测试确认失败**

  Run: `cd backend && uv run pytest tests/test_uploaded_file_store.py -q`

  Expected: FAIL，原因是 `app.services.uploaded_file_store` 尚不存在。

- [ ] **Step 3: 实现最小存储层**

  创建 `uploaded_files` 与 `project_files` 表；所有变更使用 SQLite 事务。上传使用 `file_<uuid>` 和服务端生成的 `stored_filename`，流式计算 SHA-256；展示名称仅接受纯文件名和允许扩展名。`delete_file` 在同一写事务中检查引用并删除记录，提交成功后删除物理文件；物理删除失败时回滚数据库变更并抛出存储错误。

- [ ] **Step 4: 将表纳入统一数据库迁移清单**

  在 `database.py` 增加文件库表集合，使旧数据库合并与初始化不会遗漏 `uploaded_files`、`project_files`。

- [ ] **Step 5: 运行存储层测试**

  Run: `cd backend && uv run pytest tests/test_uploaded_file_store.py tests/test_unified_database.py -q`

  Expected: PASS。

- [ ] **Step 6: 经用户授权后提交**

  ```bash
  git add backend/app/services/uploaded_file_store.py backend/app/models/database.py backend/tests/test_uploaded_file_store.py
  git commit -m "feat: add persistent uploaded file store"
  ```

### Task 2: 旧项目文件幂等迁移

**Files:**
- Modify: `backend/app/services/uploaded_file_store.py`
- Modify: `backend/app/models/project.py`
- Test: `backend/tests/test_uploaded_file_migration.py`

**Interfaces:**
- Consumes: `ProjectManager.list_projects(limit=None)`、项目 `files` 兼容快照。
- Produces: `UploadedFileStore.migrate_legacy_projects() -> dict[str, int]`，返回 `migrated`、`linked`、`skipped` 计数。

- [ ] **Step 1: 写入迁移失败测试**

  构造含旧格式 `files: [{filename, size}]` 的项目目录，验证首次迁移复制文件、创建记录与引用并回写 `file_id`；第二次执行不新增记录；物理文件缺失时跳过且不破坏项目；已含 `file_id` 的项目只补引用。

- [ ] **Step 2: 运行测试确认失败**

  Run: `cd backend && uv run pytest tests/test_uploaded_file_migration.py -q`

  Expected: FAIL，原因是迁移方法尚不存在。

- [ ] **Step 3: 实现迁移**

  使用进程内锁保护迁移；`legacy_source` 取 `<project_id>:<saved_filename>`。若旧快照没有 `saved_filename`，按 `ProjectManager.get_project_files()` 的现有顺序和展示名称匹配；无法唯一匹配则记录警告并跳过。复制成功后在事务中写记录和引用，再回写兼容快照。

- [ ] **Step 4: 运行迁移与项目模型测试**

  Run: `cd backend && uv run pytest tests/test_uploaded_file_migration.py tests/test_history_project_mutations.py -q`

  Expected: PASS。

- [ ] **Step 5: 经用户授权后提交**

  ```bash
  git add backend/app/services/uploaded_file_store.py backend/app/models/project.py backend/tests/test_uploaded_file_migration.py
  git commit -m "feat: migrate legacy project uploads"
  ```

### Task 3: 文件管理 HTTP API

**Files:**
- Create: `backend/app/api/files.py`
- Modify: `backend/app/api/__init__.py`
- Modify: `backend/app/__init__.py`
- Test: `backend/tests/test_uploaded_files_api.py`

**Interfaces:**
- Consumes: Task 1 的 `UploadedFileStore` 和 `FileInUseError`。
- Produces: `/api/files` 列表/上传、`/api/files/<file_id>` 重命名/删除、`/download` 下载、`/references` 引用查询。

- [ ] **Step 1: 写入 API 失败测试**

  用 Flask 测试客户端覆盖：分页边界、搜索、批量上传、非法扩展名、空重命名、文件下载名、不存在资源 404、有引用删除 409 及引用项目数据、无引用删除成功。

- [ ] **Step 2: 运行测试确认失败**

  Run: `cd backend && uv run pytest tests/test_uploaded_files_api.py -q`

  Expected: FAIL，文件蓝图未注册或路由返回 404。

- [ ] **Step 3: 实现并注册蓝图**

  创建 `files_bp`，统一返回 `{success, data, error}`；列表限制 `limit` 为 1–200、`offset` 不小于 0；上传接受可重复 `files`；下载使用 `send_from_directory` 和数据库中的固定存储名；将蓝图注册到 `/api/files`。

- [ ] **Step 4: 运行 API 测试**

  Run: `cd backend && uv run pytest tests/test_uploaded_files_api.py tests/test_history_api_contract.py -q`

  Expected: PASS。

- [ ] **Step 5: 经用户授权后提交**

  ```bash
  git add backend/app/api/files.py backend/app/api/__init__.py backend/app/__init__.py backend/tests/test_uploaded_files_api.py
  git commit -m "feat: expose uploaded file management api"
  ```

### Task 4: 项目创建引用已有文件

**Files:**
- Modify: `backend/app/api/graph.py`
- Modify: `backend/app/models/project.py`
- Test: `backend/tests/test_project_file_reuse.py`

**Interfaces:**
- Consumes: `UploadedFileStore.get_file()`、`save_upload()`、`add_project_references()`、`remove_project_references()`。
- Produces: `POST /api/graph/ontology/generate` 的重复表单字段 `file_ids`，以及项目快照中的 `{file_id, filename, size}`。

- [ ] **Step 1: 写入复用流程失败测试**

  mock 本体生成器，只验证项目与文本输入：仅已有文件可创建项目；已有与新文件混合时顺序稳定；空来源返回 400；任一未知 `file_id` 返回 404 且不创建项目；解析失败时项目引用被清理；删除项目解除引用。

- [ ] **Step 2: 运行测试确认失败**

  Run: `cd backend && uv run pytest tests/test_project_file_reuse.py -q`

  Expected: FAIL，创建接口尚不读取 `file_ids`。

- [ ] **Step 3: 调整创建与删除流程**

  在创建接口中先校验已有 ID，再保存新文件；用统一文件记录调用 `FileParser.extract_text()`；项目快照保存 `file_id`。异常路径和项目删除路径调用 `remove_project_references(project_id)`。保留对旧项目 `files` 字段的读取兼容。

- [ ] **Step 4: 运行相关后端测试**

  Run: `cd backend && uv run pytest tests/test_project_file_reuse.py tests/test_history_project_mutations.py tests/test_ontology_api_errors.py -q`

  Expected: PASS。

- [ ] **Step 5: 经用户授权后提交**

  ```bash
  git add backend/app/api/graph.py backend/app/models/project.py backend/tests/test_project_file_reuse.py
  git commit -m "feat: reuse uploaded files in new projects"
  ```

### Task 5: 前端文件管理页

**Files:**
- Create: `frontend/src/api/files.js`
- Create: `frontend/src/views/FileLibraryView.vue`
- Modify: `frontend/src/router/index.js`
- Modify: `frontend/src/views/Home.vue`
- Modify: `locales/zh.json`
- Modify: `locales/en.json`
- Test: `backend/tests/test_file_library_frontend_contract.py`

**Interfaces:**
- Consumes: Task 3 的文件 CRUD、下载和引用查询 API。
- Produces: `listUploadedFiles(params)`、`uploadFiles(formData)`、`renameUploadedFile(fileId, data)`、`deleteUploadedFile(fileId)`、`getUploadedFileReferences(fileId)`、`uploadedFileDownloadUrl(fileId)`，以及 `/files` 路由。

- [ ] **Step 1: 写入前端契约失败测试**

  静态契约断言新 API 导出、`/files` 路由、导航入口、中英文 `fileLibrary` 文案，以及管理页中的搜索、上传、重命名、下载、引用和删除处理函数。

- [ ] **Step 2: 运行测试确认失败**

  Run: `cd backend && uv run pytest tests/test_file_library_frontend_contract.py -q`

  Expected: FAIL，前端文件尚不存在。

- [ ] **Step 3: 实现 API 封装与管理页**

  管理页加载分页列表，使用隐藏 file input 上传；重命名保留合法扩展名；下载链接由当前 API base URL 生成；删除 409 时打开引用项目列表，普通失败显示可恢复错误；操作成功后刷新当前页。

- [ ] **Step 4: 注册路由、导航和国际化文案**

  在首页导航增加文件管理入口，所有新增用户可见文本通过 `vue-i18n` 提供中英文版本。

- [ ] **Step 5: 运行契约测试与前端构建**

  Run: `cd backend && uv run pytest tests/test_file_library_frontend_contract.py -q && cd ../frontend && npm run build`

  Expected: 测试 PASS，Vite build 成功。

- [ ] **Step 6: 经用户授权后提交**

  ```bash
  git add frontend/src/api/files.js frontend/src/views/FileLibraryView.vue frontend/src/router/index.js frontend/src/views/Home.vue locales/zh.json locales/en.json backend/tests/test_file_library_frontend_contract.py
  git commit -m "feat: add uploaded file management page"
  ```

### Task 6: 新建推演选择文件库文件

**Files:**
- Create: `frontend/src/components/FileLibraryPicker.vue`
- Modify: `frontend/src/views/Home.vue`
- Modify: `frontend/src/store/pendingUpload.js`
- Modify: `frontend/src/views/MainView.vue`
- Modify: `frontend/src/views/Process.vue`
- Modify: `locales/zh.json`
- Modify: `locales/en.json`
- Test: `backend/tests/test_file_reuse_frontend_contract.py`

**Interfaces:**
- Consumes: `listUploadedFiles()`；`generateOntology(formData)` 的 `file_ids` 字段。
- Produces: `setPendingUpload(files, requirement, uploadedFileIds)`；选择器通过 `v-model` 返回 `string[]`。

- [ ] **Step 1: 写入选择与提交契约失败测试**

  断言选择器支持搜索和多选；首页允许本地文件或已有文件满足提交条件；pending store 持久化 `uploadedFileIds`；`MainView.vue` 和兼容的 `Process.vue` 向 FormData 逐个 append `file_ids`。

- [ ] **Step 2: 运行测试确认失败**

  Run: `cd backend && uv run pytest tests/test_file_reuse_frontend_contract.py -q`

  Expected: FAIL，选择器和 `uploadedFileIds` 尚不存在。

- [ ] **Step 3: 实现文件选择器与首页组合选择**

  选择器显示名称、大小、日期与引用数，支持搜索、多选、确认和取消；首页统一展示本地文件与已选库文件，并允许分别移除。重复选择同一 `file_id` 时去重。

- [ ] **Step 4: 扩展 pending store 与创建请求**

  `pendingUpload` 增加 `uploadedFileIds`；两个项目创建视图逐个追加 `file_ids`，继续逐个追加本地 `files`，成功后统一清理 pending 状态。

- [ ] **Step 5: 运行前端契约与构建**

  Run: `cd backend && uv run pytest tests/test_file_reuse_frontend_contract.py -q && cd ../frontend && npm run build`

  Expected: 测试 PASS，Vite build 成功。

- [ ] **Step 6: 经用户授权后提交**

  ```bash
  git add frontend/src/components/FileLibraryPicker.vue frontend/src/views/Home.vue frontend/src/store/pendingUpload.js frontend/src/views/MainView.vue frontend/src/views/Process.vue locales/zh.json locales/en.json backend/tests/test_file_reuse_frontend_contract.py
  git commit -m "feat: select uploaded files for simulations"
  ```

### Task 7: 全量回归与交付检查

**Files:**
- Modify: `README.md`
- Modify: `README-EN.md`

**Interfaces:**
- Consumes: Tasks 1–6 的所有用户功能。
- Produces: 文件库使用说明和最终验证记录。

- [ ] **Step 1: 更新用户文档**

  在中英文 README 的功能和使用部分说明：文件上传后进入全局文件库、创建推演可复用、被引用文件不能删除，以及 Docker 持久化仍依赖 `backend/uploads/`。

- [ ] **Step 2: 运行后端完整测试**

  Run: `cd backend && uv run pytest -q`

  Expected: 全部 PASS，无新增 warning 或资源泄漏错误。

- [ ] **Step 3: 运行前端生产构建**

  Run: `cd frontend && npm run build`

  Expected: 构建成功并生成 `frontend/dist/`。

- [ ] **Step 4: 检查变更质量**

  Run: `git diff --check && git status --short && git diff --stat`

  Expected: `git diff --check` 无输出；状态只包含本功能相关源文件、测试和文档，构建产物未进入版本控制。

- [ ] **Step 5: 经用户授权后提交**

  ```bash
  git add README.md README-EN.md docs/superpowers/specs/2026-09-04-uploaded-file-library-design.md docs/superpowers/plans/2026-09-04-uploaded-file-library.md
  git commit -m "docs: document uploaded file reuse"
  ```
