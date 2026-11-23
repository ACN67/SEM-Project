"""Template-driven report generation."""

from .client import ChatCompletionsClient, ChapterWriter
from .service import GenerationService, summarize_chapter

__all__ = ["ChatCompletionsClient", "ChapterWriter", "GenerationService", "summarize_chapter"]
