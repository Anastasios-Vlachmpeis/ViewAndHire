const listingId = getQueryParam("listingId") || getSession("listing")?.id;
const bankId = getQueryParam("bankId") || getSession("questionBank")?.id;

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

function renderPickList() {
  pickQuestionsList.innerHTML = questions
    .map(
      (q, idx) => `
      <label class="question-item">
        <input type="checkbox" data-id="${escapeHtml(q.id)}" ${idx < 5 ? "checked" : ""}>
        <span class="badge">${q.source === "custom" ? "Your question" : escapeHtml(q.type)}</span>
        ${q.source === "custom" ? "" : `<span class="badge">${escapeHtml(q.likelihood)}/5</span>`}
        ${escapeHtml(q.question)}
      </label>`
    )
    .join("");
}

selectionModeEl.addEventListener("change", () => {
  pickQuestionsWrap.hidden = selectionModeEl.value !== "predetermined";
});

async function loadBank() {
  if (!listingId || !bankId) {
    statusEl.textContent = "Missing listing or question bank. Start from the job listing page.";
    startBtn.disabled = true;
    return;
  }
  const bank = await api(`/api/listings/banks/${bankId}`);
  questions = bank.questions;
  setSession("questionBank", bank);
  renderPickList();
  pickQuestionsWrap.hidden = selectionModeEl.value !== "predetermined";
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
