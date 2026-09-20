const assert = require("node:assert/strict");
const { test } = require("node:test");
const vm = require("node:vm");
const fs = require("node:fs");
const path = require("node:path");

async function setup() {
  const elements = new Map();
  const requests = [];
  const questions = [
    { id: "q1", question: "Other generated question", type: "technical", likelihood: 4 },
    { id: "custom1", question: "My <question>", source: "custom", type: "custom", likelihood: 3 },
    { id: "q2", question: "Previous generated question", type: "behavioral", likelihood: 5 },
  ];
  const context = vm.createContext({
    document: { getElementById(id) {
      if (!elements.has(id)) elements.set(id, {
        value: "", handlers: {}, inputs: [],
        addEventListener(event, callback) { this.handlers[event] = callback; },
        set innerHTML(markup) {
          this.markup = markup;
          this.inputs = [...markup.matchAll(/<input[^>]+data-id="([^"]+)"([^>]*)>/g)].map((match) => ({ dataset: { id: match[1] }, checked: match[2].includes("checked") }));
        },
        querySelectorAll(selector) { return selector === "input:checked" ? this.inputs.filter((i) => i.checked) : this.inputs; },
      });
      return elements.get(id);
    } },
    window: { location: { href: "" } },
    sessionStorage: { setItem() {} },
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, "../frontend/js/api.js"), "utf8"), context);
  context.getQueryParam = (name) => name === "retakeId" ? "original" : null;
  context.getSession = () => ({ id: "stale-session" });
  context.api = async (url, options) => {
    if (url === "/api/interviews/original") return {
      listing_id: "listing", question_bank_id: "bank", selected_questions: [questions[2], questions[1]],
      settings: { prep_seconds: 15, answer_seconds: 90, record_mode: "mic" },
    };
    if (url === "/api/listings/banks/bank") return { id: "bank", listing_id: "listing", questions };
    if (url === "/api/interviews") { requests.push(JSON.parse(options.body)); return { id: "new-attempt" }; }
    throw new Error(`Unexpected request ${url}`);
  };
  await vm.runInContext(fs.readFileSync(path.join(__dirname, "../frontend/js/settings.js"), "utf8"), context);
  return { elements, requests, context };
}

test("retake restores previous question order, settings and full bank", async () => {
  const { elements, requests, context } = await setup();
  assert.equal(elements.get("startBtn").disabled, false);
  assert.equal(elements.get("selectionMode").value, "predetermined");
  assert.equal(elements.get("prepSeconds").value, 15);
  assert.equal(elements.get("answerSeconds").value, 90);
  assert.equal(elements.get("recordMode").value, "mic");
  assert.equal(elements.get("questionCount").value, 2);
  assert.deepEqual(elements.get("pickQuestionsList").inputs.map((i) => i.dataset.id), ["q2", "custom1", "q1"]);
  assert.match(elements.get("pickQuestionsList").markup, /My &lt;question&gt;/);
  await elements.get("startBtn").handlers.click();
  assert.equal(requests[0].question_bank_id, "bank");
  assert.equal(requests[0].listing_id, "listing");
  assert.deepEqual(requests[0].settings.selected_question_ids, ["q2", "custom1"]);
  assert.equal(context.window.location.href, "/session?id=new-attempt");
});

test("retake can choose an unused question or restore the original selection", async () => {
  const { elements, requests } = await setup();
  elements.get("clearQuestionsBtn").handlers.click();
  assert.equal(elements.get("questionCount").value, 0);
  await elements.get("startBtn").handlers.click();
  assert.equal(requests.length, 0);
  const picker = elements.get("pickQuestionsList");
  picker.inputs.find((i) => i.dataset.id === "q1").checked = true;
  picker.handlers.change();
  await elements.get("startBtn").handlers.click();
  assert.equal(requests[0].settings.question_count, 1);
  assert.deepEqual(requests[0].settings.selected_question_ids, ["q1"]);
  elements.get("previousQuestionsBtn").handlers.click();
  assert.deepEqual(picker.querySelectorAll("input:checked").map((i) => i.dataset.id), ["q2", "custom1"]);
  assert.equal(elements.get("questionCount").value, 2);
});
