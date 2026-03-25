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

### Resume Handling
- Resume is parsed (raw text extracted) on upload
- `structure_resume()` is NOT called on upload (would exceed 10s limit)
- `resume_structured` will be None for new users — always check before use
- Fallback: use `resume_raw_text` directly, minimum 8000 chars window

## File Map
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

## Known Fragile Areas
- `POST /api/profile` — breaks on Vercel if DB engine is not lazy or if new imports cause eager init
- Prep pack quality — directly tied to how much resume text is passed to Claude (use 8000+ chars)
- Email error toast — email_sender.py must catch exceptions internally, never bubble to frontend as 500
- "Add email" button — only show when `!authStatus?.google_connected`

## Environment Variables (all required on Vercel)
DATABASE_URL, ANTHROPIC_API_KEY, GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET

## Dev Commands
cd backend && uvicorn main:app --reload   # local dev
# Test Vercel behavior: use Vercel CLI — `vercel dev`
# DB: point DATABASE_URL at Supabase even locally to catch pg8000 issues early

## Before Every Deploy
- Verify: lazy engine init not broken by new imports in main.py
- Check: no new blocking calls added to request handlers
