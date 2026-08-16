# Evernews

AI-powered media source tracing & contextual verification platform. Drop in a
short clip you're unsure about and see what we found: where it likely came
from, what it currently claims, and whether the two still agree, no "fake"
verdicts, just evidence you can judge for yourself.

## How it works

```text
Upload video + caption
        |
Keyframe extraction (ffmpeg)
        |
Visual match against the demo source index
        |
Caption -> event/location/date claims (deterministic keyword extraction)
        |
Compare claims vs. matched source -> classify -> confidence
        |
VerificationResult (headline, comparison, sources)
```

## Project layout

```text
backend/    FastAPI app: ingestion, keyframes, evidence/comparison services
frontend/   Next.js (App Router) + Tailwind UI
data/       Demo source index (data/demo_sources/) used for visual matching
```

## Prerequisites

- Python 3.11+ (see `backend/requirements.txt` for the dependency list)
- Node.js 18+
- **ffmpeg + ffprobe** on your `PATH`, required for keyframe extraction;
  without them, video uploads fail with a 422 (see Known limitations below)

## Run the backend

The virtual environment lives inside `backend/`, but every command below
still runs from the repo root so `backend.*` imports resolve correctly.

```bash
python -m venv backend/.venv          # first time only
source backend/.venv/Scripts/activate # Windows Git Bash; use backend/.venv/Scripts/Activate.ps1 in PowerShell,
                                       # or backend/.venv/bin/activate on macOS/Linux
pip install -r backend/requirements.txt

# build the demo source index (gitignored, regenerate whenever data/demo_sources/ changes)
python -m backend.scripts.index_demo_sources

# copy env vars and fill in any keys you have (all optional for the demo path)
cp .env.example .env

uvicorn backend.main:app --reload --port 8000
```

Health check: `curl http://localhost:8000/health`

Run tests: `pytest`

## Run the frontend

```bash
cd frontend
npm install
cp .env.local.example .env.local   # points at http://localhost:8000 by default
npm run dev
```

Open `http://localhost:3000`.

## Known limitations (current demo scope)

- **Claim extraction is keyword-based**, not the full Luna LLM pipeline (no
  API key configured yet). It resolves the demo's flood/Jakarta/Bangkok
  vocabulary correctly; anything outside that dictionary stays "unresolved"
  rather than guessed. See `backend/services/context/caption_claims.py`.
- **Visual matching uses the committed demo source index**
  (`data/demo_sources/`), not live web search, so it's a hybrid demo
  retrieval strategy rather than a full internet crawler.
- **No sample "try it risk-free" clips yet**, so the frontend upload flow is
  real, but no demo video files are bundled in the repo.
- Synchronous endpoint: no job-status polling. The frontend's "analyzing"
  animation covers the real (short) processing latency instead.
