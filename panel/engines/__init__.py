"""Engine registry."""
from typing import Any, Dict, Optional

from panel.engines.base import CallContext, Engine, EngineResult, ParsedResponse  # noqa: F401
from panel.engines.claude import ClaudeEngine
from panel.engines.gemini import GeminiEngine
from panel.engines.google_aio import GoogleAIOEngine
from panel.engines.openai_engine import OpenAIEngine
from panel.engines.perplexity import PerplexityEngine

ENGINE_CLASSES = {
    "openai": OpenAIEngine,
    "gemini": GeminiEngine,
    "perplexity": PerplexityEngine,
    "claude": ClaudeEngine,
    "google_aio": GoogleAIOEngine,
}


def make_engine(engine_id: str, panel: Dict[str, Any], mock: bool = False) -> Engine:
    cls = ENGINE_CLASSES[engine_id]
    return cls(panel["engines"][engine_id], retry_cfg=panel.get("retry"), mock=mock)
