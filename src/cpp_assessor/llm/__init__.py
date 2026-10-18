"""LLM 调用层（模式二用）。

这里只负责"把提示词发给模型、把 JSON 收回来"，不参与任何评分判定。
"""

from .provider import LLMClient, LLMError, LLMResponse, LLMSettings

__all__ = ["LLMClient", "LLMError", "LLMResponse", "LLMSettings"]
