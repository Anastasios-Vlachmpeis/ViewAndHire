const assert = require("node:assert/strict");
const { test } = require("node:test");
const vm = require("node:vm");
const fs = require("node:fs");
const path = require("node:path");

async function setup() {
  const elements = new Map();
  const requests = [];
  const additions = [];
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
    if (url === "/api/listings/banks/bank/custom-questions") {
      const texts = JSON.parse(options.body).questions;
      additions.push(texts);
      const added = texts.map((question) => ({ id: `custom${questions.length + 1}`, question, source: "custom", type: "custom", likelihood: 3 }));
      added.forEach((q, i) => { q.id += `_${i}`; });
      questions.push(...added);
      return { bank: { id: "bank", listing_id: "listing", questions }, added };
    }
    if (url === "/api/interviews") { requests.push(JSON.parse(options.body)); return { id: "new-attempt" }; }
    throw new Error(`Unexpected request ${url}`);
  };
  await vm.runInContext(fs.readFileSync(path.join(__dirname, "../frontend/js/settings.js"), "utf8"), context);
  return { elements, requests, additions, context };
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

test("adding questions preserves edited selection, selects additions, and keeps them on a second add", async () => {
  const s = await setup();
  const picker = s.elements.get("pickQuestionsList");
  s.elements.get("clearQuestionsBtn").handlers.click();
  picker.inputs.find((q) => q.dataset.id === "q1").checked = true;
  s.elements.get("newCustomQuestions").value = " My follow-up? \n\n";
  await s.elements.get("saveNewQuestionsBtn").handlers.click();
  assert.deepEqual(picker.querySelectorAll("input:checked").map((q) => q.dataset.id), ["q1", "custom4_0"]);
  s.elements.get("newCustomQuestions").value = "Another <question>?";
  await s.elements.get("saveNewQuestionsBtn").handlers.click();
  assert.deepEqual(picker.querySelectorAll("input:checked").map((q) => q.dataset.id), ["q1", "custom4_0", "custom5_0"]);
  assert.match(picker.markup, /Another &lt;question&gt;/);
  assert.equal(s.elements.get("questionCount").value, 3);
  assert.equal(s.elements.get("newCustomQuestions").value, "");
  await s.elements.get("startBtn").handlers.click();
  assert.deepEqual(s.requests[0].settings.selected_question_ids, ["q1", "custom4_0", "custom5_0"]);
});

test("starting a retake saves pending question text before creating the attempt", async () => {
  const s = await setup();
  s.elements.get("newCustomQuestions").value = "Pending question?";
  await s.elements.get("startBtn").handlers.click();
  assert.deepEqual(s.additions, [["Pending question?"]]);
  assert.deepEqual(s.requests[0].settings.selected_question_ids, ["q2", "custom1", "custom4_0"]);
});

test("failed additions retain text and selection and prevent starting until retried", async () => {
  const s = await setup();
  const api = s.context.api;
  s.context.api = async (url, options) => {
    if (url.endsWith("/custom-questions")) throw new Error("Could not save your question");
    return api(url, options);
  };
  s.elements.get("newCustomQuestions").value = "Keep this question?";
  await s.elements.get("startBtn").handlers.click();
  assert.equal(s.requests.length, 0);
  assert.equal(s.elements.get("newCustomQuestions").value, "Keep this question?");
  assert.match(s.elements.get("addQuestionsStatus").textContent, /Could not save/);
  assert.equal(s.elements.get("startBtn").disabled, false);
  s.context.api = api;
  await s.elements.get("startBtn").handlers.click();
  assert.equal(s.requests.length, 1);
});

test("saving blocks duplicate clicks and starting while the request is pending", async () => {
  const s = await setup();
  const api = s.context.api;
  let release;
  s.context.api = async (url, options) => {
    if (url.endsWith("/custom-questions")) await new Promise((resolve) => { release = resolve; });
    return api(url, options);
  };
  s.elements.get("newCustomQuestions").value = "One question?";
  const pending = s.elements.get("saveNewQuestionsBtn").handlers.click();
  assert.equal(s.elements.get("startBtn").disabled, true);
  await s.elements.get("saveNewQuestionsBtn").handlers.click();
  await s.elements.get("startBtn").handlers.click();
  assert.equal(s.requests.length, 0);
  release(); await pending;
  assert.equal(s.additions.length, 1);
  assert.equal(s.elements.get("startBtn").disabled, false);
});

test("random mode keeps its count and explains that added questions are optional", async () => {
  const s = await setup();
  s.elements.get("selectionMode").value = "random";
  s.elements.get("questionCount").value = 1;
  s.elements.get("selectionMode").handlers.change();
  s.elements.get("newCustomQuestions").value = "Random pool question?";
  await s.elements.get("saveNewQuestionsBtn").handlers.click();
  assert.equal(s.elements.get("questionCount").value, 1);
  assert.match(s.elements.get("addQuestionsStatus").textContent, /Random mode may choose/);
  assert.equal(s.elements.get("pickQuestionsWrap").hidden, true);
});
