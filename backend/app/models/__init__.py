"""数据模型模块，公开对象按需加载以避免导入时创建数据库。"""

__all__ = ["TaskManager", "TaskStatus", "Project", "ProjectStatus", "ProjectManager"]


def __getattr__(name):
    if name in {"TaskManager", "TaskStatus"}:
        from .task import TaskManager, TaskStatus

        return {"TaskManager": TaskManager, "TaskStatus": TaskStatus}[name]
    if name in {"Project", "ProjectStatus", "ProjectManager"}:
        from .project import Project, ProjectManager, ProjectStatus

        return {
            "Project": Project,
            "ProjectStatus": ProjectStatus,
            "ProjectManager": ProjectManager,
        }[name]
    raise AttributeError(name)
