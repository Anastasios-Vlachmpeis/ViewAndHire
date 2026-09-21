const assert = require("node:assert/strict");
const { test } = require("node:test");
const vm = require("node:vm");
const fs = require("node:fs");
const path = require("node:path");

const source = fs.readFileSync(path.join(__dirname, "../frontend/js/session.js"), "utf8");

async function session(mode = "both") {
  let clock = 0;
  let identifier = 0;
  const intervals = new Map();
  const timeouts = new Map();
  const elements = new Map();
  const requests = [];
  const configured = {
    id: "test", status: "pending", settings: { record_mode: mode, prep_seconds: 1, answer_seconds: 15 },
    selected_questions: [{ id: "q1", question: "First" }, { id: "q2", question: "Second" }],
  };
  const context = vm.createContext({
    performance: { now: () => clock },
    document: { getElementById(id) {
      if (!elements.has(id)) elements.set(id, {
        hidden: true, style: {}, classList: { add() {}, remove() {} },
        handlers: {}, addEventListener(event, fn) { this.handlers[event] = fn; },
        async play() { this.played = true; },
      });
      return elements.get(id);
    } },
    setInterval(fn) { const id = ++identifier; intervals.set(id, fn); return id; },
    clearInterval(id) { intervals.delete(id); },
    setTimeout(fn) { const id = ++identifier; timeouts.set(id, fn); return id; },
    clearTimeout(id) { timeouts.delete(id); },
    navigator: { mediaDevices: { getUserMedia: async () => ({ getTracks: () => [{ stop() {} }] }) } },
    MediaRecorder: class {
      static isTypeSupported() { return true; }
      constructor() { this.state = "inactive"; }
      start() { this.state = "recording"; }
      pause() { this.state = "paused"; }
      resume() { this.state = "recording"; }
      stop() { this.state = "inactive"; this.ondataavailable({ data: new Blob(["recording"]) }); this.onstop(); }
    },
    Blob, FormData,
    getQueryParam: () => "test", getSession: () => null, setSession() {},
    window: { location: { href: "" }, addEventListener() {} },
    LiveGazeOverlay: class { start() {} stop() {} setHeadSensitivity() {} },
    api: async (url) => url.endsWith("/progress") ? { stage: "score", percent: 50, message: "Scoring" } : configured,
    fetch: async (url, options) => { requests.push({ url, options }); return { ok: true }; },
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, "../frontend/js/head-movement.js"), "utf8"), context);
  await vm.runInContext(source, context);
  return { context, elements, intervals, timeouts, requests,
    at(ms) { clock = ms; },
    run(code) { return vm.runInContext(code, context); },
    async start() {
      await elements.get("startBtn").handlers.click();
      assert.equal(vm.runInContext("phase", context), "prep", elements.get("status").textContent);
    },
  };
}

test("every recording mode starts question preparation immediately without calibration", async () => {
  for (const mode of ["both", "camera", "mic"]) {
    const s = await session(mode);
    await s.start();
    assert.equal(s.run("timestamps[0].prep_start"), 0);
    assert.equal(s.elements.get("questionText").textContent, "First");
    assert.equal(s.elements.get("timer").textContent, "00:01");
    assert.equal(s.intervals.size, 1);
    assert.equal(s.run("mediaRecorder.state"), "inactive");
    if (mode !== "mic") {
      assert.equal(s.elements.get("previewWrap").hidden, false);
      assert.equal(s.elements.get("preview").played, true);
      assert.ok(s.elements.get("preview").srcObject);
    }
    s.at(1000); s.run("onPhaseComplete()");
    s.at(6000); await s.run("finishInterview()");
    const form = s.requests[0].options.body;
    assert.equal(form.has("calibration"), false);
    assert.equal(JSON.parse(form.get("timestamps"))[0].answer_start, 0);
  }
});

test("stopping during an answer records its end and uploads once", async () => {
  const s = await session();
  await s.start();
  s.at(1000); s.run("onPhaseComplete()");
  s.at(6000); await s.run("finishInterview()");
  await s.run("finishInterview()");
  assert.equal(s.requests.length, 1);
  const timestamps = JSON.parse(s.requests[0].options.body.get("timestamps"));
  assert.equal(timestamps[0].answer_start, 0);
  assert.equal(timestamps[0].answer_end, 5);
  assert.equal(s.intervals.size, 0);
});

