"""
Agent tools for the agentic workbench.

All tools inherit from ``ToolSpec`` which provides:
    - Pydantic input validation via ``args_schema``
    - Automatic tracing, logging, and metrics in ``execute()``
    - LangChain/LangGraph interop via ``to_langchain_tool()``

Core tools (Days 1–2):
    SearchWikipediaTool, OpenChunkTool

Utility tools:
    DateNormalizerTool
"""

from workbench.tools.base import ToolSpec
from workbench.tools.open_chunk import OpenChunkArgs, OpenChunkTool
from workbench.tools.search_wikipedia import SearchWikipediaArgs, SearchWikipediaTool
from workbench.tools.utility.dates import DateNormalizerArgs, DateNormalizerTool

__all__ = [
    # Base
    "ToolSpec",
    # Core tools
    "SearchWikipediaTool",
    "SearchWikipediaArgs",
    "OpenChunkTool",
    "OpenChunkArgs",
    # Day 5 tools
    "DateNormalizerTool",
    "DateNormalizerArgs",
]
