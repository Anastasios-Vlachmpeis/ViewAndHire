const assert = require("node:assert/strict");
const { test } = require("node:test");
const vm = require("node:vm");
const fs = require("node:fs");
const path = require("node:path");

test("suggested answers render safely and older results remain readable", () => {
  const elements = new Map();
  const context = vm.createContext({ URLSearchParams,
    window: { location: { search: "" } }, sessionStorage: { getItem: () => null },
    document: { getElementById(id) {
      if (!elements.has(id)) elements.set(id, { addEventListener() {}, style: {} });
      return elements.get(id);
    } },
  });
  for (const file of ["api.js", "results.js"]) vm.runInContext(fs.readFileSync(path.join(__dirname, "../frontend/js", file), "utf8"), context);
  const question = { question: "What did you build?", transcript: "A hackathon app", answer_quality: { overall: 60, notes: "Specific example", adequacy: 60, specificity: 60, structure: 60 }, speech_delivery: { score: null }, face_gaze: {} };
  context.renderBreakdown([question]);
  assert.doesNotMatch(elements.get("questionBreakdown").innerHTML, /Suggested answer/);
  question.answer_quality.suggested_answer = 'At the hackathon, we built <script>alert(1)</script>. [Add the outcome.]';
  context.renderBreakdown([question]);
  const html = elements.get("questionBreakdown").innerHTML;
  assert.match(html, /Suggested answer/);
  assert.match(html, /At the hackathon, we built &lt;script&gt;/);
  assert.doesNotMatch(html, /<script>/);
  assert.match(html, /\[Add the outcome\.\]/);
});
