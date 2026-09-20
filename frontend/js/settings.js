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
const saveNewQuestionsBtn = document.getElementById("saveNewQuestionsBtn");
const newCustomQuestions = document.getElementById("newCustomQuestions");
const addQuestionsStatus = document.getElementById("addQuestionsStatus");
const deleteQuestionStatus = document.getElementById("deleteQuestionStatus");
const undoDeleteBtn = document.getElementById("undoDeleteBtn");

let questions = [];
let previousIds = [];
let addingQuestions = false;
let creatingInterview = false;
let deletingQuestion = false;
let lastDeleted = null;
startBtn.disabled = true;
saveNewQuestionsBtn.disabled = true;

function syncSelection() {
  const predetermined = selectionModeEl.value === "predetermined";
  pickQuestionsWrap.hidden = false;
  pickQuestionsList.querySelectorAll("input").forEach((input) => { input.disabled = !predetermined; });
  document.getElementById("pickQuestionsHeading").textContent = predetermined ? "Select questions" : "Question bank";
  document.getElementById("pickQuestionsHelp").textContent = predetermined
    ? "Choose up to 20 questions. Every checked question will be included."
    : "Random mode chooses from this bank. Delete any questions you do not want included.";
  questionCountEl.disabled = predetermined;
  questionCountEl.max = Math.min(20, Math.max(1, questions.length));
  if (predetermined) {
    questionCountEl.value = pickQuestionsList.querySelectorAll("input:checked").length;
  } else {
    questionCountEl.value = questions.length ? Math.max(1, Math.min(Number(questionCountEl.value) || 1, questions.length, 20)) : 0;
  }
  startBtn.disabled = addingQuestions || creatingInterview || deletingQuestion || !questions.length;
}

function renderPickList(selectedIds = null) {
  pickQuestionsList.innerHTML = questions
    .map(
      (q, idx) => `
      <div class="question-item question-bank-row">
      <label>
        <input type="checkbox" data-id="${escapeHtml(q.id)}" ${(selectedIds ? selectedIds.has(q.id) : retakeId ? previousIds.includes(q.id) : idx < 5) ? "checked" : ""}>
        <span class="badge">${q.source === "custom" ? "Your question" : escapeHtml(q.type)}</span>
        ${q.source === "custom" ? "" : `<span class="badge">${escapeHtml(q.likelihood)}/5</span>`}
        ${previousIds.includes(q.id) ? '<span class="badge">Used last time</span>' : ""}
        ${escapeHtml(q.question)}
      </label>
      <button type="button" class="btn btn-danger" data-delete-id="${escapeHtml(q.id)}" aria-label="Delete question: ${escapeHtml(q.question)}">Delete</button>
      </div>`
    )
    .join("") || '<p class="muted">No questions left. Add a question above or undo the last deletion.</p>';
}

function updateBank(bank, selectedIds) {
  const byId = new Map(bank.questions.map((q) => [q.id, q]));
  questions = [...previousIds.map((id) => byId.get(id)).filter(Boolean), ...bank.questions.filter((q) => !previousIds.includes(q.id))];
  setSession("questionBank", bank);
  renderPickList(selectedIds);
  const description = document.getElementById("retakeDescription");
  if (retakeId) description.textContent = `Choose from ${questions.length} saved questions. Deleted questions stay in previous results but are excluded from future attempts.`;
  syncSelection();
}

async function changeQuestion(questionId, restore = false) {
  if (addingQuestions || creatingInterview || deletingQuestion) return;
  const selectedBefore = new Set([...pickQuestionsList.querySelectorAll("input:checked")].map((input) => input.dataset.id));
  deletingQuestion = true;
  startBtn.disabled = true;
  saveNewQuestionsBtn.disabled = true;
  undoDeleteBtn.disabled = true;
  deleteQuestionStatus.textContent = restore ? "Restoring question..." : "Deleting question...";
  try {
    const result = await api(`/api/listings/banks/${encodeURIComponent(bankId)}/questions/${encodeURIComponent(questionId)}${restore ? "/restore" : ""}`, { method: restore ? "POST" : "DELETE" });
    const selected = new Set([...pickQuestionsList.querySelectorAll("input:checked")].map((input) => input.dataset.id));
    if (restore) {
      if (lastDeleted?.selected && selected.size < 20) selected.add(questionId);
      lastDeleted = null;
    } else {
      lastDeleted = { id: questionId, selected: selectedBefore.has(questionId) };
      selected.delete(questionId);
    }
    updateBank(result.bank, selected);
    undoDeleteBtn.hidden = !lastDeleted;
    deleteQuestionStatus.textContent = restore ? "Question restored." : "Question deleted from this bank. Previous attempts are unchanged.";
  } catch (err) {
    deleteQuestionStatus.textContent = err.message;
  } finally {
    deletingQuestion = false;
    saveNewQuestionsBtn.disabled = false;
    undoDeleteBtn.disabled = false;
    syncSelection();
  }
}

