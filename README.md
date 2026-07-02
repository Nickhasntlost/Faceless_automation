# YouTube Shorts Automation Pipeline (Phase 1)

Standalone, faceless YouTube Shorts pipeline for an AI/Tech niche channel. Phase 1 is a **local, run-by-hand CLI**; Phase 2 (Cloud Run + Scheduler) is intentionally out of scope until Phase 1 passes its definition of done.

## Verified build-time pricing (2026-06-28)

| Service | Model / tier | Rate |
| --- | --- | --- |
| Script | `gemini-3.5-flash` | $1.50 / 1M input tokens, $9.00 / 1M output tokens |
| Video | `veo-3.1-lite-generate-preview` | $0.05/s (720p), $0.08/s (1080p) |
| Voice | Cloud TTS Studio | $160 / 1M characters |
| Thumbnail | `gemini-3.1-flash-image` | ~$0.067 / 1K image |

Sources: [Gemini API pricing](https://ai.google.dev/gemini-api/docs/pricing), [Cloud TTS pricing](https://cloud.google.com/text-to-speech/pricing).

Re-check with:

```bash
python scripts/verify_pricing.py
```

## Requirements

- Python 3.11+
- FFmpeg + ffprobe on `PATH`
- Google credentials:
  - `GEMINI_API_KEY` or Application Default Credentials for Gemini/Veo
  - Google Cloud credentials for Cloud TTS (`gcloud auth application-default login`)
  - YouTube OAuth client at `credentials/client_secrets.json` (for real uploads)

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

Optional: copy `.env.example` values into your environment.

## Run (Phase 1)

Mock run (no paid APIs — good for structure/quality-gate tests):

```bash
python main.py --mock --skip-upload
```

Real run (uses paid APIs; budget guard enforced before Module 4+):

```bash
python main.py --skip-upload
```

Real run with upload (private/unlisted by default in `config/pipeline_config.json`):

```bash
python main.py
```

Each run writes artifacts under `output/<run_id>/`, including:

- `script.json`
- `narration.mp3`, `word_timings.json`
- `clips/scene_XX.mp4`
- `assembly/final_short.mp4`
- `verification/caption_check.jpg`
- `quality_report.json`

Budget counter: `data/budget_counter.json` (threshold default $250 of $300 credit).

## Forced-failure tests (Definition of Done #2)

```bash
python main.py --mock --simulate-timeout
python main.py --mock --simulate-budget-breach
python main.py --mock --simulate-interrupt
pytest tests/test_pipeline.py -q
```

## Configuration

- `config/channel_identity.json` — niche, persona, tone (not hardcoded in source)
- `config/pipeline_config.json` — scene count, resolution, budget threshold, upload privacy
- `config/pricing_verified.json` — model IDs and unit costs verified at build time

## Modules

1. Script generator (`src/module1_script.py`)
2. Voice + per-word SSML marks (`src/module2_voice.py`)
3. Budget guard (`src/module3_budget_guard.py`) — wired before first paid video call
4. Veo 3.1 Lite clips (`src/module4_video.py`)
5. Caption burn-in + assembly (`src/module5_assembly.py`)
6. Thumbnail (`src/module6_thumbnail.py`) — disabled by default in Phase 1
7. YouTube uploader with `status.containsSyntheticMedia=true` (`src/module7_uploader.py`)
8. Quality gate (`src/module8_quality_gate.py`) — `PASS` / `REVIEW` / `FAIL` / `INCOMPLETE`

## Phase 2 (later)

Do not start until three consecutive successful local runs + forced-failure tests pass. Phase 2 adds containerization, Cloud Run, and Cloud Scheduler.
