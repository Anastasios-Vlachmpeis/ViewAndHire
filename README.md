# ViewAndHire Mock Interview

Local mock video interview practice app. Paste a job listing, generate role-specific questions, record a timed session, and get scored feedback on answer quality, speech delivery, and on-camera presence.

## Stack

- **Frontend:** vanilla HTML, CSS, JavaScript
- **Backend:** FastAPI, SQLite, local file storage
- **LLM:** OpenAI-compatible API (questions, answer scoring, feedback overview only)
- **ML:** faster-whisper (transcription), Parselmouth (voice), MediaPipe + OpenCV ONNX (face/gaze/expression)

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
3. **Session** — optional 40-second eye-contact calibration, prep/answer timers, continuous recording
4. **Results** — scores, replay with face box, save interview
5. **History** — reopen saved sessions or retake with the same or different questions from their full saved bank

Saving an interview retains its entire generated and custom question bank, including questions not used in that attempt. Choose **Retake** in History or **Retake interview** on Results to select questions and adjust timers. Previous questions and settings are restored by default. Every retake creates a separate recording and result; the original attempt stays intact.

## Privacy

Recordings stay in `data/interviews/`. Job listing text, questions, transcripts, and derived scoring metrics are sent to the configured LLM for feedback. Audio and video files are processed locally.

## Notes

- The video overlay separates head orientation from estimated eye contact (Toward lens / Away / Uncertain).
- Speech "confidence" uses acoustic proxies (pitch, pauses, fillers).
- First analysis run downloads Whisper and face model weights.
- Run one server worker for this local app. Interrupted analyses become retryable after a restart.
- Existing recordings affected by the old stop-button bug (`answer_end: 0`) recover their final answer end from the audio duration. The results display a recovery notice; original timestamps remain unchanged.
- Eye-contact calibration alternates lens/screen prompts, then repeats both as separate checks. Each step has a 3-second preparation countdown followed by 7 seconds of calibration; preparation is excluded from calibration samples. All setup footage is excluded from answer scoring. Skipping or failing calibration leaves eye contact uncertain, including on old recordings.
- Horizontal and vertical iris positions are compared with the user's lens reference. Small eyes, closed eyelids, large head changes, ambiguous positions and transitions produce Uncertain. Known eye samples must cover at least half the answer and contain at least 10 samples to contribute a score. Coverage is shown beside each estimate; unavailable eye scores are excluded and the overall score reweighted.
- Head direction and facial expressions do not contribute to the eye-contact score. Expressions do not establish emotional state. This geometric estimate is not a validated gaze tracker or hiring assessment; glasses, lighting, camera placement and head movement can reduce coverage or accuracy. Repeat checks test calibration consistency, not real-world accuracy. MediaPipe itself [does not infer where a person is looking](https://github.com/google-ai-edge/mediapipe/blob/master/docs/solutions/iris.md).
- Feedback contains one strength and three next-attempt actions (at most 25 words each). Per-answer notes are limited to 45 words. Malformed or overlong feedback gets one automatic repair attempt.

## Verification

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
