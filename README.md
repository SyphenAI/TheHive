# TheHive

Several frontier models argue a problem out. One of them judges. You get a transcript, scores, and a synthesis — and a hard dollar cap on the whole thing.

TheHive runs locally in Docker, behind its own login, using **your** API keys for OpenAI, Anthropic, xAI, Google, or any OpenAI-compatible endpoint. Keys are encrypted at rest. Every provider call is budget-checked *before* it goes out.

## The protocol

```
Round 1  answer    every answering seat responds BLIND — no one sees anyone else (avoids anchoring)
Round 2  critique  each critiquing seat reviews all answers, anonymized as Response A/B/C…
Round 2  revise    each answering seat revises using peers + critiques
Round 3+ repeat critique/revise until max rounds or the answers stop changing
Final    judge     a seat that did NOT answer scores the finalists against a rubric and writes a synthesis
```

Design choices that matter:

- **Anonymized, shuffled aliases per phase.** Models never see "GPT said…"; they see "Response B". The same model isn't always Response A. This kills brand bias and self-favoritism.
- **Judge never grades its own paper.** Auto mode prefers a seat that didn't answer and rotates by session. If you force a judge that also answered, its own response is excluded from scoring and the verdict says so.
- **Convergence stop.** If every seat's revised answer is ≥92% similar to its previous one, remaining rounds are skipped. Rounds are where the bill lives.
- **Budget guard is pre-flight.** Before each call the orchestrator estimates worst-case cost (input estimate + max output tokens × registry price) and checks it against the per-seat cap, the session budget, and global daily/monthly ceilings. If it would cross any line the session halts cleanly with the transcript so far intact.
- **Trust boundary.** Model output is untrusted input to the next model. Everything a model reads is wrapped in tags and the prompts say so. No model ever has tool access; the orchestrator is the only thing that touches the DB or network.

## Quick start

```bash
git clone https://github.com/SyphenAI/TheHive.git && cd TheHive
cp .env.example .env
./scripts/gen-secrets.sh >> .env        # Windows: scripts\gen-secrets.ps1 >> .env
docker compose up -d --build
```

Open <http://127.0.0.1:50090>. Sign in as `hive` / `change-on-first-login`; you'll be forced to set a real password immediately.

Then, under **Providers & seats**:

1. Add a key for each vendor (stored Fernet-encrypted; the UI only ever sees `sk-a…9f3x`).
2. Add seats: one model on one key. Toggle whether each seat may **A**nswer, **C**ritique, **J**udge, and give it an optional per-call dollar cap.
3. Hit **Test** / **Discover models** to confirm the key works and see what the vendor currently exposes.

Go to **Sessions**, paste a problem (and any prior work as context), pick seats, set a budget, release the hive.

The app refuses to start if `SECRET_KEY` or `TOKEN_FERNET_KEY` is missing or looks like a placeholder.

## Local development

```bash
# backend
cd backend && python -m venv .venv && . .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
export SECRET_KEY=… TOKEN_FERNET_KEY=… DATA_DIR=../data STORAGE_DIR=../storage REGISTRY_PATH=../registry/providers.json
uvicorn app.main:app --reload --host 127.0.0.1 --port 50090
pytest                                   # fake transport; no network, no spend

# frontend (separate shell; proxies /api to :50090)
cd frontend && npm install && npm run dev
```

## Layout

```
backend/app/
  config.py            settings; refuses placeholder secrets
  security.py          bcrypt, JWT, Fernet
  models.py            users, api_tokens, hive_members (seats), hive_sessions, hive_turns, usage_events, audit_log
  hive/prompts.py      every prompt template + rubrics, in one readable file
  hive/orchestrator.py the round loop, aliasing, convergence, judge selection
  services/budget.py   pre-flight guard + usage ledger
  services/llm.py      HTTP transport for openai | anthropic | google wire styles
  services/registry.py loads registry/providers.json
  routers/             auth, providers (keys + seats + kill switch), hive (sessions), system (health, audit)
backend/tests/         orchestrator + budget tests against a scripted FakeTransport
frontend/              Vite + React, no UI framework
registry/providers.json  vendors, models, prices — edit this, not Python
```

## Costs and pricing

Prices in `registry/providers.json` are **estimates used for enforcement**, not billing truth. Update them when vendors change pricing; unknown model IDs fall back to the provider's `default_price`. The **Usage** page shows the ledger; your vendor invoices are the real number.

## Security notes

- Binds to `127.0.0.1` only. If you need LAN access, put a TLS reverse proxy with its own auth in front; don't change the bind.
- Container runs read-only, non-root, all capabilities dropped. Only `data/`, `storage/` are writable (mounted volumes).
- Swagger is off by default (`ENABLE_API_DOCS=true` to enable while developing).
- Login is throttled (10 failures / 5 min / IP). Audit log records sign-ins, key changes, seat changes, and every session start.
- **Kill switch** wipes every stored key and VACUUMs SQLite. It does **not** revoke keys at the vendor — do that in each console.
- One `SECRET_KEY` signs sessions; a separate `TOKEN_FERNET_KEY` protects the key vault. Back them up together with `data/`; lose the Fernet key and the vault is gone.

## Roadmap

- [ ] Execution judge for `code` tasks: run candidates against tests in a network-isolated runner instead of asking a model which looks best
- [ ] Context store: index prior sessions and local repos, retrieve per problem with per-seat context caps
- [ ] Streaming transcript (SSE) instead of polling
- [ ] Export session as Markdown
- [ ] Shared core package with [TheResearcher](https://github.com/SyphenAI/TheResearcher) (auth, vault, transport, usage)

## License

MIT — see [LICENSE](LICENSE).
