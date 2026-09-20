# ViewAndHired Mock Interview

Local mock video interview practice app. Paste a job listing, generate role-specific questions, record a timed session, and get scored feedback on answer quality, speech delivery, and on-camera presence.

## Stack

- **Frontend:** vanilla HTML, CSS, JavaScript
- **Backend:** FastAPI, SQLite, local file storage
- **LLM:** OpenAI-compatible API (questions, answer scoring, feedback overview only)
- **ML:** faster-whisper (transcription), Parselmouth (descriptive voice measurements), Intel OpenVINO (gaze/head pose/eye state), MediaPipe blendshapes (neutral facial movement)

## Prerequisites

- Python 3.11+
- Audio/video decoding uses PyAV (installed with the requirements); no separate FFmpeg executable is required.
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

1. **Listing** — paste job post, optionally add your own questions (one per line), and generate a combined question bank
2. **Settings** — timers, question selection, recording mode
3. **Session** — prep/answer timers and continuous recording, with no calibration step
4. **Results** — scores, replay with face box, save interview
5. **History** — reopen saved sessions or retake with the same or different questions from their full saved bank

Saving an interview retains its entire generated and custom question bank, including questions not used in that attempt. Choose **Retake** in History or **Retake interview** on Results to select questions and adjust timers. Previous questions and settings are restored by default. Every retake creates a separate recording and result; the original attempt stays intact.

## Privacy

Recordings stay in `data/interviews/`. Job listing text, questions, transcripts, and derived scoring metrics are sent to the configured LLM for feedback. Audio and video files are processed locally. Live preview sends small JPEG frames only to this app's local `/api/gaze/frame` endpoint; these frames are not saved or sent to an external service.

## Notes

- Live preview and replay separate head orientation from estimated eye contact (Toward camera / Away / Uncertain).
- A separate **Head movement** overlay tracks rotation and position changes. Choose High, Balanced (default), or Low sensitivity in the session or replay; the preference is remembered. Existing recordings with saved head poses work immediately. See [movement thresholds and limitations](docs/head-movement.md).
- Speech "confidence" uses acoustic proxies (pitch, pauses, fillers).
- First analysis run downloads Whisper and face model weights.
- Run one server worker for this local app. Interrupted analyses become retryable after a restart.
- Existing recordings affected by the old stop-button bug (`answer_end: 0`) recover their final answer end from the audio duration. The results display a recovery notice; original timestamps remain unchanged.
- No calibration is required. Intel's trained gaze model uses both eye images and its matching head-pose model. First use downloads checksum-verified model files to `backend/weights/intel_gaze/`; subsequent inference is local and works offline.
- Live gaze targets five updates per second, allows only one frame request at a time, discards late results, and expires displayed labels after 800 ms. It pauses in hidden tabs and stops when recording finishes. A gaze failure does not stop recording.
- Blinks, blurred/small eye crops, multiple faces, large head turns and borderline gaze produce Uncertain. Eye contact is descriptive and does not affect the overall score: the model runs without calibration, but its camera-facing display thresholds have not been validated for this user's camera. Head direction remains separate.
- Historical calibration metadata is retained only to exclude old setup footage from summaries. New analysis uses the trained model for all recordings. See [implementation and verification notes](docs/real-time-gaze.md) and [third-party notices](THIRD_PARTY_NOTICES.md).
- Head direction and facial expressions do not establish eye contact or emotional state. MediaPipe itself [does not infer where a person is looking](https://github.com/google-ai-edge/mediapipe/blob/master/docs/solutions/iris.md).
- Feedback contains one strength and three next-attempt actions (at most 25 words each). Per-answer notes are limited to 45 words. Malformed or overlong feedback gets one automatic repair attempt.

## Verification

The [competency framework implementation](docs/competency-framework-implementation.md) separates neutral delivery observations from transcript-only behavioral evidence. In version 4, answer quality is the only overall score; competency levels require a relevant question and quoted example, action, reasoning and outcome. Missing evidence is unscored. Re-analyze recording refreshes older results and backs up the previous analysis.

Run the regression suites from the project root:

```bash
python -m unittest discover -s tests -v
node --test tests/*.test.cjs
```

Verify real recordings using the installed local models without calling the feedback API or replacing saved results:

```bash
python -m scripts.verify_local_models INTERVIEW_ID [INTERVIEW_ID ...]
```

To regenerate complete results using the configured feedback API:

```bash
python -m scripts.reanalyze_recordings INTERVIEW_ID [INTERVIEW_ID ...]
```

The latter sends transcripts and scoring metrics to the configured API and replaces generated results. Previous JSON artifacts are backed up under the recording's `analysis_backups/` folder. Recording files and original timestamps are preserved.

Benchmark only the trained gaze pipeline on local recordings, without feedback API calls or replacing results:

```bash
python -m scripts.verify_gaze INTERVIEW_ID [INTERVIEW_ID ...]
```

This writes numeric diagnostics under `data/gaze_verification/`. Speed and unlabelled predictions do not establish eye-contact accuracy.
