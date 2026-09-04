"""已上传文件及其项目引用的 SQLite 持久化存储。"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..config import Config
from ..models.project import ProjectManager
from ..models.database import initialize_file_library_tables, unified_database_path


class FileInUseError(RuntimeError):
    """文件仍被项目引用，不能删除。"""

    def __init__(self, file_id: str, references: list[dict]) -> None:
        self.references = references
        super().__init__(f"文件 {file_id} 仍被项目引用")


class FileStorageError(RuntimeError):
    """文件系统与数据库无法保持一致。"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


_FILE_COLUMNS = (
    "file_id, display_name, stored_filename, extension, size, sha256, "
    "created_at, updated_at, legacy_source"
)

_legacy_project_migration_lock = threading.Lock()
_legacy_migration_gate_lock = threading.RLock()
_completed_legacy_migrations: dict[tuple[str, str, str], dict[str, int]] = {}
_active_legacy_migrations = threading.local()
logger = logging.getLogger(__name__)


class UploadedFileStore:
    """持久化用户上传文件、物理内容和项目引用。"""

    def __init__(
        self,
        database_path: Path | None = None,
        library_dir: Path | None = None,
    ) -> None:
        self.database_path = Path(database_path or unified_database_path())
        self.library_dir = Path(library_dir or Path(Config.UPLOAD_FOLDER) / "library")
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.library_dir.mkdir(parents=True, exist_ok=True)
        self._initialize()
        self._recover_delete_operations()
        self._recover_project_delete_operations()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            try:
                connection.execute("BEGIN IMMEDIATE")
                initialize_file_library_tables(connection)
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def _finalize_delete_operation(
        self,
        connection: sqlite3.Connection,
        file_id: str,
    ) -> None:
        """在调用方持有写事务时推进一个持久删除操作。"""
        operation = connection.execute(
            """
            SELECT d.tombstone_filename, f.stored_filename
            FROM uploaded_file_delete_operations AS d
            JOIN uploaded_files AS f ON f.file_id=d.file_id
            WHERE d.file_id=?
            """,
            (file_id,),
        ).fetchone()
        if operation is None:
            return

        references = self._with_project_summaries([
            dict(row)
            for row in connection.execute(
                "SELECT project_id, position FROM project_files WHERE file_id=? ORDER BY project_id",
                (file_id,),
            ).fetchall()
        ])
        if references:
            connection.execute(
                "DELETE FROM uploaded_file_delete_operations WHERE file_id=?",
                (file_id,),
            )
            raise FileInUseError(file_id, references)

        stored_path = self.library_dir / operation["stored_filename"]
        tombstone_path = self.library_dir / operation["tombstone_filename"]
        connection.execute("SAVEPOINT finalize_uploaded_file_delete")
        try:
            if stored_path.exists():
                os.replace(stored_path, tombstone_path)
            connection.execute("DELETE FROM uploaded_files WHERE file_id=?", (file_id,))
            tombstone_paths = sorted(
                self.library_dir.glob(f".{operation['stored_filename']}.deleting-*")
            )
            for candidate_path in tombstone_paths:
                candidate_path.unlink()
            connection.execute("RELEASE SAVEPOINT finalize_uploaded_file_delete")
        except Exception as error:
            connection.execute("ROLLBACK TO SAVEPOINT finalize_uploaded_file_delete")
            connection.execute("RELEASE SAVEPOINT finalize_uploaded_file_delete")
            restore_error = None
            if not stored_path.exists():
                recovery_paths = [tombstone_path]
                recovery_paths.extend(
                    sorted(self.library_dir.glob(f".{operation['stored_filename']}.deleting-*"))
                )
                for recovery_path in dict.fromkeys(recovery_paths):
                    if not recovery_path.exists():
                        continue
                    try:
                        os.replace(recovery_path, stored_path)
                    except OSError as candidate_restore_error:
                        restore_error = candidate_restore_error
                    else:
                        restore_error = None
                        break
            if stored_path.exists():
                connection.execute(
                    "DELETE FROM uploaded_file_delete_operations WHERE file_id=?",
                    (file_id,),
                )
            else:
                connection.execute(
                    """
                    UPDATE uploaded_file_delete_operations
                    SET state='failed', last_error=?, updated_at=?
                    WHERE file_id=?
                    """,
                    (str(error), _now(), file_id),
                )
            if restore_error is not None:
                if isinstance(error, OSError):
                    raise FileStorageError(
                        "物理文件删除失败且暂时无法恢复原路径，数据库记录已保留"
                    ) from restore_error
                raise FileStorageError("删除失败且无法恢复原始文件") from restore_error
            if isinstance(error, OSError):
                raise FileStorageError("物理文件删除失败，数据库记录已保留") from error
            raise

    def _recover_delete_operations(self) -> None:
        """串行重试持久删除操作，并兼容恢复旧版 tombstone。"""
        recovery_started_at = _now()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            operations = connection.execute(
                """
                SELECT file_id
                FROM uploaded_file_delete_operations
                WHERE updated_at <= ?
                ORDER BY created_at, operation_id
                """,
                (recovery_started_at,),
            ).fetchall()
            for operation in operations:
                try:
                    self._finalize_delete_operation(connection, operation["file_id"])
                except (FileInUseError, FileStorageError, sqlite3.Error):
                    continue

            tracked_tombstones = {
                row[0]
                for row in connection.execute(
                    "SELECT tombstone_filename FROM uploaded_file_delete_operations"
                )
            }
            for tombstone_path in self.library_dir.glob(".*.deleting-*"):
                if tombstone_path.name in tracked_tombstones:
                    continue
                tombstone_name = tombstone_path.name
                stored_filename = tombstone_name[1:].split(".deleting-", 1)[0]
                row = connection.execute(
                    "SELECT file_id FROM uploaded_files WHERE stored_filename=?",
                    (stored_filename,),
                ).fetchone()
                try:
                    if row is None:
                        tombstone_path.unlink()
                    else:
                        stored_path = self.library_dir / stored_filename
                        if stored_path.exists():
                            tombstone_path.unlink()
                        else:
                            os.replace(tombstone_path, stored_path)
                except OSError:
                    continue
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _finalize_project_delete_operation(
        self,
        connection: sqlite3.Connection,
        project_id: str,
    ) -> bool:
        """推进项目目录恢复或垃圾回收，失败时保留持久操作供重试。"""
        operation = connection.execute(
            """
            SELECT original_path, tombstone_path, state
            FROM project_delete_operations
            WHERE project_id=?
            """,
            (project_id,),
        ).fetchone()
        if operation is None:
            return True

        original_path = Path(operation["original_path"])
        tombstone_path = Path(operation["tombstone_path"])
        try:
            if operation["state"] == "restore_failed":
                if not original_path.exists() and tombstone_path.exists():
                    os.replace(tombstone_path, original_path)
                if not original_path.exists():
                    raise OSError("项目原目录与 tombstone 均不存在")
                if tombstone_path.exists():
                    shutil.rmtree(tombstone_path)
                connection.execute(
                    "DELETE FROM project_delete_operations WHERE project_id=?",
                    (project_id,),
                )
                return True

            connection.execute(
                "DELETE FROM project_files WHERE project_id=?",
                (project_id,),
            )
            if original_path.exists():
                if tombstone_path.exists():
                    shutil.rmtree(original_path)
                else:
                    os.replace(original_path, tombstone_path)
            if tombstone_path.exists():
                shutil.rmtree(tombstone_path)
            connection.execute(
                "DELETE FROM project_delete_operations WHERE project_id=?",
                (project_id,),
            )
            return True
        except OSError as error:
            next_state = (
                "restore_failed"
                if operation["state"] == "restore_failed"
                else "cleanup_failed"
            )
            connection.execute(
                """
                UPDATE project_delete_operations
                SET state=?, last_error=?, updated_at=?
                WHERE project_id=?
                """,
                (next_state, str(error), _now(), project_id),
            )
            return False

    def _recover_project_delete_operations(self) -> None:
        """恢复未提交的项目 tombstone，并幂等清理已提交的项目删除。"""
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            operations = connection.execute(
                """
                SELECT project_id
                FROM project_delete_operations
                ORDER BY created_at, operation_id
                """
            ).fetchall()
            for operation in operations:
                self._finalize_project_delete_operation(
                    connection,
                    operation["project_id"],
                )

            projects_dir = Path(ProjectManager.PROJECTS_DIR)
            tracked_paths = {
                Path(row[0])
                for row in connection.execute(
                    "SELECT tombstone_path FROM project_delete_operations"
                )
            }
            if projects_dir.exists():
                for tombstone_path in projects_dir.glob(".*.deleting-*"):
                    if tombstone_path in tracked_paths:
                        continue
                    project_id = tombstone_path.name[1:].split(".deleting-", 1)[0]
                    original_path = projects_dir / project_id
                    try:
                        if not original_path.exists():
                            os.replace(tombstone_path, original_path)
                        else:
                            shutil.rmtree(tombstone_path)
                    except OSError as error:
                        logger.warning(
                            "项目 tombstone 恢复扫描失败：project_id=%s, error=%s",
                            project_id,
                            error,
                        )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _file_from_row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        return dict(row) if row is not None else None

    @staticmethod
    def _with_project_summaries(references: list[dict]) -> list[dict]:
        summaries = []
        for reference in references:
            project_name = None
            try:
                project = ProjectManager.get_project(reference["project_id"])
            except Exception as error:
                logger.warning(
                    "读取文件引用项目摘要失败：project_id=%s, error=%s",
                    reference["project_id"],
                    error,
                )
            else:
                if project is not None:
                    project_name = project.name
            summaries.append({**reference, "project_name": project_name})
        return summaries

    @staticmethod
    def _assert_project_referenceable(
        connection: sqlite3.Connection,
        project_id: str,
    ) -> None:
        deleting = connection.execute(
            "SELECT 1 FROM project_delete_operations WHERE project_id=?",
            (project_id,),
        ).fetchone()
        project_dir = Path(ProjectManager._get_project_dir(project_id))
        project_meta = Path(ProjectManager._get_project_meta_path(project_id))
        try:
            visible = project_dir.is_dir() and project_meta.is_file()
        except OSError:
            visible = False
        if deleting is not None or not visible:
            raise sqlite3.IntegrityError(f"项目不存在或正在删除: {project_id}")

    @staticmethod
    def validate_display_name(display_name: str) -> str:
        """校验并返回可安全作为文件库展示名的纯文件名。"""
        if not isinstance(display_name, str) or not display_name:
            raise ValueError("展示名称必须是非空文件名")
        if display_name != Path(display_name).name or "/" in display_name or "\\" in display_name:
            raise ValueError("展示名称必须是纯文件名")
        suffix = Path(display_name).suffix.lower().lstrip(".")
        if not suffix or suffix not in Config.ALLOWED_EXTENSIONS:
            raise ValueError("不支持的文件扩展名")
        return display_name

    def save_uploads(self, uploads: list[tuple[Any, str]]) -> list[dict]:
        """原子保存一批上传文件；任意失败都清理本批次的文件与记录。"""
        prepared_uploads = []
        for file_storage, display_name in uploads:
            display_name = self.validate_display_name(display_name)
            extension = Path(display_name).suffix.lower().lstrip(".")
            stored_filename = f"stored_{uuid.uuid4().hex}.{extension}"
            prepared_uploads.append({
                "file_storage": file_storage,
                "file_id": f"file_{uuid.uuid4().hex}",
                "display_name": display_name,
                "extension": extension,
                "stored_filename": stored_filename,
                "final_path": self.library_dir / stored_filename,
                "temporary_path": self.library_dir / f".{stored_filename}.uploading",
            })
        try:
            for upload in prepared_uploads:
                digest = hashlib.sha256()
                size_bytes = 0
                with upload["temporary_path"].open("xb") as target:
                    while chunk := upload["file_storage"].stream.read(1024 * 1024):
                        digest.update(chunk)
                        size_bytes += len(chunk)
                        target.write(chunk)
                upload["sha256"] = digest.hexdigest()
                upload["size"] = size_bytes

            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                for upload in prepared_uploads:
                    os.replace(upload["temporary_path"], upload["final_path"])
                now = _now()
                for upload in prepared_uploads:
                    connection.execute(
                        """
                        INSERT INTO uploaded_files (
                            file_id, display_name, stored_filename, extension, size,
                            sha256, created_at, updated_at, legacy_source
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)
                        """,
                        (
                            upload["file_id"],
                            upload["display_name"],
                            upload["stored_filename"],
                            upload["extension"],
                            upload["size"],
                            upload["sha256"],
                            now,
                            now,
                        ),
                    )
                rows = [
                    connection.execute(
                        f"SELECT {_FILE_COLUMNS} FROM uploaded_files WHERE file_id=?",
                        (upload["file_id"],),
                    ).fetchone()
                    for upload in prepared_uploads
                ]
                connection.commit()
                return [self._file_from_row(row) for row in rows]
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()
        except Exception:
            for upload in prepared_uploads:
                upload["temporary_path"].unlink(missing_ok=True)
                upload["final_path"].unlink(missing_ok=True)
            raise

    def save_upload(self, file_storage, display_name: str) -> dict:
        """保存单个文件，复用批量保存的原子性语义。"""
        return self.save_uploads([(file_storage, display_name)])[0]

    def list_files(self, query: str = "", limit: int = 50, offset: int = 0) -> list[dict]:
        limit = max(0, int(limit))
        offset = max(0, int(offset))
        escaped_query = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT f.file_id, f.display_name, f.stored_filename, f.extension, f.size,
                       f.sha256, f.created_at, f.updated_at, f.legacy_source,
                       COUNT(p.file_id) AS reference_count
                FROM uploaded_files AS f
                LEFT JOIN project_files AS p ON p.file_id=f.file_id
                LEFT JOIN uploaded_file_delete_operations AS d ON d.file_id=f.file_id
                WHERE f.display_name LIKE ? ESCAPE '\\' AND d.file_id IS NULL
                GROUP BY f.file_id
                ORDER BY f.created_at DESC, f.file_id DESC
                LIMIT ? OFFSET ?
                """,
                (f"%{escaped_query}%", limit, offset),
            ).fetchall()
        return [self._file_from_row(row) for row in rows]

    def get_file(self, file_id: str) -> dict | None:
        with self._connect() as connection:
            row = connection.execute(
                f"SELECT {_FILE_COLUMNS} FROM uploaded_files WHERE file_id=?", (file_id,)
            ).fetchone()
        return self._file_from_row(row)

    def get_referenceable_file(self, file_id: str) -> dict | None:
        """只返回当前允许建立新项目引用的文件。"""
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT f.file_id, f.display_name, f.stored_filename, f.extension,
                       f.size, f.sha256, f.created_at, f.updated_at, f.legacy_source
                FROM uploaded_files AS f
                LEFT JOIN uploaded_file_delete_operations AS d ON d.file_id=f.file_id
                WHERE f.file_id=? AND d.file_id IS NULL
                """,
                (file_id,),
            ).fetchone()
        return self._file_from_row(row)

    def rename_file(self, file_id: str, display_name: str) -> dict:
        display_name = self.validate_display_name(display_name)
        extension = Path(display_name).suffix.lower().lstrip(".")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "UPDATE uploaded_files SET display_name=?, extension=?, updated_at=? WHERE file_id=?",
                (display_name, extension, _now(), file_id),
            )
            row = connection.execute(
                f"SELECT {_FILE_COLUMNS} FROM uploaded_files WHERE file_id=?", (file_id,)
            ).fetchone()
        if row is None:
            raise KeyError(file_id)
        return self._file_from_row(row)

    def add_project_references(self, project_id: str, file_ids: list[str]) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._assert_project_referenceable(connection, project_id)
            for position, file_id in enumerate(file_ids):
                available = connection.execute(
                    """
                    SELECT 1
                    FROM uploaded_files AS f
                    LEFT JOIN uploaded_file_delete_operations AS d ON d.file_id=f.file_id
                    WHERE f.file_id=? AND d.file_id IS NULL
                    """,
                    (file_id,),
                ).fetchone()
                if available is None:
                    raise sqlite3.IntegrityError(f"文件不存在或正在删除: {file_id}")
                connection.execute(
                    """
                    INSERT OR IGNORE INTO project_files (project_id, file_id, position)
                    VALUES (?, ?, ?)
                    """,
                    (project_id, file_id, position),
                )

    def remove_project_references(self, project_id: str) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("DELETE FROM project_files WHERE project_id=?", (project_id,))

    def ensure_legacy_migration(self) -> dict[str, int]:
        """对当前文件库配置执行一次共享、可重试且防递归的旧项目迁移。"""
        configuration_key = (
            str(self.database_path.resolve()),
            str(self.library_dir.resolve()),
            str(Path(ProjectManager.PROJECTS_DIR).resolve()),
        )
        active_keys = getattr(_active_legacy_migrations, "keys", None)
        if active_keys is None:
            active_keys = set()
            _active_legacy_migrations.keys = active_keys
        if configuration_key in active_keys:
            raise FileStorageError("检测到递归的旧项目文件迁移")

        with _legacy_migration_gate_lock:
            cached = _completed_legacy_migrations.get(configuration_key)
            if cached is not None:
                return dict(cached)
            active_keys.add(configuration_key)
            try:
                result = self.migrate_legacy_projects()
            finally:
                active_keys.remove(configuration_key)
            if result.get("skipped") == 0:
                _completed_legacy_migrations[configuration_key] = dict(result)
            return result

    def assert_project_sources_migrated(self, project_id: str) -> None:
        """拒绝删除仍含未受文件库引用保护的旧项目源文件。"""
        project = ProjectManager.get_project(project_id)
        if project is None:
            raise FileStorageError(f"项目元数据不可读，无法确认旧文件迁移状态: {project_id}")
        if not isinstance(project.files, list):
            raise FileStorageError(f"旧项目文件迁移不完整，已阻止删除: {project_id}")

        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT p.position, p.file_id, f.legacy_source
                FROM project_files AS p
                JOIN uploaded_files AS f ON f.file_id=p.file_id
                LEFT JOIN uploaded_file_delete_operations AS d ON d.file_id=f.file_id
                WHERE p.project_id=? AND d.file_id IS NULL
                """,
                (project_id,),
            ).fetchall()
        links_by_position = {row["position"]: row["file_id"] for row in rows}
        protected_legacy_sources = {
            row["legacy_source"] for row in rows if row["legacy_source"]
        }
        for position, snapshot in enumerate(project.files):
            linked_file_id = links_by_position.get(position)
            if linked_file_id is None:
                raise FileStorageError(
                    f"旧项目文件迁移不完整，已阻止删除: {project_id}"
                )
            if isinstance(snapshot, dict) and isinstance(snapshot.get("file_id"), str):
                if snapshot["file_id"] != linked_file_id:
                    raise FileStorageError(
                        f"旧项目文件迁移不完整，已阻止删除: {project_id}"
                    )

        try:
            source_paths = ProjectManager.get_project_files(project_id)
        except OSError as error:
            raise FileStorageError(
                f"无法确认旧项目文件迁移状态，已阻止删除: {project_id}"
            ) from error
        for source_path in source_paths:
            legacy_source = f"{project_id}:{Path(source_path).name}"
            if legacy_source not in protected_legacy_sources:
                raise FileStorageError(
                    f"旧项目文件迁移不完整，已阻止删除: {project_id}"
                )

    def delete_project_directory(self, project_id: str, project_dir: Path) -> bool:
        """在同一写事务中隐藏项目并解除引用，再持久化清理 tombstone。"""
        project_dir = Path(project_dir)
        if not project_dir.exists():
            return False
        operation_id = f"project_delete_{uuid.uuid4().hex}"
        tombstone_path = project_dir.parent / f".{project_id}.deleting-{operation_id}"
        now = _now()
        connection = self._connect()
        moved = False
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO project_delete_operations (
                    operation_id, project_id, original_path, tombstone_path,
                    state, last_error, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'cleanup_pending', NULL, ?, ?)
                """,
                (
                    operation_id,
                    project_id,
                    str(project_dir),
                    str(tombstone_path),
                    now,
                    now,
                ),
            )
            os.replace(project_dir, tombstone_path)
            moved = True
            connection.execute(
                "DELETE FROM project_files WHERE project_id=?",
                (project_id,),
            )
            connection.commit()
        except Exception as error:
            connection.rollback()
            if moved and tombstone_path.exists() and not project_dir.exists():
                try:
                    os.replace(tombstone_path, project_dir)
                except OSError as restore_error:
                    recovery_connection = self._connect()
                    try:
                        recovery_connection.execute("BEGIN IMMEDIATE")
                        recovery_connection.execute(
                            """
                            INSERT OR REPLACE INTO project_delete_operations (
                                operation_id, project_id, original_path, tombstone_path,
                                state, last_error, created_at, updated_at
                            ) VALUES (?, ?, ?, ?, 'restore_failed', ?, ?, ?)
                            """,
                            (
                                operation_id,
                                project_id,
                                str(project_dir),
                                str(tombstone_path),
                                str(restore_error),
                                now,
                                _now(),
                            ),
                        )
                        recovery_connection.commit()
                    except Exception:
                        recovery_connection.rollback()
                        raise
                    finally:
                        recovery_connection.close()
                    raise FileStorageError(
                        f"项目引用解除失败且原目录暂时无法恢复: {project_id}"
                    ) from restore_error
            raise
        finally:
            connection.close()

        cleanup_connection = self._connect()
        try:
            cleanup_connection.execute("BEGIN IMMEDIATE")
            cleaned = self._finalize_project_delete_operation(
                cleanup_connection,
                project_id,
            )
            cleanup_connection.commit()
        except Exception:
            cleanup_connection.rollback()
            raise
        finally:
            cleanup_connection.close()
        if not cleaned:
            logger.warning(
                "项目已逻辑删除，目录 tombstone 将在后续操作重试清理：project_id=%s",
                project_id,
            )
        return True

    def migrate_legacy_projects(self) -> dict[str, int]:
        """将项目目录中的旧上传文件幂等迁移到共享文件库。"""
        result = {"migrated": 0, "linked": 0, "skipped": 0}
        with _legacy_project_migration_lock:
            for project in ProjectManager.list_projects(limit=None):
                snapshots = project.files if isinstance(project.files, list) else []
                try:
                    source_paths = [
                        Path(path) for path in ProjectManager.get_project_files(project.project_id)
                    ]
                except OSError as error:
                    logger.warning(
                        "跳过旧项目文件迁移：无法枚举项目目录，project_id=%s, error=%s",
                        project.project_id,
                        error,
                    )
                    result["skipped"] += max(len(snapshots), 1)
                    continue

                file_ids = [
                    snapshot.get("file_id")
                    for snapshot in snapshots
                    if isinstance(snapshot, dict) and isinstance(snapshot.get("file_id"), str)
                ]
                legacy_sources_by_file_id = {}
                if file_ids:
                    placeholders = ", ".join("?" for _ in file_ids)
                    try:
                        with self._connect() as connection:
                            rows = connection.execute(
                                f"SELECT file_id, legacy_source FROM uploaded_files WHERE file_id IN ({placeholders})",
                                file_ids,
                            ).fetchall()
                    except sqlite3.Error as error:
                        logger.warning(
                            "跳过旧项目文件迁移：无法读取已迁移文件来源，project_id=%s, error=%s",
                            project.project_id,
                            error,
                        )
                        result["skipped"] += max(len(snapshots), 1)
                        continue
                    legacy_sources_by_file_id = {
                        row["file_id"]: row["legacy_source"] for row in rows
                    }

                matched_paths: dict[int, Path] = {}
                reserved_names: set[str] = set()
                source_claims: dict[str, list[int]] = {}
                blocked_positions: set[int] = set()
                unproven_existing_source = False

                for position, snapshot in enumerate(snapshots):
                    if not isinstance(snapshot, dict):
                        continue
                    file_id = snapshot.get("file_id")
                    if file_id:
                        if not isinstance(file_id, str):
                            unproven_existing_source = True
                            continue
                        legacy_source = legacy_sources_by_file_id.get(file_id)
                        source_name = None
                        prefix = f"{project.project_id}:"
                        if isinstance(legacy_source, str) and legacy_source.startswith(prefix):
                            source_name = legacy_source.removeprefix(prefix)
                        elif isinstance(snapshot.get("saved_filename"), str):
                            source_name = snapshot["saved_filename"]
                        if source_name:
                            source_claims.setdefault(source_name, []).append(position)
                        else:
                            unproven_existing_source = True
                        continue

                    saved_filename = snapshot.get("saved_filename")
                    if isinstance(saved_filename, str) and saved_filename:
                        source_claims.setdefault(saved_filename, []).append(position)

                for source_name, positions in source_claims.items():
                    reserved_names.add(source_name)
                    if len(positions) <= 1:
                        continue
                    logger.warning(
                        "跳过旧项目文件迁移：旧源声明冲突，project_id=%s, saved_filename=%s",
                        project.project_id,
                        source_name,
                    )
                    for position in positions:
                        snapshot = snapshots[position]
                        if snapshot.get("file_id"):
                            unproven_existing_source = True
                        else:
                            blocked_positions.add(position)

                for position, snapshot in enumerate(snapshots):
                    if position in blocked_positions:
                        result["skipped"] += 1
                        continue
                    if not isinstance(snapshot, dict) or snapshot.get("file_id"):
                        continue
                    saved_filename = snapshot.get("saved_filename")
                    if not isinstance(saved_filename, str) or not saved_filename:
                        continue
                    matching_paths = [
                        path for path in source_paths if path.name == saved_filename
                    ]
                    if len(matching_paths) == 1:
                        matched_paths[position] = matching_paths[0]
                    else:
                        logger.warning(
                            "跳过旧项目文件迁移：物理文件不存在或不唯一，project_id=%s, saved_filename=%s",
                            project.project_id,
                            saved_filename,
                        )
                        blocked_positions.add(position)
                        result["skipped"] += 1

                remaining_paths = [
                    path for path in source_paths if path.name not in reserved_names
                ]
                display_positions: dict[str, list[int]] = {}
                for position, snapshot in enumerate(snapshots):
                    if (
                        position in blocked_positions
                        or not isinstance(snapshot, dict)
                        or snapshot.get("file_id")
                        or snapshot.get("saved_filename")
                    ):
                        continue
                    display_name = snapshot.get("filename")
                    if isinstance(display_name, str) and display_name:
                        display_positions.setdefault(display_name, []).append(position)

                if not unproven_existing_source:
                    for display_name, positions in display_positions.items():
                        matching_paths = [
                            path for path in remaining_paths if path.name == display_name
                        ]
                        if len(positions) == 1 and len(matching_paths) == 1:
                            matched_paths[positions[0]] = matching_paths[0]
                            reserved_names.add(matching_paths[0].name)

                fallback_indexes = [
                    position
                    for position, snapshot in enumerate(snapshots)
                    if (
                        position not in blocked_positions
                        and isinstance(snapshot, dict)
                        and not snapshot.get("file_id")
                        and not snapshot.get("saved_filename")
                        and position not in matched_paths
                    )
                ]
                remaining_paths = [
                    path for path in source_paths if path.name not in reserved_names
                ]
                if (
                    not unproven_existing_source
                    and len(fallback_indexes) == len(remaining_paths)
                ):
                    for position, source_path in zip(fallback_indexes, remaining_paths):
                        matched_paths[position] = source_path
                else:
                    for position in fallback_indexes:
                        logger.warning(
                            "跳过旧项目文件迁移：无法唯一匹配旧文件，project_id=%s, position=%s",
                            project.project_id,
                            position,
                        )
                        result["skipped"] += 1

                snapshot_changed = False
                pending_result = {"migrated": 0, "linked": 0}
                pending_snapshot_positions = []
                for position, snapshot in enumerate(snapshots):
                    if not isinstance(snapshot, dict):
                        logger.warning(
                            "跳过旧项目文件迁移：兼容快照格式无效，project_id=%s, position=%s",
                            project.project_id,
                            position,
                        )
                        result["skipped"] += 1
                        continue

                    file_id = snapshot.get("file_id")
                    source_path = matched_paths.get(position)
                    created_path = None
                    try:
                        if file_id:
                            if not isinstance(file_id, str):
                                raise ValueError("file_id 必须是字符串")
                            connection = self._connect()
                            try:
                                connection.execute("BEGIN IMMEDIATE")
                                self._assert_project_referenceable(
                                    connection,
                                    project.project_id,
                                )
                                available = connection.execute(
                                    """
                                    SELECT 1
                                    FROM uploaded_files AS f
                                    LEFT JOIN uploaded_file_delete_operations AS d ON d.file_id=f.file_id
                                    WHERE f.file_id=? AND d.file_id IS NULL
                                    """,
                                    (file_id,),
                                ).fetchone()
                                if available is None:
                                    raise sqlite3.IntegrityError("文件不存在或正在删除")
                                cursor = connection.execute(
                                    """
                                    INSERT OR IGNORE INTO project_files (project_id, file_id, position)
                                    VALUES (?, ?, ?)
                                    """,
                                    (project.project_id, file_id, position),
                                )
                                connection.commit()
                                result["linked"] += cursor.rowcount
                            except Exception:
                                connection.rollback()
                                raise
                            finally:
                                connection.close()
                            continue

                        if source_path is None:
                            continue
                        display_name = self.validate_display_name(snapshot.get("filename"))
                        legacy_source = f"{project.project_id}:{source_path.name}"
                        with self._connect() as lookup_connection:
                            existing = lookup_connection.execute(
                                f"SELECT {_FILE_COLUMNS} FROM uploaded_files WHERE legacy_source=?",
                                (legacy_source,),
                            ).fetchone()

                        if existing is None:
                            extension = Path(display_name).suffix.lower().lstrip(".")
                            file_id = f"file_{uuid.uuid4().hex}"
                            stored_filename = f"stored_{uuid.uuid4().hex}.{extension}"
                            created_path = self.library_dir / stored_filename
                            temporary_path = self.library_dir / f".{stored_filename}.migrating"
                            digest = hashlib.sha256()
                            size_bytes = 0
                            try:
                                with source_path.open("rb") as source, temporary_path.open("xb") as target:
                                    while chunk := source.read(1024 * 1024):
                                        digest.update(chunk)
                                        size_bytes += len(chunk)
                                        target.write(chunk)
                                os.replace(temporary_path, created_path)
                            finally:
                                temporary_path.unlink(missing_ok=True)
                        else:
                            file_id = existing["file_id"]

                        connection = self._connect()
                        try:
                            connection.execute("BEGIN IMMEDIATE")
                            self._assert_project_referenceable(
                                connection,
                                project.project_id,
                            )
                            if existing is None:
                                now = _now()
                                connection.execute(
                                    """
                                    INSERT INTO uploaded_files (
                                        file_id, display_name, stored_filename, extension, size,
                                        sha256, created_at, updated_at, legacy_source
                                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                                    """,
                                    (
                                        file_id,
                                        display_name,
                                        stored_filename,
                                        extension,
                                        size_bytes,
                                        digest.hexdigest(),
                                        now,
                                        now,
                                        legacy_source,
                                    ),
                                )
                            available = connection.execute(
                                """
                                SELECT 1
                                FROM uploaded_files AS f
                                LEFT JOIN uploaded_file_delete_operations AS d ON d.file_id=f.file_id
                                WHERE f.file_id=? AND d.file_id IS NULL
                                """,
                                (file_id,),
                            ).fetchone()
                            if available is None:
                                raise sqlite3.IntegrityError("文件不存在或正在删除")
                            cursor = connection.execute(
                                """
                                INSERT OR IGNORE INTO project_files (project_id, file_id, position)
                                VALUES (?, ?, ?)
                                """,
                                (project.project_id, file_id, position),
                            )
                            connection.commit()
                        except Exception:
                            connection.rollback()
                            if created_path is not None:
                                created_path.unlink(missing_ok=True)
                            raise
                        finally:
                            connection.close()

                        if existing is None:
                            pending_result["migrated"] += 1
                        pending_result["linked"] += cursor.rowcount
                        updated_snapshot = dict(snapshot)
                        updated_snapshot["file_id"] = file_id
                        project.files[position] = updated_snapshot
                        snapshot_changed = True
                        pending_snapshot_positions.append(position)
                    except Exception as error:
                        logger.warning(
                            "跳过旧项目文件迁移：project_id=%s, position=%s, error=%s",
                            project.project_id,
                            position,
                            error,
                        )
                        result["skipped"] += 1

                if snapshot_changed:
                    try:
                        ProjectManager.save_project(project)
                    except Exception as error:
                        logger.warning(
                            "旧项目文件迁移完成后无法回写兼容快照：project_id=%s, error=%s",
                            project.project_id,
                            error,
                        )
                        result["skipped"] += len(pending_snapshot_positions)
                    else:
                        result["migrated"] += pending_result["migrated"]
                        result["linked"] += pending_result["linked"]
        return result

    def list_references(self, file_id: str) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT project_id, position FROM project_files WHERE file_id=? ORDER BY project_id",
                (file_id,),
            ).fetchall()
        return self._with_project_summaries([dict(row) for row in rows])

    def delete_file(self, file_id: str) -> None:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                f"SELECT {_FILE_COLUMNS} FROM uploaded_files WHERE file_id=?", (file_id,)
            ).fetchone()
            if row is None:
                connection.commit()
                return
            references = self._with_project_summaries([
                dict(reference)
                for reference in connection.execute(
                    "SELECT project_id, position FROM project_files WHERE file_id=? ORDER BY project_id",
                    (file_id,),
                ).fetchall()
            ])
            if references:
                raise FileInUseError(file_id, references)
            operation = connection.execute(
                """
                SELECT operation_id
                FROM uploaded_file_delete_operations
                WHERE file_id=?
                """,
                (file_id,),
            ).fetchone()
            now = _now()
            if operation is None:
                operation_id = f"delete_{uuid.uuid4().hex}"
                connection.execute(
                    """
                    INSERT INTO uploaded_file_delete_operations (
                        operation_id, file_id, stored_filename, tombstone_filename,
                        state, last_error, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, 'pending', NULL, ?, ?)
                    """,
                    (
                        operation_id,
                        file_id,
                        row["stored_filename"],
                        f".{row['stored_filename']}.deleting-{operation_id}",
                        now,
                        now,
                    ),
                )
            else:
                connection.execute(
                    """
                    UPDATE uploaded_file_delete_operations
                    SET state='pending', last_error=NULL, updated_at=?
                    WHERE file_id=?
                    """,
                    (now, file_id),
                )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

        connection = self._connect()
        operation_error = None
        try:
            connection.execute("BEGIN IMMEDIATE")
            try:
                self._finalize_delete_operation(connection, file_id)
            except (FileInUseError, FileStorageError, sqlite3.Error) as error:
                operation_error = error
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        if operation_error is not None:
            raise operation_error
