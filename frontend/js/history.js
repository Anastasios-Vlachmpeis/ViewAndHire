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
          <strong>${item.role_title || "Interview"}</strong>
          ${item.company ? `<span class="muted"> @ ${item.company}</span>` : ""}
          <div class="muted">${formatDate(item.created_at)} · ${item.settings.question_count} questions</div>
        </div>
        <div>
          <div style="font-weight:700; color:${scoreColor(item.aggregate_score || 0)}">${item.aggregate_score ?? "--"}</div>
          <a class="btn btn-secondary" href="/results?id=${item.id}">Open</a>
        </div>
      </div>`
    )
    .join("");
}

loadHistory().catch((err) => {
  statusEl.textContent = err.message;
});
