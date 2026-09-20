const jobTextEl = document.getElementById("jobText");
const companyEl = document.getElementById("company");
const roleEl = document.getElementById("role");
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
        <span class="badge">${q.type}</span>
        <span class="badge">Likelihood ${q.likelihood}/5</span>
        <p><strong>${q.question}</strong></p>
        <p class="muted">${q.rationale}</p>
      </div>`
    )
    .join("");
}

generateBtn.addEventListener("click", async () => {
  const jobText = jobTextEl.value.trim();
  if (jobText.length < 20) {
    statusEl.textContent = "Please paste a longer job listing (at least 20 characters).";
    return;
  }
  generateBtn.disabled = true;
  statusEl.textContent = "Creating listing and generating questions...";
  try {
    const listing = await api("/api/listings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        job_text: jobText,
        company: companyEl.value.trim() || null,
        role_title: roleEl.value.trim() || null,
      }),
    });
    const bank = await api(`/api/listings/${listing.id}/questions`, { method: "POST" });
    setSession("listing", listing);
    setSession("questionBank", bank);
    renderQuestions(bank.questions);
    questionsCard.hidden = false;
    continueBtn.href = `/settings?listingId=${listing.id}&bankId=${bank.id}`;
    statusEl.textContent = `Generated ${bank.questions.length} questions.`;
  } catch (err) {
    statusEl.textContent = err.message;
  } finally {
    generateBtn.disabled = false;
  }
});
