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
let headMovementByFrame = new Map();
function rebuildHeadMovement() {
  const results = HeadMovement.analyzeFrames(faceFrames, "balanced");
  headMovementByFrame = new Map(faceFrames.map((frame, index) => [frame, results[index]]));
}

function renderAggregate(agg) {
  const cards = [
    { label: "Answer quality", value: agg.answer_quality },
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
  points = points.filter((p) => !/_(delivery|face)$/.test(p.metric));
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

function renderCompetencies(bundle) {
  if (!bundle) return '<p class="muted">Competency evidence was not assessed in this older analysis. Re-analyze to add it.</p>';
  return `<details class="feedback-details"><summary>Competency evidence in this answer</summary>
    <p class="muted">Practice feedback from your words only. These provisional rubrics describe evidence in an answer, not your personality or employability. No combined competency score.</p>
    ${Object.values(bundle.assessments || {}).map((item) => {
      if (item.status === "not_assessed") return `<section class="competency-evidence"><h4>${escapeHtml(item.label)}</h4><p><strong>Not assessed for this question</strong></p><p class="muted">${escapeHtml(item.question_fit_reason)}</p></section>`;
      const labels = { assessed: item.level === 3 ? "Strong evidence · level 3/3" : "Evidence with gaps · level 2/3", not_assessed: "Not assessed for this question", insufficient_evidence: "Insufficient evidence" };
      return `<section class="competency-evidence"><h4>${escapeHtml(item.label)}</h4>
        <p><strong>${labels[item.status] || "Insufficient evidence"}</strong></p>
        <p class="muted">${escapeHtml(item.question_fit_reason)} Confidence: ${escapeHtml(item.confidence)} (not a calibrated probability).</p>
        ${(item.evidence || []).map((span) => `<p><strong>${escapeHtml(span.anchor)}:</strong> <q>${escapeHtml(span.quote)}</q></p>`).join("")}
        ${item.missing_evidence?.length ? `<p><strong>Missing evidence:</strong> ${item.missing_evidence.map(escapeHtml).join(" ")}</p>` : ""}
        <p><strong>Next attempt:</strong> ${escapeHtml(item.coaching_action)}</p></section>`;
    }).join("")}</details>`;
}

function renderDelivery(q) {
  const features = q.speech_delivery?.features || {};
  const face = q.face_gaze || {};
  const number = (value, suffix) => Number.isFinite(value) ? `${value}${suffix}` : "Unavailable";
  return `<details class="feedback-details"><summary>Optional delivery observations</summary>
    <p class="muted">Observations only. These do not measure emotion, confidence or competency and do not affect answer scores. There is no ideal population target.</p>
    <p>Speaking rate during voiced time: ${number(features.speaking_rate_wps, " words/second")}</p>
    <p>Detected pauses: ${number(features.pause_count, "")}; average pause: ${number(features.mean_pause_duration, " seconds")}</p>
    <p>Pitch variation: ${number(features.f0_stdev, " Hz")}; volume variation: ${number(features.intensity_stdev, " dB")}</p>
    <p>Eye contact (estimate): ${face.eye_contact_ratio != null ? `${Math.round(face.eye_contact_ratio * 100)}% of clear samples` : "Uncertain"}; clear samples: ${Math.round((face.eye_contact_coverage || 0) * 100)}%</p>
    <p>Head facing camera: ${face.head_facing_ratio != null ? `${Math.round(face.head_facing_ratio * 100)}%` : "Uncertain"}</p>
    <p class="muted">Optional practice: try a pause between ideas, vary emphasis on a key point, or adjust camera framing. Compare with your own earlier attempts.</p>
  </details>`;
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
            ${renderDelivery(q)}
          </div>
        </div>
        ${renderCompetencies(q.competency_evidence)}
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
      const movement = { active: "Active", low: "Low", uncertain: "Uncertain" }[frame.facial_movement?.state] || "Uncertain";
      const labels = `Head movement: ${HeadMovement.label(headMovementByFrame.get(frame))}<br>Facial movement: ${movement}<br>Head facing camera: ${headFacing == null ? "Uncertain" : headFacing ? "Yes" : "No"}<br>Eye contact (estimate): ${eyeLabels[eyeState] || "Uncertain"}`;
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

document.getElementById("reanalyzeBtn").addEventListener("click", async () => {
  const button = document.getElementById("reanalyzeBtn");
  button.disabled = true;
  try {
    await api(`/api/interviews/${interviewId}/analyze`, { method: "POST" });
    window.location.href = `/session?id=${encodeURIComponent(interviewId)}`;
  } catch (err) {
    saveStatus.textContent = err.message;
    button.disabled = false;
  }
});

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
  rebuildHeadMovement();
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
