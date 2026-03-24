# Job Prep Agent

An AI-powered agent that connects to your Google Calendar, detects upcoming job-related meetings, and automatically generates personalised preparation packs — delivered to your inbox before every interview, recruiter screen, or networking call.

**Live demo:** [job-prep-agent.vercel.app/demo](https://job-prep-agent.vercel.app/demo)

---

## What it does

Every time you have a job-related meeting coming up, the agent:

1. **Syncs your calendar** via Google Calendar API and scans upcoming events
2. **Classifies each event** using Claude — distinguishing interviews, recruiter screens, networking calls, and company intros from unrelated meetings, with a confidence score and reasoning
3. **Generates a tailored prep pack** for each job-related event, including:
   - Meeting summary and context
   - 4–6 concrete talking points based on your background and experience
   - 5–7 expected questions with suggested answers
   - 4–6 thoughtful questions to ask the interviewer
   - A 30-minute pre-meeting checklist
   - Caveats and assumptions flagged for your review
4. **Emails the prep pack** to you via Gmail, with deduplication so you only get fresh content
5. **Surfaces ambiguous events** in a review queue where you can override the classification

Everything is personalised to your profile — your name, target roles, background summary, key projects, and parsed resume.

---

## Demo

Visit **[job-prep-agent.vercel.app/demo](https://job-prep-agent.vercel.app/demo)** to explore the full product with pre-seeded data — no sign-up or Google OAuth required.

On the demo page you can:
- Browse three classified upcoming events (final round interview, networking coffee chat, recruiter screen)
- Read each full prep pack including talking points, expected Q&A, and a 30-min checklist
- Regenerate any prep pack live using your own Anthropic API key
- Enter your email to receive the prep pack in your inbox and experience the full email flow

---

## Tech stack

| Layer | Technology |
|---|---|
| Backend | FastAPI (Python) |
| AI | Anthropic Claude (`claude-sonnet-4-6`) |
| Database | SQLite via SQLAlchemy |
| Auth | Google OAuth 2.0 (Calendar + Gmail scopes) |
| Email | Gmail API (OAuth) with SMTP fallback |
| Scheduler | APScheduler (background sync every 30 min) |
| Frontend | React 18 (CDN, no build step) + TailwindCSS |
| Deployment | Vercel (serverless) |

---

## Running locally

### Prerequisites

- Python 3.11+
- An [Anthropic API key](https://console.anthropic.com)
- A Google Cloud project with OAuth 2.0 credentials (Calendar + Gmail scopes)

### Setup

```bash
git clone https://github.com/ifedj/job-prep-agent
cd job-prep-agent
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
```

Create a `.env` file in the project root:

```env
ANTHROPIC_API_KEY=sk-ant-...
GOOGLE_CLIENT_ID=your-client-id.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=your-client-secret
GOOGLE_REDIRECT_URI=http://localhost:8080/auth/google/callback
SECRET_KEY=your-secret-key
TOKEN_ENCRYPTION_KEY=your-fernet-key
DATABASE_URL=sqlite:///./job_prep.db
CLAUDE_MODEL=claude-sonnet-4-6

# Optional: SMTP fallback for sending emails without Google OAuth
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=yourapp@gmail.com
SMTP_PASS=your-16-char-app-password
```

Generate the security keys:
```bash
python -c "import secrets; print(secrets.token_hex(32))"
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

### Start the server

```bash
unset ANTHROPIC_API_KEY   # prevent empty shell var overriding .env
python run.py
```

Open [http://localhost:8080](http://localhost:8080).

### Seed test data (optional)

Populate three realistic demo events and generate prep packs without needing a real calendar:

```bash
unset ANTHROPIC_API_KEY && python3 test_workflow.py
```

---

## Google OAuth setup

1. Go to [Google Cloud Console](https://console.cloud.google.com) → APIs & Services → Credentials
2. Create an OAuth 2.0 Client ID (Web application)
3. Add `http://localhost:8080/auth/google/callback` as an authorised redirect URI
4. Enable the **Google Calendar API** and **Gmail API** for your project
5. Add your Google account as a test user under OAuth consent screen → Audience

---

## Deployment (Vercel)

The repo is configured for zero-config Vercel deployment via `vercel.json`. The bundled `job_prep.db` is copied to `/tmp` on cold start.

```bash
npm i -g vercel
vercel --prod
```

Set these environment variables in your Vercel project dashboard:

| Variable | Description |
|---|---|
| `ANTHROPIC_API_KEY` | Your Anthropic API key |
| `SECRET_KEY` | Random 32-byte hex string |
| `TOKEN_ENCRYPTION_KEY` | Fernet key for OAuth token encryption |
| `SMTP_USER` | Gmail address used to send prep packs |
| `SMTP_PASS` | Gmail App Password (16 characters, no spaces) |

---

## Classification thresholds

| Confidence | Behaviour |
|---|---|
| ≥ 85% | Auto-generate prep pack and email it |
| 65–85% | Flag as ambiguous — added to review queue |
| < 65% | Marked as not job-related, ignored |

Adjustable via `CLASSIFICATION_HIGH_CONFIDENCE` and `CLASSIFICATION_AMBIGUOUS_LOWER` in `.env`.

---

## Project structure

```
job-prep-agent/
├── backend/
│   ├── config.py              # Settings (pydantic-settings v2)
│   ├── database.py            # SQLAlchemy engine + session
│   ├── models.py              # ORM models
│   ├── schemas.py             # Pydantic response schemas
│   ├── security.py            # JWT + Fernet token encryption
│   ├── deps.py                # FastAPI dependencies
│   ├── main.py                # App factory, /demo route
│   └── routers/
│       ├── auth.py            # Google OAuth flow + demo login
│       ├── events.py          # Calendar event endpoints
│       ├── prep_packs.py      # Prep pack CRUD + regenerate
│       ├── profile.py         # User profile + resume upload
│       ├── review.py          # Ambiguous event review queue
│       └── sync.py            # Manual sync trigger
│   └── services/
│       ├── classifier.py      # Claude event classification
│       ├── prep_generator.py  # Claude prep pack generation
│       ├── email_sender.py    # Gmail + SMTP email delivery
│       ├── gcalendar.py       # Google Calendar sync
│       ├── ggmail.py          # Gmail API wrapper
│       ├── oauth.py           # OAuth token management + refresh
│       ├── resume_parser.py   # PDF resume parsing (pdfplumber)
│       └── scheduler.py       # APScheduler background sync
├── frontend/
│   └── index.html             # React SPA (CDN, no build step)
├── api/
│   └── index.py               # Vercel serverless entry point
├── tests/                     # pytest test suite
├── test_workflow.py           # End-to-end pipeline test script
├── run.py                     # Local dev server (uvicorn :8080)
├── vercel.json                # Vercel deployment config
└── requirements.txt
```

---

## Key design decisions

**Plain JSON prompts over tool_use for prep generation** — Claude's tool_use API caused server disconnects on Vercel serverless due to the large tool definition payload. Switched to a plain JSON prompt with an automatic repair step for truncated responses.

**Three-layer email deduplication** — DB unique constraint on `(user_id, event_id)`, content hash on the prep pack itself, and content hash on the outgoing email body. Prevents duplicate sends across scheduler re-runs, manual triggers, and retries.

**SMTP fallback for email** — Demo users and testers without Google OAuth can still receive prep packs via Gmail SMTP with an App Password, configured through environment variables.

**SQLite on Vercel** — Pre-seeded database is bundled in the repo and copied to `/tmp` on cold start. Gives full read/write within a serverless invocation without needing an external database for demo purposes.

**React CDN, no build step** — The entire frontend is a single `index.html` file. No npm, no bundler, no CI build pipeline required.

---

## License

MIT
