"""Structured-output schemas for Gemini responses (Pydantic models).

Passed directly as response_schema to the Gemini API so the model is
constrained to return exactly this shape as JSON.
"""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class Stage1Decision(str, Enum):
    DEEP_ANALYZE = "DEEP_ANALYZE"
    KEEP_LIGHT = "KEEP_LIGHT"
    REJECT = "REJECT"


class Stage1Result(BaseModel):
    preliminary_relevance_score: int = Field(ge=0, le=100)
    decision: Stage1Decision
    reason: str = Field(description="One short sentence explaining the decision.")


class ChildrenAgeRelevance(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    UNKNOWN = "UNKNOWN"


class RecommendedAction(str, Enum):
    HIGH_PRIORITY = "HIGH_PRIORITY"
    MEDIUM_PRIORITY = "MEDIUM_PRIORITY"
    LOW_PRIORITY = "LOW_PRIORITY"
    REJECT = "REJECT"


class ProspectType(str, Enum):
    CUSTOMER = "CUSTOMER"
    CREATOR = "CREATOR"
    PROFESSIONAL_PARTNER = "PROFESSIONAL_PARTNER"
    NOT_RELEVANT = "NOT_RELEVANT"


class Stage2Result(BaseModel):
    relevance_score: int = Field(ge=0, le=100)
    is_relevant: bool
    prospect_types: list[ProspectType]
    primary_category: str
    likely_parent: Optional[bool] = None
    likely_parent_audience: Optional[bool] = None
    children_age_relevance: ChildrenAgeRelevance
    language: str
    country: str = Field(description="Full country name, or 'UNKNOWN' if it cannot be reasonably inferred.")
    fableya_fit: list[str] = Field(description="Short bullet points on why/how Fableya fits this account.")
    positive_signals: list[str]
    negative_signals: list[str]
    reason: str = Field(description="Concise explanation, 1-3 sentences max.")
    recommended_action: RecommendedAction