test("natural question transitions maintain exactly one timer", async () => {
  const s = await session();
  await s.start();
  s.at(1000); s.run("onPhaseComplete()");
  assert.equal(s.run("mediaRecorder.state"), "recording");
  assert.equal(s.intervals.size, 1);
  s.at(16000); s.run("onPhaseComplete()");
  assert.equal(s.intervals.size, 1);
  assert.equal(s.run("timestamps[1].prep_start"), 15);
  assert.equal(s.run("mediaRecorder.state"), "paused");
  assert.equal(s.elements.get("previewWrap").hidden, false);
  s.at(17000); s.run("onPhaseComplete()");
  s.at(32000); s.run("onPhaseComplete()");
  await Promise.resolve(); await Promise.resolve();
  assert.equal(s.intervals.size, 0);
  assert.equal(s.run("timestamps[1].answer_start"), 15);
  assert.equal(s.run("timestamps[1].answer_end"), 30);
});

test("skip during preparation has zero answer duration", async () => {
  const s = await session();
  await s.start();
  s.at(500); s.run("skipQuestion()");
  assert.equal(s.run("timestamps[0].answer_start"), 0);
  assert.equal(s.run("timestamps[0].answer_end"), 0);
  assert.equal(s.run("timestamps[1].prep_start"), 0);
  assert.equal(s.run("mediaRecorder.state"), "inactive");
  assert.equal(s.intervals.size, 1);
});

test("stop before any answer avoids empty upload and allows another attempt", async () => {
  const s = await session();
  await s.start();
  s.at(500); await s.run("finishInterview()");
  assert.equal(s.run("timestamps[0].answer_start"), 0);
  assert.equal(s.run("timestamps[0].answer_end"), 0);
  assert.equal(s.requests.length, 0);
  assert.equal(s.run("phase"), "idle");
  assert.equal(s.elements.get("startBtn").disabled, false);
  await s.start();
  assert.equal(s.run("nowSeconds()"), 0);
});

test("upload HTTP errors offer upload retry and do not start polling", async () => {
  const s = await session();
  s.context.fetch = async () => ({ ok: false, status: 400, json: async () => ({ detail: "Invalid recording" }) });
  await s.start();
  s.at(1000); s.run("onPhaseComplete()");
  s.at(1200); await s.run("finishInterview()");
  assert.match(s.elements.get("uploadStatus").textContent, /Invalid recording/);
  assert.equal(s.elements.get("retryBtn").hidden, false);
  assert.equal(s.elements.get("retryBtn").textContent, "Retry upload");
  assert.equal(s.timeouts.size, 0);
});

test("thirty-second prep intervals stay out of the saved answer timeline", async () => {
  const s = await session();
  s.run("interview.settings.prep_seconds = 30");
  await s.start();
  s.at(30000); s.run("onPhaseComplete()");
  s.at(35000); s.run("skipQuestion()");
  assert.equal(s.run("mediaRecorder.state"), "paused");
  s.at(65000); s.run("onPhaseComplete()");
  s.at(72000); await s.run("finishInterview()");
  const times = JSON.parse(s.requests[0].options.body.get("timestamps"));
  assert.deepEqual(times.map(t => [t.prep_start, t.answer_start, t.answer_end]), [[0, 0, 5], [5, 5, 12]]);
});

test("finishing during later preparation preserves only the earlier answer", async () => {
  const s = await session();
  await s.start();
  s.at(1000); s.run("onPhaseComplete()");
  s.at(16000); s.run("onPhaseComplete()");
  s.at(16500); await s.run("finishInterview()");
  const times = JSON.parse(s.requests[0].options.body.get("timestamps"));
  assert.deepEqual(times.map(t => [t.answer_start, t.answer_end]), [[0, 15], [15, 15]]);
});

test("skipping every preparation never starts recording or uploads", async () => {
  const s = await session();
  await s.start();
  s.run("skipQuestion()"); s.run("skipQuestion()");
  await Promise.resolve(); await Promise.resolve();
  assert.equal(s.requests.length, 0);
  assert.equal(s.run("mediaRecorder.state"), "inactive");
  assert.equal(s.run("phase"), "idle");
});

test("progress updates and network failures both keep polling functional", async () => {
  const s = await session();
  await s.run("pollProgress()");
  assert.equal(s.elements.get("progressMessage").textContent, "Scoring");
  assert.equal(s.timeouts.size, 1);
  s.context.api = async () => { throw new Error("offline"); };
  await s.run("pollProgress()");
  assert.equal(s.timeouts.size, 1);
  assert.match(s.elements.get("uploadStatus").textContent, /Retrying/);
});

test("analysis error offers retry and success opens results", async () => {
  const s = await session();
  s.context.api = async () => ({ stage: "error", percent: 0, message: "failure" });
  await s.run("pollProgress()");
  assert.equal(s.elements.get("retryBtn").hidden, false);
  assert.equal(s.elements.get("retryBtn").textContent, "Retry analysis");
  assert.equal(s.timeouts.size, 0);
  s.context.api = async () => ({ stage: "done", percent: 100, message: "Complete" });
  await s.run("pollProgress()");
  assert.equal(s.context.window.location.href, "/results?id=test");
});
