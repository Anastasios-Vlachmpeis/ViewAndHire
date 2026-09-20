const historyList = document.getElementById("historyList");
const statusEl = document.getElementById("status");

function formatDate(iso) {
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

async function loadHistory() {
  const items = await api("/api/interviews?saved=true");
  if (!items.length) {
    historyList.innerHTML = "<p class='muted'>No saved interviews yet. Complete an interview and click Save on the results page.</p>";
    return;
  }
  historyList.innerHTML = items
    .map(
      (item) => `
      <div class="history-row">
        <div>
          <strong>${escapeHtml(item.role_title || "Interview")}</strong>
          ${item.company ? `<span class="muted"> @ ${escapeHtml(item.company)}</span>` : ""}
          <div class="muted">${escapeHtml(formatDate(item.created_at))} · ${item.question_count} questions practised · ${item.question_bank_count} in saved bank</div>
        </div>
        <div>
          <div style="font-weight:700; color:${scoreColor(item.aggregate_score || 0)}">${item.aggregate_score ?? "--"}</div>
          <a class="btn btn-secondary" href="/results?id=${encodeURIComponent(item.id)}">Open</a>
          <a class="btn btn-secondary" href="/settings?retakeId=${encodeURIComponent(item.id)}">Retake</a>
        </div>
      </div>`
    )
    .join("");
}

loadHistory().catch((err) => {
  statusEl.textContent = err.message;
});
