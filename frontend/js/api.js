async function api(path, options = {}) {
  const response = await fetch(path, options);
  const contentType = response.headers.get("content-type") || "";
  const isJson = contentType.includes("application/json");
  const body = isJson ? await response.json() : await response.text();
  if (!response.ok) {
    const detail = isJson ? body.detail || JSON.stringify(body) : body;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return body;
}

function getQueryParam(name) {
  return new URLSearchParams(window.location.search).get(name);
}

function setSession(key, value) {
  sessionStorage.setItem(key, JSON.stringify(value));
}

function getSession(key, fallback = null) {
  const raw = sessionStorage.getItem(key);
  if (!raw) return fallback;
  try {
    return JSON.parse(raw);
  } catch {
    return fallback;
  }
}

function scoreColor(score) {
  if (score >= 75) return "var(--success)";
  if (score >= 50) return "var(--warning)";
  return "var(--danger)";
}

function renderScoreBar(container, score) {
  container.innerHTML = `
    <div class="score-bar"><span style="width:${Math.max(0, Math.min(100, score))}%; background:${scoreColor(score)}"></span></div>
  `;
}