pickQuestionsList.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-delete-id]");
  if (button) return changeQuestion(button.dataset.deleteId);
});
undoDeleteBtn.addEventListener("click", () => { if (lastDeleted) return changeQuestion(lastDeleted.id, true); });

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
async function addQuestions() {
  if (addingQuestions || creatingInterview || deletingQuestion) return false;
  const textarea = newCustomQuestions;
  const texts = textarea.value.split(/\r?\n/).map((question) => question.trim()).filter(Boolean);
  if (!texts.length) {
    addQuestionsStatus.textContent = "Type at least one question, one per line.";
    return false;
  }
  if (texts.length > 20 || texts.some((question) => question.length > 2000)) {
    addQuestionsStatus.textContent = "Add up to 20 questions, each 2,000 characters or fewer.";
    return false;
  }
  const saveBtn = saveNewQuestionsBtn;
  if (!bankId) {
    addQuestionsStatus.textContent = "Load an interview before adding questions.";
    return false;
  }
  saveBtn.disabled = true;
  startBtn.disabled = true;
  textarea.disabled = true;
  addingQuestions = true;
  addQuestionsStatus.textContent = "Adding your questions...";
  try {
    const result = await api(`/api/listings/banks/${encodeURIComponent(bankId)}/custom-questions`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ questions: texts }),
    });
    const addedIds = result.added.map((q) => q.id);
    // Read current choices after the request, including changes made while saving.
    const selectedIds = new Set([...pickQuestionsList.querySelectorAll("input:checked")].map((input) => input.dataset.id));
    if (selectionModeEl.value === "predetermined") {
      addedIds.forEach((id) => { if (selectedIds.size < 20) selectedIds.add(id); });
    }
    questions = result.bank.questions;
    if (previousIds.length) {
      const byId = new Map(questions.map((q) => [q.id, q]));
      questions = [...previousIds.map((id) => byId.get(id)).filter(Boolean), ...questions.filter((q) => !previousIds.includes(q.id))];
    }
    setSession("questionBank", result.bank);
    renderPickList(selectedIds);
    questionCountEl.max = Math.min(20, questions.length);
    textarea.value = "";
    const description = document.getElementById("retakeDescription");
    if (description && !description.hidden) {
      description.textContent = `Choose from all ${questions.length} saved questions, including your own. Use previous questions to restore the original selection. This creates a new attempt and keeps your previous results.`;
    }
    syncSelection();
    const selectedAdded = addedIds.filter((id) => selectedIds.has(id)).length;
    addQuestionsStatus.textContent = selectionModeEl.value === "random"
      ? `Added ${addedIds.length} to the question bank. Random mode may choose them; switch to predetermined mode to include specific questions.`
      : `Added ${addedIds.length} to the question bank; ${selectedAdded} selected for this attempt.` + (selectedAdded < addedIds.length ? " You can select up to 20 questions below." : "");
    return true;
  } catch (err) {
    addQuestionsStatus.textContent = err.message;
    return false;
  } finally {
    saveBtn.disabled = false;
    textarea.disabled = false;
    addingQuestions = false;
    syncSelection();
  }
}
saveNewQuestionsBtn.addEventListener("click", addQuestions);

async function loadBank() {
  if (retakeId) {
    const previous = await api(`/api/interviews/${encodeURIComponent(retakeId)}`);
    listingId = previous.listing_id;
    bankId = previous.question_bank_id;
    previousIds = previous.selected_questions.map((q) => q.id);
    selectionModeEl.value = "predetermined";
    questionCountEl.value = previous.settings.question_count || previousIds.length;
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
  if (retakeId) {
    // Keep the original attempt's order when repeating its questions.
    const byId = new Map(questions.map((q) => [q.id, q]));
    questions = [...previousIds.map((id) => byId.get(id)).filter(Boolean), ...questions.filter((q) => !previousIds.includes(q.id))];
    const description = document.getElementById("retakeDescription");
    description.textContent = `Available previous questions are selected. Choose from ${questions.length} saved questions. Deleted questions stay in previous results but are excluded from future attempts.`;
    description.hidden = false;
  }
  setSession("questionBank", bank);
  renderPickList();
  questionCountEl.max = Math.min(20, questions.length);
  syncSelection();
  saveNewQuestionsBtn.disabled = false;
}

startBtn.addEventListener("click", async () => {
  if (addingQuestions || creatingInterview || deletingQuestion) return;
  if (newCustomQuestions.value.trim() && !await addQuestions()) {
    statusEl.textContent = "Your questions could not be added. Check the message beside Add questions before starting.";
    return;
  }
  const selectedIds = [...pickQuestionsList.querySelectorAll("input:checked")].map((el) => el.dataset.id);
  if (!questions.length) {
    statusEl.textContent = "Add at least one question before starting.";
    return;
  }
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
  saveNewQuestionsBtn.disabled = true;
  creatingInterview = true;
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
    saveNewQuestionsBtn.disabled = false;
    creatingInterview = false;
  }
});

loadBank().catch((err) => {
  statusEl.textContent = err.message;
  startBtn.disabled = true;
});
