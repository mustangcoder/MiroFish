"""已上传文件库管理 API。"""

import logging
import sqlite3

from flask import jsonify, request, send_from_directory
from werkzeug.exceptions import RequestEntityTooLarge

from . import files_bp
from ..services.uploaded_file_store import FileInUseError, FileStorageError, UploadedFileStore


logger = logging.getLogger(__name__)


def _success(data, status=200, **extra):
    """返回符合全局 API 契约的成功响应。"""
    return jsonify({"success": True, "data": data, "error": None, **extra}), status


def _error(message, status, data=None):
    """返回符合全局 API 契约的失败响应。"""
    return jsonify({"success": False, "data": data, "error": message}), status


def _store():
    store = UploadedFileStore()
    store.ensure_legacy_migration()
    return store


@files_bp.errorhandler(RequestEntityTooLarge)
def uploaded_file_request_too_large(error):
    """将 Flask 的上传大小限制错误映射为 API 契约。"""
    logger.warning("文件上传请求超过大小限制：%s", error)
    return _error("上传文件总大小不能超过50MB", 413)


@files_bp.errorhandler(FileStorageError)
@files_bp.errorhandler(OSError)
@files_bp.errorhandler(sqlite3.Error)
def uploaded_file_storage_error(error):
    """隐藏存储实现细节，同时在服务端保留完整异常。"""
    logger.exception("文件库操作失败：%s", error)
    return _error("文件存储失败，请稍后重试", 500)


@files_bp.get("")
@files_bp.get("/")
def list_uploaded_files():
    """分页搜索已上传的文件，并附带引用数。"""
    limit = request.args.get("limit", 50, type=int)
    offset = request.args.get("offset", 0, type=int)
    limit = max(1, min(limit if limit is not None else 50, 200))
    offset = max(0, offset if offset is not None else 0)
    store = _store()
    files = store.list_files(request.args.get("query", ""), limit, offset)
    return _success(files, limit=limit, offset=offset)


@files_bp.post("")
@files_bp.post("/")
def upload_files():
    """上传一个或多个文件到共享文件库。"""
    uploads = [file for file in request.files.getlist("files") if file.filename]
    if not uploads:
        return _error("请选择至少一个文件", 400)
    store = _store()
    try:
        for file in uploads:
            store.validate_display_name(file.filename)
        files = store.save_uploads([(file, file.filename) for file in uploads])
    except ValueError as error:
        return _error(str(error), 400)
    return _success(files, 201)


@files_bp.patch("/<file_id>")
def rename_uploaded_file(file_id):
    """更新文件展示名称。"""
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return _error("请求体必须是 JSON 对象", 400)
    try:
        file = _store().rename_file(file_id, payload.get("display_name", ""))
    except ValueError as error:
        return _error(str(error), 400)
    except KeyError:
        return _error(f"文件不存在: {file_id}", 404)
    return _success(file)


@files_bp.get("/<file_id>/download")
def download_uploaded_file(file_id):
    """使用不可变存储名定位文件，并以展示名称下载。"""
    store = _store()
    file = store.get_file(file_id)
    if file is None:
        return _error(f"文件不存在: {file_id}", 404)
    if not (store.library_dir / file["stored_filename"]).is_file():
        return _error("文件内容不存在", 404)
    return send_from_directory(
        store.library_dir,
        file["stored_filename"],
        as_attachment=True,
        download_name=file["display_name"],
    )


@files_bp.get("/<file_id>/references")
def list_uploaded_file_references(file_id):
    """查询引用文件的项目。"""
    store = _store()
    if store.get_file(file_id) is None:
        return _error(f"文件不存在: {file_id}", 404)
    return _success(store.list_references(file_id))


@files_bp.delete("/<file_id>")
def delete_uploaded_file(file_id):
    """删除未被项目引用的文件。"""
    store = _store()
    if store.get_file(file_id) is None:
        return _error(f"文件不存在: {file_id}", 404)
    try:
        store.delete_file(file_id)
    except FileInUseError as error:
        return _error(
            str(error),
            409,
            data={"references": error.references},
        )
    return _success(None)
