# ViewAndHire Mock Interview

Local mock video interview practice app. Paste a job listing, generate role-specific questions, record a timed session, and get scored feedback on answer quality, speech delivery, and on-camera presence.

## Stack

- **Frontend:** vanilla HTML, CSS, JavaScript
- **Backend:** FastAPI, SQLite, local file storage
- **LLM:** OpenAI-compatible API (questions, answer scoring, feedback overview only)
- **ML:** faster-whisper (transcription), Parselmouth (voice), MediaPipe + OpenCV ONNX (face/gaze/expression)

## Prerequisites

- Python 3.11+
- [FFmpeg](https://ffmpeg.org/) on your PATH (optional; PyAV fallback is used if missing)
- OpenAI-compatible API key

## Setup

```bash
cd ViewAndHire
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

Edit `.env` and set `OPENAI_API_KEY`.

## Run

```bash
uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000

## Flow

1. **Listing** — paste job post, generate questions
2. **Settings** — timers, question selection, recording mode
3. **Session** — prep/answer timers, continuous recording
4. **Results** — scores, replay with face box, save interview
5. **History** — reopen saved sessions

## Privacy

Recordings stay in `data/interviews/`. Only job listing text and transcripts are sent to the LLM.

## Notes

- Face/gaze scores use pretrained heuristics, not clinical assessment.
- Speech "confidence" uses acoustic proxies (pitch, pauses, fillers).
- First analysis run downloads Whisper and face model weights.
