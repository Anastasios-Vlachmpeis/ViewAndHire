const interviewId = getQueryParam("id") || getSession("interview")?.id;
const phaseLabel = document.getElementById("phaseLabel");
const timerEl = document.getElementById("timer");
const questionMeta = document.getElementById("questionMeta");
const questionText = document.getElementById("questionText");
const previewWrap = document.getElementById("previewWrap");
const preview = document.getElementById("preview");
const startBtn = document.getElementById("startBtn");
const skipBtn = document.getElementById("skipBtn");
const stopBtn = document.getElementById("stopBtn");
const statusEl = document.getElementById("status");
const uploadCard = document.getElementById("uploadCard");
const uploadStatus = document.getElementById("uploadStatus");
const progressBar = document.getElementById("progressBar");
const progressMessage = document.getElementById("progressMessage");
const retryBtn = document.getElementById("retryBtn");
const liveGaze = new LiveGazeOverlay(preview, document.getElementById("liveFaceOverlay"),
  document.getElementById("liveGazeLabels"), document.getElementById("liveGazeStatus"));
startBtn.disabled = true;

let interview = null;
let mediaStream = null;
let mediaRecorder = null;
let chunks = [];
let questionIndex = 0;
let timerInterval = null;
let sessionStart = 0;
let phase = "idle";
let phaseEndsAt = 0;
let timestamps = [];
let pollTimer = null;
let recordingBlob = null;
let uploadSucceeded = false;

function formatTime(seconds) {
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

function nowSeconds() {
  return (performance.now() - sessionStart) / 1000;
}

async function setupMedia() {
  const mode = interview.settings.record_mode;
  const constraints = {
    audio: mode === "both" || mode === "mic" || mode === "camera",
    video: mode === "both" || mode === "camera",
  };
  mediaStream = await navigator.mediaDevices.getUserMedia(constraints);
  if (constraints.video) {
    previewWrap.hidden = false;
    preview.srcObject = mediaStream;
  }
}

function pickMimeType(mode) {
  const candidates =
    mode === "mic"
      ? ["audio/webm;codecs=opus", "audio/webm", "video/webm"]
      : ["video/webm;codecs=vp9,opus", "video/webm;codecs=vp8,opus", "video/webm"];
  return candidates.find((type) => MediaRecorder.isTypeSupported(type)) || "video/webm";
}

function startRecording() {
  chunks = [];
  const mimeType = pickMimeType(interview.settings.record_mode);
  mediaRecorder = new MediaRecorder(mediaStream, { mimeType });
  mediaRecorder.ondataavailable = (event) => {
    if (event.data.size > 0) chunks.push(event.data);
  };
  mediaRecorder.start(1000);
}

function stopRecording() {
  return new Promise((resolve) => {
    if (!mediaRecorder || mediaRecorder.state === "inactive") {
      resolve(new Blob(chunks, { type: "video/webm" }));
      return;
    }
    mediaRecorder.onstop = () => resolve(new Blob(chunks, { type: "video/webm" }));
    mediaRecorder.stop();
  });
}

function updateTimer() {
  const remaining = Math.max(0, Math.ceil((phaseEndsAt - performance.now()) / 1000));
  timerEl.textContent = formatTime(remaining);
  if (performance.now() >= phaseEndsAt) {
    onPhaseComplete();
  }
}

function beginPrep() {
  phase = "prep";
  statusEl.textContent = "Recording in progress.";
  skipBtn.textContent = "Skip question";
  stopBtn.textContent = "Finish & analyze";
  const q = interview.selected_questions[questionIndex];
  timestamps[questionIndex] = {
    question_id: q.id, question_index: questionIndex,
    prep_start: nowSeconds(), answer_start: 0, answer_end: 0,
  };
  phaseLabel.textContent = "Preparation";
  questionMeta.textContent = `Question ${questionIndex + 1} of ${interview.selected_questions.length}`;
  questionText.textContent = q.question;
  phaseEndsAt = performance.now() + interview.settings.prep_seconds * 1000;
  timerEl.textContent = formatTime(interview.settings.prep_seconds);
  skipBtn.hidden = false;
  stopBtn.hidden = false;
  clearInterval(timerInterval);
  timerInterval = setInterval(updateTimer, 200);
}

function beginAnswer() {
  phase = "answer";
  phaseLabel.textContent = "Answer";
  const ts = {
    question_id: interview.selected_questions[questionIndex].id,
    question_index: questionIndex,
    prep_start: timestamps[questionIndex]?.prep_start ?? nowSeconds(),
    answer_start: nowSeconds(),
    answer_end: 0,
  };
  timestamps[questionIndex] = ts;
  phaseEndsAt = performance.now() + interview.settings.answer_seconds * 1000;
  timerEl.textContent = formatTime(interview.settings.answer_seconds);
}

function onPhaseComplete() {
  clearInterval(timerInterval);
  if (phase === "prep") {
    if (!timestamps[questionIndex]) {
      timestamps[questionIndex] = {
        question_id: interview.selected_questions[questionIndex].id,
        question_index: questionIndex,
        prep_start: nowSeconds(),
        answer_start: 0,
        answer_end: 0,
      };
    }
    beginAnswer();
    timerInterval = setInterval(updateTimer, 200);
    return;
  }
  if (phase === "answer") {
    timestamps[questionIndex].answer_end = nowSeconds();
    questionIndex += 1;
    if (questionIndex >= interview.selected_questions.length) {
      finishInterview();
      return;
    }
    beginPrep();
  }
}

function skipQuestion() {
  if (phase !== "prep" && phase !== "answer") return;
  clearInterval(timerInterval);
  if (phase === "prep") {
    const skippedAt = nowSeconds();
    timestamps[questionIndex] = {
      question_id: interview.selected_questions[questionIndex].id,
      question_index: questionIndex,
      prep_start: timestamps[questionIndex].prep_start,
      answer_start: skippedAt,
      answer_end: skippedAt,
    };
  } else if (phase === "answer") {
    timestamps[questionIndex].answer_end = nowSeconds();
  }
  questionIndex += 1;
  if (questionIndex >= interview.selected_questions.length) {
    finishInterview();
    return;
  }
  beginPrep();
}

async function finishInterview() {
  if (phase === "done" || phase === "idle") return;
  clearInterval(timerInterval);
  const current = timestamps[questionIndex];
  if (current && phase === "answer") current.answer_end = nowSeconds();
  if (current && phase === "prep") current.answer_start = current.answer_end = nowSeconds();
  phase = "done";
  liveGaze.stop();
  phaseLabel.textContent = "Uploading";
  startBtn.hidden = true;
  skipBtn.hidden = true;
  stopBtn.hidden = true;
  uploadCard.hidden = false;
  statusEl.textContent = "Processing recording...";

  recordingBlob = await stopRecording();
  if (mediaStream) {
    mediaStream.getTracks().forEach((track) => track.stop());
  }

  try {
    await uploadRecording();
    pollProgress();
  } catch (err) {
    uploadStatus.classList.add("error");
    uploadStatus.textContent = `Upload failed: ${err.message}`;
    retryBtn.textContent = "Retry upload";
    retryBtn.hidden = false;
  }
}

async function uploadRecording() {
  const form = new FormData();
  form.append("recording", recordingBlob, "recording.webm");
  form.append("timestamps", JSON.stringify(timestamps));
  const response = await fetch(`/api/interviews/${interviewId}/upload`, { method: "POST", body: form });
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(error.detail || `Upload failed (${response.status})`);
  }
  uploadSucceeded = true;
}

