const jobTextEl = document.getElementById("jobText");
const companyEl = document.getElementById("company");
const roleEl = document.getElementById("role");
const customQuestionsEl = document.getElementById("customQuestions");
const generateBtn = document.getElementById("generateBtn");
const statusEl = document.getElementById("status");
const questionsCard = document.getElementById("questionsCard");
const questionsList = document.getElementById("questionsList");
const continueBtn = document.getElementById("continueBtn");

function renderQuestions(questions) {
  questionsList.innerHTML = questions
    .map(
      (q) => `
      <div class="question-item">
        <span class="badge">${q.source === "custom" ? "Your question" : escapeHtml(q.type)}</span>
        ${q.source === "custom" ? "" : `<span class="badge">Likelihood ${escapeHtml(q.likelihood)}/5</span>`}
        <p><strong>${escapeHtml(q.question)}</strong></p>
        <p class="muted">${escapeHtml(q.rationale)}</p>
      </div>`
    )
    .join("");
}

generateBtn.addEventListener("click", async () => {
  const jobText = jobTextEl.value.trim();
  const customQuestions = customQuestionsEl.value.split(/\r?\n/).map((question) => question.trim()).filter(Boolean);
  if (jobText.length < 20) {
    statusEl.textContent = "Please paste a longer job listing (at least 20 characters).";
    return;
  }
  if (customQuestions.length > 20 || customQuestions.some((question) => question.length > 2000)) {
    statusEl.textContent = "Add up to 20 questions, with no more than 2,000 characters per question.";
    return;
  }
  generateBtn.disabled = true;
  questionsCard.hidden = true;
  statusEl.textContent = "Creating listing and generating questions...";
  try {
    const listing = await api("/api/listings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        job_text: jobText,
        company: companyEl.value.trim() || null,
        role_title: roleEl.value.trim() || null,
        custom_questions: customQuestions,
      }),
    });
    const bank = await api(`/api/listings/${listing.id}/questions`, { method: "POST" });
    setSession("listing", listing);
    setSession("questionBank", bank);
    renderQuestions(bank.questions);
    questionsCard.hidden = false;
    continueBtn.href = `/settings?listingId=${listing.id}&bankId=${bank.id}`;
    const customCount = bank.questions.filter((q) => q.source === "custom").length;
    statusEl.textContent = customCount
      ? `${bank.questions.length} questions ready, including ${customCount} of your own.`
      : `Generated ${bank.questions.length} questions.`;
  } catch (err) {
    statusEl.textContent = err.message;
  } finally {
    generateBtn.disabled = false;
  }
});
