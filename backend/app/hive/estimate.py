"""Pre-run cost estimate. Uses the same prices and output caps the budget guard uses, so the number
shown before 'Release the hive' is the same worst case that would halt the run."""

from __future__ import annotations

from dataclasses import dataclass

from app.hive.orchestrator import MAX_OUTPUT_TOKENS, Seat
from app.hive.prompts import ANSWER_SYSTEM, CRITIQUE_SYSTEM, JUDGE_SYSTEM, REDTEAM_SYSTEM, REVISE_SYSTEM
from app.services.registry import estimate_cost, load_registry, rough_token_count


@dataclass
class PhaseEstimate:
    phase: str
    calls: int
    usd: float


def estimate_session(*, seats: list[Seat], problem: str, context: str, max_rounds: int) -> dict:
    reg = load_registry()
    base_in = rough_token_count(problem) + rough_token_count(context)
    answerers = [s for s in seats if s.member.can_answer]
    critics = [s for s in seats if s.member.can_critique]
    redteam = [s for s in seats if getattr(s.member, "can_redteam", False)]
    judges = [s for s in seats if s.member.can_judge] or seats
    n = len(answerers)

    def cost(seat: Seat, tokens_in: int, phase: str) -> float:
        info = reg.provider(seat.token.provider).model_info(seat.member.model)
        return estimate_cost(info, tokens_in, MAX_OUTPUT_TOKENS[phase])

    phases: list[PhaseEstimate] = []
    # round 1 answers
    tin = base_in + rough_token_count(ANSWER_SYSTEM)
    phases.append(PhaseEstimate("answer", n, sum(cost(s, tin, "answer") for s in answerers)))

    # worst case: every remaining round runs critique (+redteam) and revise, no convergence
    answers_tokens = n * MAX_OUTPUT_TOKENS["answer"]
    crit_tokens = (len(critics) * MAX_OUTPUT_TOKENS["critique"]) + (len(redteam) * MAX_OUTPUT_TOKENS["redteam"])
    for r in range(2, max_rounds + 1):
        tin_c = base_in + rough_token_count(CRITIQUE_SYSTEM) + answers_tokens
        phases.append(PhaseEstimate(f"critique r{r}", len(critics), sum(cost(s, tin_c, "critique") for s in critics)))
        if redteam:
            tin_r = base_in + rough_token_count(REDTEAM_SYSTEM) + answers_tokens
            phases.append(PhaseEstimate(f"redteam r{r}", len(redteam), sum(cost(s, tin_r, "redteam") for s in redteam)))
        tin_v = base_in + rough_token_count(REVISE_SYSTEM) + answers_tokens + crit_tokens
        phases.append(PhaseEstimate(f"revise r{r}", n, sum(cost(s, tin_v, "revise") for s in answerers)))
        answers_tokens = n * MAX_OUTPUT_TOKENS["revise"]

    # judge: the priciest judge-capable seat, worst case
    tin_j = base_in + rough_token_count(JUDGE_SYSTEM) + answers_tokens
    phases.append(PhaseEstimate("judge", 1, max((cost(s, tin_j, "judge") for s in judges), default=0.0)))

    total = sum(p.usd for p in phases)
    return {
        "worst_case_usd": round(total, 4),
        "likely_usd": round(total * 0.45, 4),  # typical: outputs ~half of cap, often converges early
        "calls": sum(p.calls for p in phases),
        "phases": [{"phase": p.phase, "calls": p.calls, "usd": round(p.usd, 4)} for p in phases],
        "seats": [
            {
                "id": s.member.id,
                "label": s.label,
                "known_price": reg.provider(s.token.provider).model_info(s.member.model).known,
            }
            for s in seats
        ],
    }
