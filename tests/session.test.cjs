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
      stop() { this.state = "inactive"; this.ondataavailable({ data: new Blob(["recording"]) }); this.onstop(); }
    },
    Blob, FormData,
    getQueryParam: () => "test", getSession: () => null, setSession() {},
    window: { location: { href: "" } },
    api: async (url) => url.endsWith("/progress") ? { stage: "score", percent: 50, message: "Scoring" } : configured,
    fetch: async (url, options) => { requests.push({ url, options }); return { ok: true }; },
  });
  await vm.runInContext(source, context);
  return { context, elements, intervals, timeouts, requests,
    at(ms) { clock = ms; },
    run(code) { return vm.runInContext(code, context); },
    async start(calibrate = false) {
      await elements.get("startBtn").handlers.click();
      if (!calibrate && vm.runInContext("phase", context) === "calibration_prep") vm.runInContext("skipQuestion()", context);
      assert.equal(vm.runInContext("phase", context), calibrate && mode !== "mic" ? "calibration_prep" : "prep", elements.get("status").textContent);
    },
  };
}

test("calibration stays outside answers and uploads all four reference windows", async () => {
  const s = await session();
  await s.start(true);
  assert.equal(s.run("timestamps.length"), 0);
  for (const ms of [0, 10000, 20000, 30000]) {
    assert.equal(s.run("phase"), "calibration_prep");
    assert.equal(s.elements.get("timer").textContent, "00:03");
    s.at(ms + 3000); s.run("onPhaseComplete()");
    assert.equal(s.run("phase"), "calibration");
    assert.equal(s.elements.get("timer").textContent, "00:07");
    s.at(ms + 10000); s.run("onPhaseComplete()");
    assert.equal(s.intervals.size, 1);
  }
  assert.equal(s.run("phase"), "prep");
  assert.equal(s.run("timestamps[0].prep_start"), 40);
  s.at(41000); s.run("onPhaseComplete()");
  s.at(46000); await s.run("finishInterview()");
  const form = s.requests[0].options.body;
  assert.deepEqual(JSON.parse(form.get("calibration")), [
    { target: "lens", start: 3, end: 10 }, { target: "screen", start: 13, end: 20 },
    { target: "lens_check", start: 23, end: 30 }, { target: "screen_check", start: 33, end: 40 },
  ]);
  assert.equal(JSON.parse(form.get("timestamps"))[0].answer_start, 41);
});

test("skipping partial calibration clears it and retains recording-relative question times", async () => {
  const s = await session();
  await s.start(true);
  s.at(3000); s.run("onPhaseComplete()");
  s.at(4500); s.run("skipQuestion()");
  assert.equal(s.run("calibrationWindows.length"), 0);
  assert.equal(s.run("timestamps[0].prep_start"), 4.5);
  assert.equal(s.intervals.size, 1);
});

test("cancelling calibration does not upload and permits restarting", async () => {
  const s = await session();
  await s.start(true);
  s.at(1000); await s.run("finishInterview()");
  assert.equal(s.requests.length, 0);
  assert.equal(s.intervals.size, 0);
  assert.equal(s.run("phase"), "idle");
  assert.equal(s.elements.get("startBtn").disabled, false);
  await s.start(true);
  assert.equal(s.run("calibrationWindows.length"), 0);
  s.at(4000); s.run("onPhaseComplete()");
  assert.equal(s.run("calibrationWindows[0].start"), 3);
});

test("audio-only sessions bypass calibration", async () => {
  const s = await session("mic");
  await s.start(true);
  assert.equal(s.run("phase"), "prep");
  assert.equal(s.run("calibrationWindows.length"), 0);
});

test("a delayed background timer restarts calibration", async () => {
  const s = await session();
  await s.start(true);
  s.at(3000); s.run("onPhaseComplete()");
  s.at(20000); s.run("onPhaseComplete()");
  assert.equal(s.run("phase"), "calibration_prep");
  assert.equal(s.run("calibrationWindows.length"), 0);
});

test("stopping during an answer records its end and uploads once", async () => {
  const s = await session();
  await s.start();
  s.at(1000); s.run("onPhaseComplete()");
  s.at(6000); await s.run("finishInterview()");
  await s.run("finishInterview()");
  assert.equal(s.requests.length, 1);
  const timestamps = JSON.parse(s.requests[0].options.body.get("timestamps"));
  assert.equal(timestamps[0].answer_start, 1);
  assert.equal(timestamps[0].answer_end, 6);
  assert.equal(s.intervals.size, 0);
});

test("natural question transitions maintain exactly one timer", async () => {
  const s = await session();
  await s.start();
  s.at(1000); s.run("onPhaseComplete()");
  assert.equal(s.intervals.size, 1);
  s.at(16000); s.run("onPhaseComplete()");
  assert.equal(s.intervals.size, 1);
  assert.equal(s.run("timestamps[1].prep_start"), 16);
  s.at(17000); s.run("onPhaseComplete()");
  s.at(32000); s.run("onPhaseComplete()");
  await Promise.resolve(); await Promise.resolve();
  assert.equal(s.intervals.size, 0);
  assert.equal(s.run("timestamps[1].answer_end"), 32);
});

test("skip during preparation has zero answer duration", async () => {
  const s = await session();
  await s.start();
  s.at(500); s.run("skipQuestion()");
  assert.equal(s.run("timestamps[0].answer_start"), 0.5);
  assert.equal(s.run("timestamps[0].answer_end"), 0.5);
  assert.equal(s.run("timestamps[1].prep_start"), 0.5);
  assert.equal(s.intervals.size, 1);
});

test("stop during preparation creates an empty answer instead of a negative interval", async () => {
  const s = await session();
  await s.start();
  s.at(500); await s.run("finishInterview()");
  assert.equal(s.run("timestamps[0].answer_start"), 0.5);
  assert.equal(s.run("timestamps[0].answer_end"), 0.5);
});

test("upload HTTP errors offer upload retry and do not start polling", async () => {
  const s = await session();
  s.context.fetch = async () => ({ ok: false, status: 400, json: async () => ({ detail: "Invalid recording" }) });
  await s.start();
  s.at(200); await s.run("finishInterview()");
  assert.match(s.elements.get("uploadStatus").textContent, /Invalid recording/);
  assert.equal(s.elements.get("retryBtn").hidden, false);
  assert.equal(s.elements.get("retryBtn").textContent, "Retry upload");
  assert.equal(s.timeouts.size, 0);
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
