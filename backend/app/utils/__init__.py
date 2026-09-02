"""工具模块，公开对象按需加载以避免应用导入副作用。"""

__all__ = ["FileParser", "LLMClient", "t", "get_locale", "set_locale", "get_language_instruction"]


def __getattr__(name):
    if name == "FileParser":
        from .file_parser import FileParser

        return FileParser
    if name == "LLMClient":
        from .llm_client import LLMClient

        return LLMClient
    if name in {"t", "get_locale", "set_locale", "get_language_instruction"}:
        from .locale import get_language_instruction, get_locale, set_locale, t

        return {
            "t": t,
            "get_locale": get_locale,
            "set_locale": set_locale,
            "get_language_instruction": get_language_instruction,
        }[name]
    raise AttributeError(name)
