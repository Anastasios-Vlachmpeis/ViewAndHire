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
const retryBtn = document.getElementById("retryBtn");

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

function formatTime(seconds) {
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

function nowSeconds() {
  return (Date.now() - sessionStart) / 1000;
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
  const remaining = Math.max(0, Math.ceil((phaseEndsAt - Date.now()) / 1000));
  timerEl.textContent = formatTime(remaining);
  if (Date.now() >= phaseEndsAt) {
    onPhaseComplete();
  }
}

function beginPrep() {
  phase = "prep";
  const q = interview.selected_questions[questionIndex];
  phaseLabel.textContent = "Preparation";
  questionMeta.textContent = `Question ${questionIndex + 1} of ${interview.selected_questions.length}`;
  questionText.textContent = q.question;
  phaseEndsAt = Date.now() + interview.settings.prep_seconds * 1000;
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
  phaseEndsAt = Date.now() + interview.settings.answer_seconds * 1000;
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
    timerInterval = setInterval(updateTimer, 200);
  }
}

function skipQuestion() {
  clearInterval(timerInterval);
  if (phase === "prep") {
    timestamps[questionIndex] = {
      question_id: interview.selected_questions[questionIndex].id,
      question_index: questionIndex,
      prep_start: nowSeconds(),
      answer_start: nowSeconds(),
      answer_end: nowSeconds(),
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
  timerInterval = setInterval(updateTimer, 200);
}

async function finishInterview() {
  clearInterval(timerInterval);
  phase = "done";
  phaseLabel.textContent = "Uploading";
  startBtn.hidden = true;
  skipBtn.hidden = true;
  stopBtn.hidden = true;
  uploadCard.hidden = false;
  statusEl.textContent = "Processing recording...";

  const blob = await stopRecording();
  if (mediaStream) {
    mediaStream.getTracks().forEach((track) => track.stop());
  }

  const form = new FormData();
  form.append("recording", blob, "recording.webm");
  form.append("timestamps", JSON.stringify(timestamps));

  try {
    await fetch(`/api/interviews/${interviewId}/upload`, { method: "POST", body: form });
    pollProgress();
  } catch (err) {
    uploadStatus.classList.add("error");
    uploadStatus.textContent = `Upload failed: ${err.message}`;
  }
}

async function pollProgress() {
  const poll = async () => {
    const progress = await api(`/api/interviews/${interviewId}/progress`);
    progressBar.style.width = `${progress.percent}%`;
    progressMessage.textContent = progress.message;
    uploadStatus.textContent = progress.message;
    if (progress.stage === "error") {
      uploadStatus.classList.add("error");
      retryBtn.hidden = false;
      return;
    }
    if (progress.stage === "done") {
      window.location.href = `/results?id=${interviewId}`;
      return;
    }
    setTimeout(poll, 1500);
  };
  poll();
}

startBtn.addEventListener("click", async () => {
  startBtn.disabled = true;
  statusEl.textContent = "Requesting camera/microphone access...";
  try {
    await setupMedia();
    sessionStart = Date.now();
    startRecording();
    timestamps = [];
    questionIndex = 0;
    timestamps[0] = {
      question_id: interview.selected_questions[0].id,
      question_index: 0,
      prep_start: nowSeconds(),
      answer_start: 0,
      answer_end: 0,
    };
    beginPrep();
  } catch (err) {
    statusEl.textContent = `Media error: ${err.message}`;
    startBtn.disabled = false;
  }
});

skipBtn.addEventListener("click", skipQuestion);
stopBtn.addEventListener("click", finishInterview);
retryBtn.addEventListener("click", async () => {
  retryBtn.hidden = true;
  uploadStatus.classList.remove("error");
  uploadStatus.textContent = "Retrying analysis...";
  try {
    await api(`/api/interviews/${interviewId}/analyze`, { method: "POST" });
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
  questionText.textContent = "Press begin when you are ready.";
  questionMeta.textContent = `${interview.selected_questions.length} questions configured`;
}

init().catch((err) => {
  statusEl.textContent = err.message;
  startBtn.disabled = true;
});
