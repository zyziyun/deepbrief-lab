"""AgentCard — A2A's discovery primitive.

An A2A-compliant agent publishes a JSON document at:
    https://<agent-domain>/.well-known/agent-card.json

This is the *digital business card* an orchestrator reads before deciding
which agent can handle a task. We expose a tiny subset of the official
A2A schema — enough to demonstrate discovery without dragging in all the
push-notification / state-transition machinery.

Spec: https://a2a-protocol.org (Linux Foundation, 2025).
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class Skill(BaseModel):
    id: str = Field(..., description="Stable identifier for this skill")
    name: str
    description: str
    tags: list[str] = []
    examples: list[str] = []


class Capabilities(BaseModel):
    streaming: bool = False
    pushNotifications: bool = False
    stateTransitionHistory: bool = False


class Provider(BaseModel):
    organization: str
    url: str | None = None


class AgentCard(BaseModel):
    """Minimal A2A AgentCard. Real spec has more fields — this is enough for the lab."""

    name: str
    description: str
    version: str = "0.1.0"
    url: str = Field(..., description="Public URL where this agent's A2A endpoint lives")
    provider: Provider
    capabilities: Capabilities = Capabilities()
    defaultInputModes: list[str] = ["text/plain"]
    defaultOutputModes: list[str] = ["text/plain", "application/json"]
    skills: list[Skill]


def researcher_card(public_url: str) -> AgentCard:
    return AgentCard(
        name="DeepBrief Researcher",
        description="Researches a single sub-question and returns a summary with sources.",
        url=public_url,
        provider=Provider(organization="DeepBrief Lab"),
        skills=[
            Skill(
                id="research_subquery",
                name="Research a sub-question",
                description=(
                    "Given a single focused sub-question, produces a 2-4 sentence summary "
                    "with up to 3 source URLs."
                ),
                tags=["research", "web"],
                examples=["What browsers support WebGPU as of 2026?"],
            )
        ],
    )
