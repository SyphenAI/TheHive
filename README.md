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

Seat roles: **answer**, **critique**, **judge**, and **redteam** (a seat whose only job in the critique round is to attack the responses with concrete failure scenarios; usually a model that does not answer).

Design choices that matter:

- **Anonymized, shuffled aliases per phase.** Models never see "GPT said…"; they see "Response B". The same model isn't always Response A. This kills brand bias and self-favoritism.
- **Judge never grades its own paper.** Auto mode prefers a seat that didn't answer and rotates by session. If you force a judge that also answered, its own response is excluded from scoring and the verdict says so.
- **Convergence stop.** If every seat's revised answer is ≥92% similar to its previous one, remaining rounds are skipped. Rounds are where the bill lives.
- **Budget guard is pre-flight.** Before each call the orchestrator estimates worst-case cost (input estimate + max output tokens × registry price) and checks it against the per-seat cap, the session budget, and global daily/monthly ceilings. If it would cross any line the session halts cleanly with the transcript so far intact.
- **The verdict is a briefing, not a scoreboard.** The judge must quote the exact text behind every issue it raises, list where the responses *disagree* with its own call and confidence on each, and weigh each response's self-reported confidence (answers end with a `Confidence` section; confident-and-wrong costs more than hedged-and-wrong).
- **Stateless.** Each session is built only from its own problem, context and turns. Nothing from earlier sessions, files or chats is ever included, and the UI says so where you type. If you want prior work in play, add it as context explicitly.
- **Trust boundary.** Model output is untrusted input to the next model. Everything a model reads is wrapped in tags and the prompts say so. No model ever has tool access; the orchestrator is the only thing that touches the DB or network.

## Context: files, URLs, papers

The **Add context** panel on the session form appends tagged blocks to the context:

- **url** — fetched server-side with an SSRF guard (public addresses only, ports 80/443, connection pinned to the checked IP, text content types, 1.5 MB cap), HTML stripped to text.
- **file** — text-like files (code, Markdown, JSON, logs…), 1.5 MB cap. PDFs: paste the text for now.
- **scholarly papers** — ported from [TheResearcher](https://github.com/SyphenAI/TheResearcher): Crossref, Semantic Scholar, OpenAlex and arXiv searched in parallel, merged, de-duplicated and ranked. Pick the ones you want; they land as a numbered `<evidence>` list and the seats are told to cite `[n]` and never invent citations.

Seats with smaller context windows get a trimmed copy (middle cut, marked). Nothing ingested is stored server-side.

Before you run, the form shows the **worst-case and likely cost** using the budget guard's own math, and warns if it exceeds the session budget or today's ceiling. Save a combination of seats, judge, rounds and budget as a **preset**. Finished sessions can be **exported as Markdown** (dissent first) or **re-run** with the form pre-filled.

The live feed streams tokens as each model types (OpenAI, Anthropic and Gemini wire formats); the same SSE stream is available to integrations at `GET /api/v1/hive/sessions/{id}/stream`.

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

## Running with TLS (recommended at work)

```bash
docker compose -f docker-compose.yml -f docker-compose.tls.yml up -d --build
```

Caddy terminates TLS at <https://localhost:50443> with its own local CA and the app's plain port is no longer published. Trust the CA once so the browser stops warning:

```powershell
# Windows (PowerShell, admin)
docker compose exec caddy cat /data/caddy/pki/authorities/local/root.crt > thehive-ca.crt
certutil -addstore -f Root thehive-ca.crt
```
```bash
# macOS
docker compose exec caddy cat /data/caddy/pki/authorities/local/root.crt > thehive-ca.crt
sudo security add-trusted-cert -d -r trustRoot -k /Library/Keychains/System.keychain thehive-ca.crt
```

## Security model

**Network.** Loopback bind only (`127.0.0.1`), HTTP or TLS via the overlay. Nothing listens on the LAN. Provider calls are the only outbound traffic; the container has no other reason to talk to the network.

**Container.** Read-only root filesystem, non-root user, all Linux capabilities dropped, `no-new-privileges`, pid and memory limits, log rotation. Only `data/` and `storage/` are writable.

**Secrets.** `SECRET_KEY` (JWT signing) and `TOKEN_FERNET_KEY` (provider-key vault) are separate, required, and validated at boot: placeholder or short values stop the app from starting. Provider keys are Fernet-encrypted at rest and only ever decrypted in memory for the duration of a call; the UI and API see a mask. Back up `.env` together with `data/`; without the Fernet key the vault is unrecoverable by design.

**Authentication.** bcrypt (cost 12) passwords, 12-char minimum with a weak-list and username check. Forced password change on first login and after an admin reset. JWTs carry a `jti` and a per-user `token_version`: sign-out revokes the token server-side, and a password change, admin reset or deactivation bumps the version so every other session dies at once. Access tokens expire after 8 h. Account lockout after 8 failed attempts (15 min); the lockout is per account, not per IP, because behind Docker every client shares the bridge IP. Failed logins for unknown users burn a bcrypt check so usernames can't be enumerated by timing.

**Authorization.** Two roles for people (`admin`, `member`), three scopes for integrations (`read`, `run`, `admin`). API keys can never manage users or other API keys, whatever their scope; that is JWT-admin only.

**HTTP.** Strict CSP (`default-src 'self'`, no inline script, `frame-ancestors 'none'`), `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy: no-referrer`, `Permissions-Policy`, `Cache-Control: no-store` on the API, HSTS when behind TLS. CORS is an explicit origin list, never `*`. Request bodies are capped at 2 MB (411 without Content-Length, 413 over). Swagger/OpenAPI are off unless `ENABLE_API_DOCS=true`.

**Audit.** Sign-in/out, failed logins, lockouts, password events, user and key lifecycle, provider key and seat changes, kill switch, and every session start are logged with actor (user or `key:<name>`), capped at 2000 rows.

**Kill switch** wipes every stored provider key and VACUUMs SQLite so freed pages don't retain ciphertext. It does **not** revoke keys at the vendor; do that in each console.

**Models are untrusted.** Model output is data to the next model, never instructions, and no model has tool access. The orchestrator is the only component that touches the DB or the network.

What this is *not*: a multi-tenant service. It assumes the people with accounts are trusted colleagues on one machine or one small team. There is no SSO, no MFA, and no per-user budget isolation yet.

## Updating

TheHive **never updates itself**. It holds your provider keys, so code that pulls and runs new versions on a timer is a supply-chain hole. Instead:

1. **The app tells you.** Admins see the running version in the sidebar; when GitHub has something newer it turns into an "Update available" banner with release notes. Channel is `release` by default (compares `VERSION` to the latest GitHub release); set `UPDATE_CHANNEL=pre-prod` to track the branch instead. Set `UPDATE_CHECK_ENABLED=false` to turn it off entirely.
2. **You apply it with one command**, which backs up the database and `.env` first, fetches the code, rebuilds, and restarts. Works whether your folder is a git checkout or a plain copy:
   ```powershell
   .\scripts\update.ps1            # Windows   (add -Tls if you use the TLS overlay)
   ```
   ```bash
   ./scripts/update.sh              # macOS / Linux   (add --tls)
   ```
3. **Schema changes are handled.** On boot the app adds any columns a newer version introduced (`schema: …` lines in the logs) and stamps the DB with the version. A downgrade is refused with a pointer to the pre-update backup in `data/backups/`.

**Prebuilt images.** Once the release workflow (`scripts/release.yml.example`) is in place, every `vX.Y.Z` tag publishes `ghcr.io/syphenai/thehive`. Friends who don't want to build locally use the overlay: `docker compose -f docker-compose.yml -f docker-compose.ghcr.yml up -d` (or `update.ps1 -NoBuild`). Pin with `THEHIVE_TAG=v0.2.0` for stability.

**Fully unattended (opt-in).** `docker-compose.autoupdate.yml` adds Watchtower, which pulls and restarts on every new image with nobody watching. Read the header comment in that file before using it; it's there for people who've decided the convenience is worth it.

**Cutting a release** (maintainer): bump `VERSION`, commit, `git tag v0.2.0 && git push --tags`. The workflow refuses to publish if `VERSION` and the tag disagree.

## Integration API

Everything the UI does goes through `/api/v1`. For another system to drive TheHive, create an API key under **Users & API keys** and send it as `X-API-Key`.

```bash
# start a session (needs `run`)
curl -s -X POST https://localhost:50443/api/v1/hive/sessions \
  -H "X-API-Key: hive_sk_…" -H "content-type: application/json" \
  -d '{"problem": "Review this threat model for gaps…", "context": "<paste>", "task_type": "security", "budget_usd": 2.0}'
# → 202 {"id": 17, "status": "pending", …}

# poll until status is done | halted | error (needs `read`)
curl -s https://localhost:50443/api/v1/hive/sessions/17 -H "X-API-Key: hive_sk_…"
# → {"status": "done", "verdict": {"winner": "Response B", "scores": {…}, "synthesis": "…", "alias_map": {…}, "final_answers": {…}}, "turns": [...]}
```

| Endpoint | Scope | Purpose |
|---|---|---|
| `GET /api/v1/hive/sessions`, `GET /api/v1/hive/sessions/{id}` | read | list / read transcript + verdict |
| `POST /api/v1/hive/sessions`, `DELETE …/{id}` | run | start / remove a session |
| `GET /api/v1/hive/budget`, `GET /api/v1/hive/usage` | read | spend vs ceilings, per-model ledger |
| `GET /api/v1/providers/registry`, `…/tokens`, `…/members` | read | what's configured (keys masked) |
| `POST/PATCH/DELETE /api/v1/providers/*` | admin | manage keys and seats |
| `/api/v1/auth/users*`, `/api/v1/auth/api-keys*` | JWT admin only | people and integration credentials |

Sessions started by an API key are owned by the first admin account. Set `ENABLE_API_DOCS=true` locally to browse the full OpenAPI schema at `/docs`. The `/api/v1` prefix is the compatibility promise; breaking changes will go to `/api/v2`.

## Roadmap

- [ ] Execution judge for `code` tasks: run candidates against tests in a network-isolated runner instead of asking a model which looks best
- [ ] PDF ingestion; Google Scholar via SerpAPI as an optional keyed source
- [ ] Webhook on session completion (with host allowlist) for push-style integration
- [ ] Per-user budgets; SSO / MFA for team deployments
- [ ] Shared core package with [TheResearcher](https://github.com/SyphenAI/TheResearcher) (auth, vault, transport, usage)

## License

MIT — see [LICENSE](LICENSE).
