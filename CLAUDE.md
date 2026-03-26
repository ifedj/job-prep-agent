# Job Prep Agent

## Stack
- Backend: FastAPI (Python), deployed as Vercel serverless functions
- Frontend: React via CDN (no build step), single index.html
- DB: Supabase PostgreSQL — use pg8000 + NullPool + lazy engine init (NOT SQLite locally if testing Vercel behavior)
- AI: Claude API via anthropic SDK
- Auth: Google OAuth (also used for sending emails on behalf of user)

## Critical Architecture Rules

### Vercel Constraints — READ FIRST
- **10s function timeout**. No long-running tasks in request handlers.
- Scheduler/cron is DISABLED on Vercel — do not add background tasks to main.py
- Always use lazy DB engine init — engine must not be created at module import time
- NullPool only — no persistent connections

### The Core Pipeline
Calendar Sync → Classify → Generate → Email
Each step is a separate concern. Do not merge them.

### Auto-trigger after onboarding
When `/api/onboarding/complete` is called (after profile + resume + Google connect):
- If Google connected: triggers full pipeline (sync → classify → generate → email) as BackgroundTask
- If Google NOT connected (skip or demo): triggers `generate_pending_packs` for any seeded events
- This means by the time the user sees the dashboard, packs are already generating
- No manual "Sync Now" required on first visit

### Resume Handling
- Resume is parsed (raw text extracted) on upload
- `structure_resume()` is NOT called on upload (would exceed 10s limit)
- `resume_structured` will be None for new users — always check before use
- Fallback: use `resume_raw_text` directly, minimum 8000 chars window

## File Map
```
backend/
  main.py           — FastAPI app, router registration, DB init (lazy)
  routers/
    profile.py      — Onboarding, profile POST/GET
    resume.py       — Upload + raw parse only
    prep.py         — Prep pack generation
    email.py        — Send prep pack via Google OAuth
  services/
    prep_generator.py  — Claude API calls, uses resume_raw_text fallback
    email_sender.py    — Sends via Google OAuth, best-effort: errors are logged not raised
frontend/
  index.html        — Entire frontend, React CDN
```

## Prep Pack Generation — READ BEFORE TOUCHING prep_generator.py

### What a prep pack must be
The prep pack is the core product. It must read like a senior career coach wrote it specifically for this candidate. If it could apply to any candidate, it is wrong.

Every section must reference the candidate's actual resume — real company names, real project names, real metrics, real dates. No placeholders. No generic advice.

### Required sections and minimum quality bar

| Section | Minimum |
|---|---|
| `meeting_summary` | 3–5 sentences. Names the meeting type, company, role, any mismatch between listed role and candidate's target role, and the strategic goal of the call. |
| `talking_points` | 5–6 items. Each item is 3+ sentences in second person coaching voice. Must reference specific resume details — project names, metrics, outcomes. |
| `expected_questions` | 6–8 items. Each has `question` and `suggested_answer`. Answers are 4+ sentences and reference the candidate's real background. |
| `questions_to_ask` | 5 sharp, role-specific questions the candidate should ask the interviewer. |
| `prep_checklist` | 8 specific actionable tasks to complete before the call — research, logistics, practice. |
| `caveats` | 3–6 honest flags about unknowns, assumptions, or things the candidate must verify before the call. |

### How the prompt must be constructed (do not change this pattern)
1. Pass the full `resume_raw_text` (min 8000 chars) as candidate context — never truncate to save tokens
2. Pass the meeting title, company, listed role, meeting type, attendees, and event description
3. Detect role mismatch explicitly — if the listed role differs from the candidate's target role, name it and instruct the model to address it in every section
4. Instruct the model: direct coaching voice, second person, no placeholders, no generic advice, reference specific resume details throughout
5. Instruct the model to return valid JSON only — no markdown fences, no preamble, start with `{`
6. Strip markdown fences before JSON parsing — the model sometimes wraps output in ```json despite instructions

### Token and timeout settings
- `max_tokens`: pulled from `settings.max_tokens` (default 4000, set via `MAX_TOKENS` env var)
  - 4000 is required to produce full-quality packs (~2800–3200 output tokens per pack)
  - 2500 truncates packs mid-section — do not go below 3500
  - If Vercel timeouts increase: lower `MAX_TOKENS` env var to 3500 first, then 3000
- Client timeout: 180s
- `max_retries`: 0
- Model: use `settings.claude_model` — do not hardcode

### JSON parsing — always strip fences
```python
raw = response.content[0].text.strip()
if raw.startswith("```"):
    raw = raw.split("```")[1]
    if raw.startswith("json"):
        raw = raw[4:]
    raw = raw.split("```")[0].strip()
