"""LLM-as-judge scoring negotiation drafts against an explicit rubric,
with structured Pydantic output — not a free-text opinion."""
import json
from pydantic import BaseModel, Field
from app.llm.router import call_llm

class DraftJudgment(BaseModel):
    professional_tone: int = Field(ge=1, le=5)
    factually_grounded: int = Field(ge=1, le=5, description="Does it stick to the stated issue without inventing details?")
    clearly_states_approval_needed: bool
    reasoning: str

JUDGE_PROMPT = """Score this negotiation email draft on a rubric. Return ONLY valid JSON:
{{
  "professional_tone": <1-5>,
  "factually_grounded": <1-5>,
  "clearly_states_approval_needed": <true|false>,
  "reasoning": "<one sentence>"
}}

Issue the email should address: {issue}
Draft:
{draft}
"""

def judge_draft(conn, tenant_id: str, issue: str, draft: str) -> DraftJudgment:
    prompt = JUDGE_PROMPT.format(issue=issue, draft=draft)
    result = call_llm(conn, tenant_id, "eval_judge", prompt, use_cache=False)
    parsed = json.loads(result["text"].strip().replace("```json", "").replace("```", ""))
    return DraftJudgment(**parsed)