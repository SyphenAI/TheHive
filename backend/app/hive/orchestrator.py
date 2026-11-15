"""The Hive protocol.

Round 1  answer    every answering seat responds BLIND (no peer visibility) — avoids anchoring.
Round 2  critique  each critiquing seat reviews all answers, anonymized as Response A/B/C...
Round 3  revise    each answering seat revises using peers + critiques.
Repeat critique/revise while rounds remain and answers have not converged.
Final    judge     a seat that did NOT answer scores the final answers against a rubric.
                   If every judge-capable seat also answered, the judge is still chosen but
                   its own response is excluded from scoring (flagged in verdict).

Every call goes through budget.preflight() first. BudgetExceeded halts the session cleanly;
the transcript so far is kept and the session is marked 'halted' with the reason.

Aliases are shuffled per phase so the same model is not always "Response A".
"""

from __future__ import annotations

import json
import logging
import random
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from difflib import SequenceMatcher

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import ApiToken, HiveMember, HiveSession, HiveTurn
from app.security import decrypt_secret
from app.services import budget
from app.services.llm import ChatError, HttpTransport, Transport
from app.services.registry import load_registry, rough_token_count

from . import prompts

logger = logging.getLogger("thehive.orchestrator")

ALIAS_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
MAX_OUTPUT_TOKENS = {"answer": 2000, "critique": 1500, "revise": 2500, "judge": 1500}
TEMPERATURE = {"answer": 0.7, "critique": 0.3, "revise": 0.5, "judge": 0.0}


class HiveHalted(Exception):
    pass


@dataclass
class Seat:
    member: HiveMember
    token: ApiToken

    @property
    def label(self) -> str:
        return self.member.nickname or f"{self.token.provider}/{self.member.model}"


@dataclass
class RunState:
    session: HiveSession
    seats: list[Seat]
    answers: dict[int, str] = field(default_factory=dict)  # member_id -> latest answer
    in_flight_usd: float = 0.0


