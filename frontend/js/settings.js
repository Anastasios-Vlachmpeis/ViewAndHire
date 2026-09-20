const retakeId = getQueryParam("retakeId");
let listingId = getQueryParam("listingId") || getSession("listing")?.id;
let bankId = getQueryParam("bankId") || getSession("questionBank")?.id;

const questionCountEl = document.getElementById("questionCount");
const selectionModeEl = document.getElementById("selectionMode");
const prepSecondsEl = document.getElementById("prepSeconds");
const answerSecondsEl = document.getElementById("answerSeconds");
const recordModeEl = document.getElementById("recordMode");
const pickQuestionsWrap = document.getElementById("pickQuestionsWrap");
const pickQuestionsList = document.getElementById("pickQuestionsList");
const startBtn = document.getElementById("startBtn");
const statusEl = document.getElementById("status");

let questions = [];
let previousIds = [];
startBtn.disabled = true;

function syncSelection() {
  const predetermined = selectionModeEl.value === "predetermined";
  pickQuestionsWrap.hidden = !predetermined;
  questionCountEl.disabled = predetermined;
  if (predetermined) {
    questionCountEl.value = pickQuestionsList.querySelectorAll("input:checked").length;
  } else {
    questionCountEl.value = Math.max(1, Math.min(Number(questionCountEl.value) || 1, questions.length, 20));
  }
}

function renderPickList() {
  pickQuestionsList.innerHTML = questions
    .map(
      (q, idx) => `
      <label class="question-item">
        <input type="checkbox" data-id="${escapeHtml(q.id)}" ${(retakeId ? previousIds.includes(q.id) : idx < 5) ? "checked" : ""}>
        <span class="badge">${q.source === "custom" ? "Your question" : escapeHtml(q.type)}</span>
        ${q.source === "custom" ? "" : `<span class="badge">${escapeHtml(q.likelihood)}/5</span>`}
        ${previousIds.includes(q.id) ? '<span class="badge">Used last time</span>' : ""}
        ${escapeHtml(q.question)}
      </label>`
    )
    .join("");
}

selectionModeEl.addEventListener("change", syncSelection);
pickQuestionsList.addEventListener("change", syncSelection);
document.getElementById("previousQuestionsBtn").addEventListener("click", () => {
  pickQuestionsList.querySelectorAll("input").forEach((input) => {
    input.checked = previousIds.includes(input.dataset.id);
  });
  syncSelection();
});
document.getElementById("clearQuestionsBtn").addEventListener("click", () => {
  pickQuestionsList.querySelectorAll("input").forEach((input) => { input.checked = false; });
  syncSelection();
});

async function loadBank() {
  if (retakeId) {
    const previous = await api(`/api/interviews/${encodeURIComponent(retakeId)}`);
    listingId = previous.listing_id;
    bankId = previous.question_bank_id;
    previousIds = previous.selected_questions.map((q) => q.id);
    selectionModeEl.value = "predetermined";
    prepSecondsEl.value = previous.settings.prep_seconds;
    answerSecondsEl.value = previous.settings.answer_seconds;
    recordModeEl.value = previous.settings.record_mode;
    document.getElementById("settingsHeading").textContent = "Retake interview";
    document.getElementById("retakeActions").hidden = false;
    startBtn.textContent = "Start retake";
  }
  if (!listingId || !bankId) {
    statusEl.textContent = "Missing listing or question bank. Start from the job listing page.";
    startBtn.disabled = true;
    return;
  }
  const bank = await api(`/api/listings/banks/${bankId}`);
  questions = bank.questions;
  if (bank.listing_id !== listingId) throw new Error("This question bank belongs to a different job listing.");
  if (!questions.length) throw new Error("This question bank is empty.");
  if (retakeId) {
    // Keep the original attempt's order when repeating its questions.
    const byId = new Map(questions.map((q) => [q.id, q]));
    if (previousIds.some((id) => !byId.has(id))) throw new Error("Some previous questions are missing from the saved bank.");
    questions = [...previousIds.map((id) => byId.get(id)), ...questions.filter((q) => !previousIds.includes(q.id))];
    const description = document.getElementById("retakeDescription");
    description.textContent = `Your previous questions are selected. Keep them or choose others from all ${questions.length} saved questions, including your own. This creates a new attempt and keeps your previous results.`;
    description.hidden = false;
  }
  setSession("questionBank", bank);
  renderPickList();
  questionCountEl.max = Math.min(20, questions.length);
  syncSelection();
  startBtn.disabled = false;
}

startBtn.addEventListener("click", async () => {
  const selectedIds = [...pickQuestionsList.querySelectorAll("input:checked")].map((el) => el.dataset.id);
  const settings = {
    question_count: Number(questionCountEl.value),
    selection_mode: selectionModeEl.value,
    prep_seconds: Number(prepSecondsEl.value),
    answer_seconds: Number(answerSecondsEl.value),
    record_mode: recordModeEl.value,
    selected_question_ids: selectedIds,
  };
  if (settings.selection_mode === "predetermined" && selectedIds.length === 0) {
    statusEl.textContent = "Select at least one question for predetermined mode.";
    return;
  }
  if (settings.selection_mode === "predetermined" && selectedIds.length > 20) {
    statusEl.textContent = "Select up to 20 questions for one attempt.";
    return;
  }
  if (settings.selection_mode === "predetermined") settings.question_count = selectedIds.length;
  startBtn.disabled = true;
  statusEl.textContent = "Creating interview session...";
  try {
    const interview = await api("/api/interviews", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        listing_id: listingId,
        question_bank_id: bankId,
        settings,
      }),
    });
    setSession("interview", interview);
    window.location.href = `/session?id=${interview.id}`;
  } catch (err) {
    statusEl.textContent = err.message;
    startBtn.disabled = false;
  }
});

loadBank().catch((err) => {
  statusEl.textContent = err.message;
  startBtn.disabled = true;
});