result = json.loads(raw)
```

### What NOT to do
- Do not hardcode any resume content — personalization must come from `resume_raw_text` at runtime
- Do not cache or reuse a previously generated pack — every generation is a fresh Claude API call
- Do not reduce the prompt to save tokens — the resume context is what makes the pack personalized
- Do not change `max_tokens` above 2500 — it will cause timeouts

## Demo Architecture
- Demo seeds ONLY calendar events + classifications — NO static prep pack content
- Prep packs are generated via the REAL Claude API pipeline (same as real users) from the user's uploaded resume
- Demo user has `resume_raw_text` set to a full resume (`_DEMO_RESUME_TEXT` in main.py)
- `serve_demo()` triggers `generate_pending_packs()` as a BackgroundTask on every visit
- `generate_pending_packs()` is idempotent — skips "done"/"generating", retries "failed"
- On first visit: user sees events with packs generating; they appear as packs complete
- The demo user profile must be pulled from the resume — do not hardcode a persona

## Environment Variables (all required on Vercel)
`DATABASE_URL`, `ANTHROPIC_API_KEY`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REDIRECT_URI`, `SMTP_PASS`, `SMTP_USER`, `TOKEN_ENCRYPTION_KEY`, `SECRET_KEY`

## Dev Commands
```bash
cd backend && uvicorn main:app --reload   # local dev
vercel dev                                 # test Vercel behavior locally
```
Point `DATABASE_URL` at Supabase even locally to catch pg8000 issues early.

## Before Every Deploy
- Verify lazy engine init not broken by new imports in main.py
- Check no new blocking calls added to request handlers
- Confirm `max_tokens` is pulled from `settings.max_tokens` (NOT hardcoded) in prep_generator.py
- Default is 4000 — do NOT lower below 3500 without testing for truncation first

## Bugs Fixed (do not reintroduce)
- `POST /api/profile` — NEVER use `response: Response = None` injection on Vercel. Use `JSONResponse` directly and call `resp.set_cookie()` on it. FastAPI's Response injection silently fails on Vercel serverless.
- `email_sender.py` — After email sends successfully, wrap DB logging in try/except. Never let a DB write failure after a successful send bubble up as a 500 to the frontend.
- Cookie conflicts — Users with stale HttpOnly cookies from previous sessions may get stuck. Frontend shows a clear error message telling users to clear cookies or use incognito.
- Session not updated on account switch — `POST /api/profile` switches the active user to the email-matched account but previously only issued a new session cookie for `not session_token`. Added `or user.id != session_user_id` so the cookie always follows the active account. Without this, Google sync and prep gen ran as the old (profile-less) account.
- JSON fence parsing — Claude API sometimes returns ```json fences despite instructions. Always strip before parsing.
- Timeout loop — max_tokens is now env-configurable (MAX_TOKENS). Default is 4000 (required for full pack quality). If timeouts increase, lower the env var — do not hardcode values in code.
- Logout — do NOT use FastAPI `Response` injection in `auth/logout`. Use `JSONResponse(...).delete_cookie()` directly (same as profile.py pattern).
- `/auth/demo` — must query by `User.email == "demo@jobprepagent.com"`, never `.first()`. First user in DB is not the demo user in multi-user scenarios.
- OAuth state — validated via HMAC signature, not in-memory dict. Works across Vercel instances. Do not revert to `_pending_states`.