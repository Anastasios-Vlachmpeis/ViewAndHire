const interviewId = getQueryParam("id") || getSession("interview")?.id;
const aggregateScores = document.getElementById("aggregateScores");
const weakPoints = document.getElementById("weakPoints");
const overview = document.getElementById("overview");
const timeline = document.getElementById("timeline");
const questionBreakdown = document.getElementById("questionBreakdown");
const saveBtn = document.getElementById("saveBtn");
const saveStatus = document.getElementById("saveStatus");
const replayVideo = document.getElementById("replayVideo");
const overlayCanvas = document.getElementById("overlayCanvas");
const overlayLabels = document.getElementById("overlayLabels");

let analysis = null;
let faceFrames = [];

function renderAggregate(agg) {
  const cards = [
    { label: "Overall", value: agg.overall },
    { label: "Answer quality", value: agg.answer_quality },
    { label: "Speech delivery", value: agg.speech_delivery },
    { label: "Face & gaze", value: agg.face_gaze },
  ];
  aggregateScores.innerHTML = cards
    .filter((c) => c.value !== null && c.value !== undefined)
    .map(
      (c) => `
      <div class="score-card">
        <div class="muted">${c.label}</div>
        <div class="value" style="color:${scoreColor(c.value)}">${Math.round(c.value)}</div>
      </div>`
    )
    .join("");
}

function renderWeakPoints(points) {
  if (!points.length) {
    weakPoints.innerHTML = "<p class='muted'>No weak points identified yet.</p>";
    return;
  }
  weakPoints.innerHTML = points
    .map(
      (p) => `
      <div class="question-item">
        <strong>${p.label}</strong> — score ${Math.round(p.score)}
        <div class="score-bar"><span style="width:${p.score}%; background:${scoreColor(p.score)}"></span></div>
      </div>`
    )
    .join("");
}

function renderTimeline(perQuestion) {
  timeline.innerHTML = perQuestion
    .map(
      (q, idx) => `
      <div class="timeline-item" data-index="${idx}" data-time="${q.timestamps.answer_start}">
        <strong>Q${idx + 1}</strong>
        <p class="muted">${q.question.slice(0, 80)}${q.question.length > 80 ? "..." : ""}</p>
        <div class="muted">Overall answer: ${Math.round(q.answer_quality.overall)}</div>
      </div>`
    )
    .join("");
  timeline.querySelectorAll(".timeline-item").forEach((el) => {
    el.addEventListener("click", () => {
      replayVideo.currentTime = Number(el.dataset.time);
      replayVideo.play();
    });
  });
}

function renderBreakdown(perQuestion) {
  questionBreakdown.innerHTML = perQuestion
    .map(
      (q, idx) => `
      <div class="card" style="margin-bottom:12px;">
        <h3>Question ${idx + 1}</h3>
        <p>${q.question}</p>
        <p class="muted"><em>Transcript:</em> ${q.transcript || "(no speech detected)"}</p>
        <div class="grid-2">
          <div>
            <strong>Answer quality: ${Math.round(q.answer_quality.overall)}</strong>
            <p class="muted">${q.answer_quality.notes}</p>
            <div>Adequacy ${Math.round(q.answer_quality.adequacy)}</div>
            <div>Specificity ${Math.round(q.answer_quality.specificity)}</div>
            <div>Structure ${Math.round(q.answer_quality.structure)}</div>
          </div>
          <div>
            <strong>Speech delivery: ${q.speech_delivery.score !== null ? Math.round(q.speech_delivery.score) : "N/A"}</strong>
            <p class="muted">${q.speech_delivery.notes || ""}</p>
            <div>Face & gaze: ${q.face_gaze.score !== null ? Math.round(q.face_gaze.score) : "N/A"}</div>
          </div>
        </div>
      </div>`
    )
    .join("");
}

function nearestFrame(time) {
  if (!faceFrames.length) return null;
  let best = faceFrames[0];
  let bestDiff = Math.abs(best.time - time);
  for (const frame of faceFrames) {
    const diff = Math.abs(frame.time - time);
    if (diff < bestDiff) {
      best = frame;
      bestDiff = diff;
    }
  }
  return bestDiff <= 0.25 ? best : null;
}

function setupOverlay() {
  const ctx = overlayCanvas.getContext("2d");
  const draw = () => {
    const width = replayVideo.clientWidth;
    const height = replayVideo.clientHeight;
    overlayCanvas.width = width;
    overlayCanvas.height = height;
    ctx.clearRect(0, 0, width, height);

    const frame = nearestFrame(replayVideo.currentTime);
    if (frame && frame.face_detected && frame.bbox) {
      const videoWidth = replayVideo.videoWidth || width;
      const videoHeight = replayVideo.videoHeight || height;
      const scaleX = width / videoWidth;
      const scaleY = height / videoHeight;
      const x = frame.bbox.x * scaleX;
      const y = frame.bbox.y * scaleY;
      const w = frame.bbox.w * scaleX;
      const h = frame.bbox.h * scaleY;
      ctx.strokeStyle = frame.looking_at_camera ? "#22c55e" : "#f59e0b";
      ctx.lineWidth = 3;
      ctx.strokeRect(x, y, w, h);
      overlayLabels.innerHTML = `
        Expression: ${frame.expression || "unknown"}<br>
        Looking at camera: ${frame.looking_at_camera ? "Yes" : "No"}<br>
        Time: ${replayVideo.currentTime.toFixed(1)}s
      `;
    } else {
      overlayLabels.textContent = "No face detected at this moment";
    }
    requestAnimationFrame(draw);
  };
  replayVideo.addEventListener("loadedmetadata", draw);
}

saveBtn.addEventListener("click", async () => {
  saveBtn.disabled = true;
  try {
    await api(`/api/interviews/${interviewId}/save`, { method: "POST" });
    saveStatus.textContent = "Interview saved.";
  } catch (err) {
    saveStatus.textContent = err.message;
    saveBtn.disabled = false;
  }
});

async function init() {
  if (!interviewId) {
    overview.textContent = "Missing interview id.";
    return;
  }
  const payload = await api(`/api/interviews/${interviewId}/results`);
  analysis = payload.analysis;
  faceFrames = analysis.face_frames || [];
  renderAggregate(analysis.aggregate);
  renderWeakPoints(analysis.weak_points || []);
  overview.textContent = analysis.overview || "";
  renderTimeline(analysis.per_question || []);
  renderBreakdown(analysis.per_question || []);
  replayVideo.src = `/api/interviews/${interviewId}/media/recording.webm`;
  setupOverlay();
}

init().catch((err) => {
  overview.textContent = err.message;
});
