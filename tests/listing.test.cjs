const assert = require("node:assert/strict");
const { test } = require("node:test");
const vm = require("node:vm");
const fs = require("node:fs");
const path = require("node:path");

function setup(customText) {
  const elements = new Map();
  const requests = [];
  const context = vm.createContext({
    document: { getElementById(id) {
      if (!elements.has(id)) elements.set(id, { value: "", handlers: {}, addEventListener(event, callback) { this.handlers[event] = callback; } });
      return elements.get(id);
    } },
    sessionStorage: { setItem() {}, getItem() { return null; } },
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, "../frontend/js/api.js"), "utf8"), context);
  context.api = async (url, options) => {
    requests.push({ url, options });
    if (url === "/api/listings") return { id: "listing" };
    return { id: "bank", questions: [{ id: "custom1", source: "custom", type: "custom", likelihood: 3,
      question: '<img src=x onerror="alert(1)"> Explain a < b?', rationale: "Added by you." }] };
  };
  vm.runInContext(fs.readFileSync(path.join(__dirname, "../frontend/js/listing.js"), "utf8"), context);
  elements.get("jobText").value = "A software engineering job listing";
  elements.get("customQuestions").value = customText;
  return { elements, requests };
}

test("custom questions are trimmed and sent; their text renders literally", async () => {
  const { elements, requests } = setup(" First question? \r\n\r\n Second question?  ");
  await elements.get("generateBtn").handlers.click();
  assert.deepEqual(JSON.parse(requests[0].options.body).custom_questions, ["First question?", "Second question?"]);
  const rendered = elements.get("questionsList").innerHTML;
  assert.match(rendered, /Your question/);
  assert.match(rendered, /&lt;img/);
  assert.doesNotMatch(rendered, /<img|Likelihood/);
  assert.equal(elements.get("continueBtn").href, "/settings?listingId=listing&bankId=bank");
});

test("blank optional field submits an empty list", async () => {
  const { elements, requests } = setup("\n   \n");
  await elements.get("generateBtn").handlers.click();
  assert.deepEqual(JSON.parse(requests[0].options.body).custom_questions, []);
});

test("too many questions stops submission with an explanation", async () => {
  const { elements, requests } = setup(Array(21).fill("Question?").join("\n"));
  await elements.get("generateBtn").handlers.click();
  assert.equal(requests.length, 0);
  assert.match(elements.get("status").textContent, /up to 20/);
});
