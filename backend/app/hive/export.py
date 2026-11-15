"""Session → Markdown. Self-contained: problem, context, verdict (dissent first), final answers, transcript."""

from __future__ import annotations

from app.models import HiveMember, HiveSession


def _name(t, members: dict[int, HiveMember], reveal: bool) -> str:
    if not reveal:
        return t.alias
    m = members.get(t.member_id)
    return (m.nickname or f"{m.token.provider}/{m.model}") if m else t.alias


def session_markdown(s: HiveSession, members: dict[int, HiveMember], *, reveal: bool = False) -> str:
    v = s.verdict or {}
    out: list[str] = []
    out.append(f"# {s.title or 'TheHive session'}\n")
    out.append(
        f"*TheHive session #{s.id} · {s.task_type} · {s.status} · ${s.spent_usd:.4f} of ${s.budget_usd:.2f} · {s.created_at:%Y-%m-%d %H:%M} UTC*\n"
    )
    out.append("## Problem\n")
    out.append(s.problem.strip() + "\n")
    if s.context.strip():
        out.append("## Context supplied\n")
        out.append("```\n" + s.context.strip() + "\n```\n")

    if v and not v.get("_requested_member_ids"):
        out.append("## Verdict\n")
        if v.get("error"):
            out.append(f"> Judge error: {v['error']}\n")
        else:
            label = lambda a: f"{a} ({v['alias_map'][a]})" if reveal and v.get("alias_map", {}).get(a) else a  # noqa: E731
            out.append(
                f"**Winner:** {label(v.get('winner', '?'))}  \n**Judge:** {v.get('judge', '?') if reveal else 'hidden'}\n"
            )
            if v.get("disagreements"):
                out.append("### Where the panel disagreed\n")
                for d in v["disagreements"]:
                    out.append(f"- **{d.get('topic', '')}**")
                    for a, pos in (d.get("positions") or {}).items():
                        out.append(f"  - {label(a)}: {pos}")
                    out.append(f"  - **Judge's call** ({d.get('confidence', '?')}): {d.get('call', '')}")
                out.append("")
            if v.get("scores"):
                crit = list(next(iter(v["scores"].values())).keys())
                out.append("| Response | " + " | ".join(crit) + " |")
                out.append("|---|" + "---|" * len(crit))
                for a, sc in v["scores"].items():
                    out.append(f"| {label(a)} | " + " | ".join(str(sc.get(c, "")) for c in crit) + " |")
                out.append("")
            if v.get("per_response"):
                out.append("### Findings per response\n")
                for a, pr in v["per_response"].items():
                    out.append(f"**{label(a)}**")
                    for st in pr.get("strengths") or []:
                        out.append(f"- ✅ {st}")
                    for it in pr.get("issues") or []:
                        out.append(f"- ⚠️ “{it.get('quote', '')}” — {it.get('why', '')}")
                    out.append("")
            if v.get("rationale"):
                out.append(f"### Rationale\n\n{v['rationale']}\n")
            if v.get("synthesis"):
                out.append(f"### Synthesis\n\n{v['synthesis']}\n")
        if v.get("final_answers"):
            out.append("## Final answers\n")
            for a, text in v["final_answers"].items():
                out.append(f"### {label(a) if not v.get('error') else a}\n\n{text}\n")

    out.append("## Transcript\n")
    rnd = None
    for t in s.turns:
        if t.round_no != rnd:
            rnd = t.round_no
            out.append(f"### Round {rnd}\n")
        head = f"**{_name(t, members, reveal)}** · {t.phase} · {t.tokens_in}→{t.tokens_out} tok · ${t.cost_usd:.4f}"
        if t.state == "error":
            out.append(f"{head}\n\n> ❌ {t.error}\n")
        else:
            out.append(f"{head}\n\n{t.content}\n")
    out.append("\n---\n*Exported from TheHive. Sessions are stateless; this document is the complete record.*\n")
    return "\n".join(out)
