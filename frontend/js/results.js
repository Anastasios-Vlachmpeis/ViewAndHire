const interviewId = getQueryParam("id") || getSession("interview")?.id;
const aggregateScores = document.getElementById("aggregateScores");
const weakPoints = document.getElementById("weakPoints");
const overview = document.getElementById("overview");
const timeline = document.getElementById("timeline");
const questionBreakdown = document.getElementById("questionBreakdown");
const saveBtn = document.getElementById("saveBtn");
const saveStatus = document.getElementById("saveStatus");
const replayVideo = document.getElementById("replayVideo");
const faceOverlay = document.getElementById("faceOverlay");
const overlayLabels = document.getElementById("overlayLabels");

let analysis = null;
let faceFrames = [];

function renderAggregate(agg) {
  const cards = [
    { label: "Overall", value: agg.overall },
    { label: "Answer quality", value: agg.answer_quality },
    { label: "Speech delivery", value: agg.speech_delivery },
    { label: "Eye contact (estimate)", value: analysis?.analysis_version >= 2 ? agg.face_gaze : null },
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
        <p class="muted">${escapeHtml(q.question.slice(0, 80))}${q.question.length > 80 ? "..." : ""}</p>
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
        <p>${escapeHtml(q.question)}</p>
        <div class="grid-2">
          <div>
            <strong>Answer quality: ${Math.round(q.answer_quality.overall)}</strong>
            <p class="muted">${escapeHtml(q.answer_quality.notes)}</p>
            <div>Adequacy ${Math.round(q.answer_quality.adequacy)}</div>
            <div>Specificity ${Math.round(q.answer_quality.specificity)}</div>
            <div>Structure ${Math.round(q.answer_quality.structure)}</div>
          </div>
          <div>
            <strong>Speech delivery: ${q.speech_delivery.score !== null ? Math.round(q.speech_delivery.score) : "N/A"}</strong>
            <p class="muted">${q.speech_delivery.notes || ""}</p>
            <div>Eye contact (estimate): ${q.face_gaze.eye_contact_ratio != null ? `${Math.round(q.face_gaze.eye_contact_ratio * 100)}% of clear samples` : "Uncertain"}</div>
            <div class="muted">Clear eye samples: ${Math.round((q.face_gaze.eye_contact_coverage || 0) * 100)}% of this answer</div>
            <div class="muted">Head facing camera: ${q.face_gaze.head_facing_ratio != null ? `${Math.round(q.face_gaze.head_facing_ratio * 100)}%` : "Uncertain"}</div>
          </div>
        </div>
        ${q.answer_quality.suggested_answer ? `<div class="suggested-answer">
          <h4>Suggested answer</h4>
          <p>${escapeHtml(q.answer_quality.suggested_answer)}</p>
          <p class="muted">Based on your answer. Fill in any brackets with your own details.</p>
        </div>` : ""}
        <details class="feedback-details">
          <summary>View transcript</summary>
          <p class="muted">${escapeHtml(q.transcript || "(no speech detected)")}</p>
        </details>
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
  let animationId;
  const draw = () => {
    const width = replayVideo.clientWidth;
    const height = replayVideo.clientHeight;

    const frame = nearestFrame(replayVideo.currentTime);
    if (frame && frame.face_detected && frame.bbox && width && height) {
      const videoWidth = replayVideo.videoWidth || width;
      const videoHeight = replayVideo.videoHeight || height;
      const scaleX = width / videoWidth;
      const scaleY = height / videoHeight;
      const x = frame.bbox.x * scaleX;
      const y = frame.bbox.y * scaleY;
      const w = frame.bbox.w * scaleX;
      const h = frame.bbox.h * scaleY;
      faceOverlay.hidden = false;
      faceOverlay.style.transform = `translate(${x}px, ${y}px)`;
      faceOverlay.style.width = `${w}px`;
      faceOverlay.style.height = `${h}px`;
      const eyeState = frame.eye_contact?.state || "uncertain";
      const eyeLabels = { toward_lens: "Toward camera", away: "Away", uncertain: "Uncertain" };
      const headFacing = frame.head_pose?.facing_camera;
      faceOverlay.style.setProperty("--tracking-color", eyeState === "toward_lens" ? "#22c55e" : eyeState === "away" ? "#f59e0b" : "#94a3b8");
      const emotion = frame.expression && frame.expression_confidence >= .5 ? frame.expression : "Uncertain";
      const labels = `Emotion (estimate): ${escapeHtml(emotion)}<br>Head facing camera: ${headFacing == null ? "Uncertain" : headFacing ? "Yes" : "No"}<br>Eye contact (estimate): ${eyeLabels[eyeState] || "Uncertain"}`;
      if (overlayLabels.innerHTML !== labels) overlayLabels.innerHTML = labels;
      // Attach above the face border, or inside its top edge near the video boundary.
      faceOverlay.dataset.labelInside = "false";
      faceOverlay.dataset.labelInside = String(y < overlayLabels.offsetHeight);
    } else {
      faceOverlay.hidden = true;
    }
    animationId = requestAnimationFrame(draw);
  };
  const start = () => {
    cancelAnimationFrame(animationId);
    draw();
  };
  replayVideo.addEventListener("loadedmetadata", start);
  if (replayVideo.readyState >= 1) start();
  window.addEventListener("pagehide", () => cancelAnimationFrame(animationId), { once: true });
}

saveBtn.addEventListener("click", async () => {
  saveBtn.disabled = true;
  try {
    const saved = await api(`/api/interviews/${interviewId}/save`, { method: "POST" });
    saveStatus.textContent = `Interview and all ${saved.question_bank_count} questions saved.`;
    saveBtn.textContent = "Saved";
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
  const retakeBtn = document.getElementById("retakeBtn");
  retakeBtn.href = `/settings?retakeId=${encodeURIComponent(interviewId)}`;
  retakeBtn.hidden = false;
  if (payload.interview.saved) {
    saveBtn.disabled = true;
    saveBtn.textContent = "Saved";
    saveStatus.textContent = "This interview and its full question bank are saved.";
  }
  analysis = payload.analysis;
  const warnings = document.getElementById("analysisWarnings");
  warnings.textContent = (analysis.warnings || []).map((warning) =>
    analysis.eye_contact_calibration?.status === "unavailable" && warning === analysis.eye_contact_calibration.reason
      ? "Eye-contact estimation is unavailable for this recording. Head direction is shown separately."
      : warning
  ).join(" ");
  warnings.hidden = !warnings.textContent;
  faceFrames = analysis.face_frames || [];
  renderAggregate(analysis.aggregate);
  renderWeakPoints(analysis.weak_points || []);
  overview.textContent = analysis.overview || "";
  overview.style.whiteSpace = "pre-line";
  renderTimeline(analysis.per_question || []);
  renderBreakdown(analysis.per_question || []);
  replayVideo.src = `/api/interviews/${interviewId}/media/recording.webm`;
  setupOverlay();
}

init().catch((err) => {
  overview.textContent = err.message;
});
