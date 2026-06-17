"""Multi-Agent System module for AI News Radar V3.

Public API:
    FetchAgent     — orchestrates data collection from all sources
    AnalystAgent   — TL;DR generation + deep analysis via OpenRouter
    TrendAgent     — natural-language interpretation of detected trends
    EditorAgent    — daily AI industry report generation
    CriticAgent    — quality control for generated reports
"""

from core.agents.fetch_agent import FetchAgent
from core.agents.analyst_agent import AnalystAgent
from core.agents.trend_agent import TrendAgent
from core.agents.editor_agent import EditorAgent
from core.agents.critic_agent import CriticAgent

__all__ = [
    "FetchAgent",
    "AnalystAgent",
    "TrendAgent",
    "EditorAgent",
    "CriticAgent",
]