async function pollProgress() {
  clearTimeout(pollTimer);
  const poll = async () => {
    try {
      const progress = await api(`/api/interviews/${interviewId}/progress`);
      progressBar.style.width = `${progress.percent}%`;
      progressMessage.textContent = progress.message;
      uploadStatus.textContent = progress.message;
      if (progress.stage === "error") {
        uploadStatus.classList.add("error");
        retryBtn.hidden = false;
        retryBtn.textContent = "Retry analysis";
        return;
      }
      if (progress.stage === "done") {
        window.location.href = `/results?id=${interviewId}`;
        return;
      }
      pollTimer = setTimeout(poll, 1500);
    } catch (err) {
      uploadStatus.textContent = `Unable to check analysis: ${err.message}. Retrying...`;
      pollTimer = setTimeout(poll, 3000);
    }
  };
  poll();
}

startBtn.addEventListener("click", async () => {
  startBtn.disabled = true;
  statusEl.textContent = "Requesting camera/microphone access...";
  try {
    await setupMedia();
    sessionStart = performance.now();
    startRecording();
    timestamps = [];
    questionIndex = 0;
    beginPrep();
    if (interview.settings.record_mode !== "mic") liveGaze.start();
  } catch (err) {
    liveGaze.stop();
    if (mediaStream) mediaStream.getTracks().forEach((track) => track.stop());
    statusEl.textContent = `Media error: ${err.message}`;
    startBtn.disabled = false;
  }
});

skipBtn.addEventListener("click", skipQuestion);
stopBtn.addEventListener("click", finishInterview);
window.addEventListener("pagehide", () => liveGaze.stop());
retryBtn.addEventListener("click", async () => {
  retryBtn.hidden = true;
  uploadStatus.classList.remove("error");
  uploadStatus.textContent = "Retrying analysis...";
  try {
    if (!uploadSucceeded) await uploadRecording();
    else await api(`/api/interviews/${interviewId}/analyze`, { method: "POST" });
    pollProgress();
  } catch (err) {
    uploadStatus.classList.add("error");
    uploadStatus.textContent = err.message;
    retryBtn.hidden = false;
  }
});

async function init() {
  if (!interviewId) {
    statusEl.textContent = "No interview session found.";
    startBtn.disabled = true;
    return;
  }
  interview = await api(`/api/interviews/${interviewId}`);
  setSession("interview", interview);
  if (interview.status === "complete") {
    window.location.href = `/results?id=${interviewId}`;
    return;
  }
  if (["uploaded", "analyzing", "error"].includes(interview.status)) {
    startBtn.hidden = true;
    uploadCard.hidden = false;
    uploadSucceeded = true;
    pollProgress();
    return;
  }
  questionText.textContent = "Press begin when you are ready.";
  questionMeta.textContent = `${interview.selected_questions.length} questions configured`;
  statusEl.textContent = "";
  startBtn.disabled = false;
}

init().catch((err) => {
  statusEl.textContent = err.message;
  startBtn.disabled = true;
});
