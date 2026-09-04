"""
MiroFishPlus Backend - Flask应用工厂
"""

import os
import warnings

# 抑制 multiprocessing resource_tracker 的警告（来自第三方库如 transformers）
# 需要在所有其他导入之前设置
warnings.filterwarnings("ignore", message=".*resource_tracker.*")

from flask import Flask, request
from flask_cors import CORS

from .config import Config
from .utils.logger import setup_logger, get_logger


def create_app(config_class=Config):
    """Flask应用工厂函数"""
    uses_default_config = config_class is Config
    if uses_default_config:
        from pathlib import Path
        from .models.database import (
            initialize_unified_database,
            migrate_legacy_unified_database,
            unified_database_path,
        )
        database_path = unified_database_path()
        migrate_legacy_unified_database(database_path)
        from .models.task import TaskManager
        from .models.task_store import TaskStore
        from .services.credential_cipher import CredentialCipher
        from .services.model_config_store import ModelConfigStore
        key_path = Path(Config.UPLOAD_FOLDER) / "model-config" / "master.key"
        ModelConfigStore(database_path, CredentialCipher(key_path))
        TaskStore(database_path)
        initialize_unified_database(database_path)
        # Re-open once so model-level migrations also cover imported rows.
        ModelConfigStore(database_path, CredentialCipher(key_path))
        TaskManager.configure_store(str(database_path))
        from .services.memory_backend_config_service import MemoryBackendConfigService
        memory_config_service = MemoryBackendConfigService()
        memory_config_service.initialize_from_environment()
        memory_config_service.apply_runtime_config()
    app = Flask(__name__)
    app.config.from_object(config_class)
    
    # 设置JSON编码：确保中文直接显示（而不是 \uXXXX 格式）
    # Flask >= 2.3 使用 app.json.ensure_ascii，旧版本使用 JSON_AS_ASCII 配置
    if hasattr(app, 'json') and hasattr(app.json, 'ensure_ascii'):
        app.json.ensure_ascii = False
    
    # 设置日志
    logger = setup_logger('mirofish')
    
    # 只在 reloader 子进程中打印启动信息（避免 debug 模式下打印两次）
    is_reloader_process = os.environ.get('WERKZEUG_RUN_MAIN') == 'true'
    debug_mode = app.config.get('DEBUG', False)
    should_log_startup = not debug_mode or is_reloader_process
    
    if should_log_startup:
        logger.info("=" * 50)
        logger.info("MiroFishPlus Backend 启动中...")
        logger.info("=" * 50)
    
    # 启用CORS
    CORS(app, resources={r"/api/*": {"origins": "*"}})
    
    # 注册模拟进程清理函数（确保服务器关闭时终止所有模拟进程）
    from .services.simulation_runner import SimulationRunner
    SimulationRunner.register_cleanup()
    stale_environment_count = SimulationRunner.reconcile_stale_environment_statuses()
    if should_log_startup:
        logger.info("已注册模拟进程清理函数")
        if stale_environment_count:
            logger.info("已纠正 %s 个失效的模拟采访环境状态", stale_environment_count)
    
    # 请求日志中间件
    @app.before_request
    def log_request():
        logger = get_logger('mirofish.request')
        logger.debug(f"请求: {request.method} {request.path}")
        if request.content_type and 'json' in request.content_type:
            body = request.get_json(silent=True)
            if isinstance(body, dict):
                body = {
                    key: "[REDACTED]" if key.lower() in {"api_key", "zep_api_key", "neo4j_password", "password", "token", "secret"} else value
                    for key, value in body.items()
                }
            logger.debug(f"请求体: {body}")
    
    @app.after_request
    def log_response(response):
        logger = get_logger('mirofish.request')
        logger.debug(f"响应: {response.status_code}")
        return response
    
    # 注册蓝图
    from .api import files_bp, graph_bp, simulation_bp, report_bp, model_settings_bp
    app.register_blueprint(graph_bp, url_prefix='/api/graph')
    app.register_blueprint(simulation_bp, url_prefix='/api/simulation')
    app.register_blueprint(report_bp, url_prefix='/api/report')
    app.register_blueprint(model_settings_bp, url_prefix='/api/settings/models')
    app.register_blueprint(files_bp, url_prefix='/api/files')

    if uses_default_config and should_log_startup:
        from .services.simulation_preparation_runner import get_simulation_preparation_runner

        recovered_count = get_simulation_preparation_runner().recover_pending()
        if recovered_count:
            logger.info("已恢复 %s 个环境准备任务", recovered_count)
    
    # 健康检查
    @app.route('/health')
    def health():
        return {'status': 'ok', 'service': 'MiroFishPlus Backend'}
    
    if should_log_startup:
        logger.info("MiroFishPlus Backend 启动完成")
    
    return app