class Orchestrator:
    def __init__(self, db: Session, transport: Transport | None = None, rng: random.Random | None = None):
        self.db = db
        self.transport = transport or HttpTransport()
        self.registry = load_registry()
        self.rng = rng or random.Random()
        self.cfg = get_settings()

    # ---------------- public ----------------
    def run(self, session: HiveSession, seats: list[Seat], judge: Seat | None) -> HiveSession:
        state = RunState(session=session, seats=seats)
        session.status = "running"
        self.db.commit()
        try:
            self._phase_answer(state)
            prev = dict(state.answers)
            for round_no in range(2, session.max_rounds + 1):
                critiques = self._phase_critique(state, round_no)
                self._phase_revise(state, round_no, critiques)
                if self._converged(prev, state.answers):
                    logger.info("session %s converged at round %s", session.id, round_no)
                    break
                prev = dict(state.answers)
            self._phase_judge(state, judge)
            session.status = "done"
        except HiveHalted as exc:
            session.status = "halted"
            session.halt_reason = str(exc)[:256]
        except Exception as exc:  # noqa: BLE001
            logger.exception("session %s failed", session.id)
            session.status = "error"
            session.halt_reason = f"{type(exc).__name__}: {exc}"[:256]
        session.finished_at = datetime.now(UTC)
        self.db.commit()
        return session

    # ---------------- phases ----------------
    def _phase_answer(self, state: RunState) -> None:
        seats = [s for s in state.seats if s.member.can_answer]
        if not seats:
            raise HiveHalted("No seats are allowed to answer.")
        aliases = self._aliases(seats)
        ctx = prompts.context_block(state.session.context)
        user = prompts.ANSWER_USER.format(
            problem=state.session.problem, context_block=ctx, task_type=state.session.task_type
        )
        for seat in seats:
            text = self._call(
                state,
                seat,
                round_no=1,
                phase="answer",
                alias=aliases[seat.member.id],
                system=prompts.ANSWER_SYSTEM,
                user=user,
            )
            if text is not None:
                state.answers[seat.member.id] = text
        if not state.answers:
            raise HiveHalted("Every seat failed in the answer phase.")

    def _phase_critique(self, state: RunState, round_no: int) -> dict[str, str]:
        critics = [s for s in state.seats if s.member.can_critique]
        if not critics:
            return {}
        answer_seats = [s for s in state.seats if s.member.id in state.answers]
        aliases = self._aliases(answer_seats)
        responses = {aliases[s.member.id]: state.answers[s.member.id] for s in answer_seats}
        ctx = prompts.context_block(state.session.context)
        user = prompts.CRITIQUE_USER.format(
            problem=state.session.problem, context_block=ctx, responses_block=prompts.responses_block(responses)
        )
        critiques: dict[str, str] = {}
        critic_aliases = self._aliases(critics, prefix="Reviewer")
        for seat in critics:
            text = self._call(
                state,
                seat,
                round_no=round_no,
                phase="critique",
                alias=critic_aliases[seat.member.id],
                system=prompts.CRITIQUE_SYSTEM,
                user=user,
            )
            if text:
                critiques[critic_aliases[seat.member.id]] = text
        state.session.verdict = None
        # Stash alias map for revise so each seat knows its own alias.
        state._critique_aliases = aliases  # type: ignore[attr-defined]
        return critiques

    def _phase_revise(self, state: RunState, round_no: int, critiques: dict[str, str]) -> None:
        if not critiques:
            return
        aliases: dict[int, str] = getattr(state, "_critique_aliases", {})
        answer_seats = [s for s in state.seats if s.member.id in state.answers and s.member.can_answer]
        ctx = prompts.context_block(state.session.context)
        all_responses = {aliases[s.member.id]: state.answers[s.member.id] for s in answer_seats}
        for seat in answer_seats:
            own_alias = aliases[seat.member.id]
            peers = {a: t for a, t in all_responses.items() if a != own_alias}
            user = prompts.REVISE_USER.format(
                problem=state.session.problem,
                context_block=ctx,
                own_alias=own_alias,
                own_answer=state.answers[seat.member.id],
                responses_block=prompts.responses_block(peers),
                critiques_block=prompts.critiques_block(critiques),
            )
            text = self._call(
                state, seat, round_no=round_no, phase="revise", alias=own_alias, system=prompts.REVISE_SYSTEM, user=user
            )
            if text:
                state.answers[seat.member.id] = text

    def _phase_judge(self, state: RunState, judge: Seat | None) -> None:
        session = state.session
        judge = judge or self._pick_judge(state)
        if judge is None:
            session.verdict = {"error": "No judge-capable seat available", "final_answers": self._final_answers(state)}
            return
        session.judge_member_id = judge.member.id
        answer_seats = [s for s in state.seats if s.member.id in state.answers and s.member.id != judge.member.id]
        judge_also_answered = judge.member.id in state.answers
        if not answer_seats:
            session.verdict = {
                "error": "Judge was the only answering seat; nothing to judge",
                "final_answers": self._final_answers(state),
            }
            return
        aliases = self._aliases(answer_seats)
        responses = {aliases[s.member.id]: state.answers[s.member.id] for s in answer_seats}
        ctx = prompts.context_block(session.context)
        system = prompts.JUDGE_SYSTEM.format(task_type=session.task_type, rubric=prompts.rubric_for(session.task_type))
        user = prompts.JUDGE_USER.format(
            problem=session.problem, context_block=ctx, responses_block=prompts.responses_block(responses)
        )
        text = self._call(
            state, judge, round_no=session.max_rounds + 1, phase="judge", alias="Judge", system=system, user=user
        )
        verdict = self._parse_verdict(text or "")
        verdict["judge"] = judge.label
        verdict["judge_excluded_own_answer"] = judge_also_answered
        verdict["alias_map"] = {aliases[s.member.id]: s.label for s in answer_seats}
        verdict["final_answers"] = {aliases[s.member.id]: state.answers[s.member.id] for s in answer_seats}
        session.verdict = verdict

    # ---------------- helpers ----------------
    def _call(
        self, state: RunState, seat: Seat, *, round_no: int, phase: str, alias: str, system: str, user: str
    ) -> str | None:
        """Budget-guarded provider call. Records a HiveTurn either way. Returns text or None on failure."""
        session = state.session
        provider = self.registry.provider(seat.token.provider)
        info = provider.model_info(seat.member.model)
        max_out = MAX_OUTPUT_TOKENS[phase]
        tokens_in_est = rough_token_count(system) + rough_token_count(user)

        try:
            pf = budget.preflight(
                self.db,
                session=session,
                member=seat.member,
                info=info,
                tokens_in_est=tokens_in_est,
                max_output_tokens=max_out,
                in_flight_usd=state.in_flight_usd,
            )
        except budget.BudgetExceeded as exc:
            self._record_turn(session, seat, round_no, phase, alias, "", error=str(exc))
            self.db.commit()
            raise HiveHalted(str(exc)) from exc

        state.in_flight_usd += pf.est_cost_usd
        api_key = decrypt_secret(seat.token.encrypted_value)
        try:
            result = self.transport.chat(
                provider=provider,
                info=info,
                api_key=api_key,
                system=system,
                messages=[{"role": "user", "content": user}],
                max_output_tokens=max_out,
                temperature=TEMPERATURE[phase],
                base_url_override=seat.token.base_url_override,
            )
        except ChatError as exc:
            state.in_flight_usd -= pf.est_cost_usd
            budget.record_usage(
                self.db,
                session=session,
                member=seat.member,
                info=info,
                phase=phase,
                tokens_in=tokens_in_est,
                tokens_out=0,
                ok=False,
            )
            self._record_turn(session, seat, round_no, phase, alias, "", error=str(exc))
            self.db.commit()
            logger.warning("seat %s failed in %s: %s", seat.label, phase, exc)
            return None
        finally:
            api_key = ""  # noqa: F841

        state.in_flight_usd -= pf.est_cost_usd
        cost = budget.record_usage(
            self.db,
            session=session,
            member=seat.member,
            info=info,
            phase=phase,
            tokens_in=result.tokens_in,
            tokens_out=result.tokens_out,
            ok=True,
        )
        seat.token.last_used_at = datetime.now(UTC)
        self._record_turn(
            session,
            seat,
            round_no,
            phase,
            alias,
            result.content,
            tokens_in=result.tokens_in,
            tokens_out=result.tokens_out,
            cost=cost,
            latency_ms=result.latency_ms,
        )
        self.db.commit()
        return result.content.strip() or None

    def _record_turn(
        self,
        session: HiveSession,
        seat: Seat,
        round_no: int,
        phase: str,
        alias: str,
        content: str,
        *,
        tokens_in: int = 0,
        tokens_out: int = 0,
        cost: float = 0.0,
        latency_ms: int = 0,
        error: str = "",
    ) -> None:
        self.db.add(
            HiveTurn(
                session_id=session.id,
                round_no=round_no,
                phase=phase,
                member_id=seat.member.id,
                alias=alias,
                content=content,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                cost_usd=cost,
                latency_ms=latency_ms,
                error=error[:4000],
            )
        )

    def _aliases(self, seats: list[Seat], prefix: str = "Response") -> dict[int, str]:
        order = list(seats)
        self.rng.shuffle(order)
        return {s.member.id: f"{prefix} {ALIAS_LETTERS[i]}" for i, s in enumerate(order)}

    def _pick_judge(self, state: RunState) -> Seat | None:
        judges = [s for s in state.seats if s.member.can_judge and s.member.enabled]
        if not judges:
            return None
        non_answering = [s for s in judges if s.member.id not in state.answers]
        pool = non_answering or judges
        # Rotate deterministically by session id so the same judge is not picked every time.
        pool = sorted(pool, key=lambda s: s.member.id)
        return pool[state.session.id % len(pool)]

    def _converged(self, prev: dict[int, str], curr: dict[int, str]) -> bool:
        if not prev or set(prev) != set(curr):
            return False
        ratios = [SequenceMatcher(None, prev[k], curr[k]).ratio() for k in curr]
        return min(ratios) >= self.cfg.convergence_threshold

    def _final_answers(self, state: RunState) -> dict[str, str]:
        return {s.label: state.answers[s.member.id] for s in state.seats if s.member.id in state.answers}

    @staticmethod
    def _parse_verdict(text: str) -> dict:
        text = text.strip()
        m = re.search(r"\{.*\}", text, flags=re.S)
        if m:
            try:
                data = json.loads(m.group(0))
                if isinstance(data, dict):
                    return data
            except json.JSONDecodeError:
                pass
        return {"error": "Judge did not return valid JSON", "raw": text[:4000]}
