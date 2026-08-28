"""
src/llm/schema_parser.py
Pydantic-enforced output contracts for the KPI Intelligence engine.

Every recommendation the engine emits must conform to the business-lever
action schema mandated by the Round 2 brief:

    Driver -> Controllable Lever -> Action -> Expected Impact -> Owner -> Confidence -> Monitoring Plan

and every narrative must be either a validated recommendation OR a validated
abstention -- never a free-form blob. Malformed output raises a
pydantic.ValidationError, which the caller must handle (see
narrative_generator.py's fallback path).
"""

from typing import List, Literal, Optional
from pydantic import BaseModel, Field, field_validator, model_validator


class ActionRecommendation(BaseModel):
    """The Driver -> Lever -> Action -> Impact -> Owner -> Confidence -> Monitoring schema (REQ-06)."""

    driver: str = Field(min_length=3, description="The root-cause business driver, e.g. 'Supplier shipping delay'.")
    controllable_lever: str = Field(min_length=3, description="The business lever the owner can pull.")
    action: str = Field(min_length=3, description="The concrete, executable action.")
    expected_impact: str = Field(min_length=3, description="Quantified expected outcome.")
    owner: str = Field(min_length=3, description="The role/person accountable for executing the action.")
    confidence: float = Field(ge=0.0, le=100.0, description="Confidence in this recommendation, 0-100.")
    monitoring_plan: str = Field(min_length=3, description="How success/failure will be tracked going forward.")

    @field_validator("driver", "controllable_lever", "action", "expected_impact", "owner", "monitoring_plan")
    @classmethod
    def _no_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("field must not be blank")
        return v


class AbstentionResponse(BaseModel):
    """Structured 'Abstain: Insufficient Evidence' response (REQ-05)."""

    abstained: Literal[True] = True
    reason: str = Field(min_length=3)
    confidence: float = Field(ge=0.0, le=100.0)
    conflicting_signals: List[str] = Field(default_factory=list)


class PersonaNarrative(BaseModel):
    """A single persona's rendering of an anomaly (REQ-04)."""

    persona: Literal["vp_sales", "supply_planner"]
    headline: str = Field(min_length=3)
    summary: str = Field(min_length=3)
    synthesis_title: str = Field(min_length=3)
    synthesis_body: str = Field(min_length=3)
    recommended_action: Optional[ActionRecommendation] = None
    abstention: Optional[AbstentionResponse] = None
    generation_method: Literal["llm", "deterministic"] = "deterministic"

    @model_validator(mode="after")
    def _exactly_one_outcome(self):
        has_action = self.recommended_action is not None
        has_abstention = self.abstention is not None
        if has_action == has_abstention:
            raise ValueError(
                "PersonaNarrative must carry exactly one of recommended_action or abstention, not both/neither"
            )
        return self


class NarrativeBundle(BaseModel):
    """Both persona views of one anomaly, produced by a single pipeline run."""

    vp_sales: PersonaNarrative
    supply_planner: PersonaNarrative


def validate_action(data: dict) -> ActionRecommendation:
    """Raises pydantic.ValidationError on malformed action payloads."""
    return ActionRecommendation(**data)


def validate_narrative_bundle(data: dict) -> NarrativeBundle:
    """Raises pydantic.ValidationError on malformed narrative bundles."""
    return NarrativeBundle(**data)
