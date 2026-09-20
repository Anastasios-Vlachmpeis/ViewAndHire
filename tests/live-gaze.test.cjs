const assert = require("node:assert/strict");
const { test } = require("node:test");
const vm = require("node:vm");
const fs = require("node:fs");
const path = require("node:path");

const source = fs.readFileSync(path.join(__dirname, "../frontend/js/live-gaze.js"), "utf8");
function harness() {
  let clock = 0, id = 0;
  const timers = new Map(), requests = [];
  const video = { currentTime: 0, paused: false, ended: false, readyState: 2, videoWidth: 640, videoHeight: 480, clientWidth: 640, clientHeight: 480 };
  const box = { hidden: true, style: { setProperty() {} }, dataset: {} };
  const labels = { textContent: "", offsetHeight: 60 }, status = { hidden: true };
  const document = { hidden: false, createElement: () => ({
    getContext: () => ({ drawImage() {} }), toBlob: (callback) => callback(new Blob(["frame"], { type: "image/jpeg" })),
  }) };
  const known = { face_detected: true, width: 640, height: 480, bbox: { x: 200, y: 100, w: 200, h: 200 },
    head_pose: { facing_camera: true }, eye_contact: { state: "toward_lens" } };
  const context = vm.createContext({ document, performance: { now: () => clock }, Blob, AbortController,
    setTimeout(fn, delay) { const key = ++id; timers.set(key, { fn, at: clock + delay }); return key; },
    clearTimeout(key) { timers.delete(key); },
    fetch: async (url, options) => { requests.push({ url, options }); return { ok: true, json: async () => known }; },
    video, box, labels, status,
  });
  vm.runInContext(source + "\nvar overlay = new LiveGazeOverlay(video, box, labels, status);", context);
  const overlay = context.overlay;
  return { context, document, requests, timers, overlay, labels, status, box, known, video,
    at(ms) { clock = ms; video.currentTime = ms / 1000; },
    async fire(key = overlay.nextTick) { const timer = timers.get(key); assert.ok(timer); timers.delete(key); return timer.fn(); },
  };
}

test("live overlay requires consecutive samples and expires a positive label", async () => {
  const s = harness(); s.overlay.start();
  await s.fire();
  assert.match(s.labels.textContent, /Uncertain/);
  s.at(200); await s.fire();
  assert.match(s.labels.textContent, /Toward camera/);
  assert.equal(s.box.style.transform, "translate(200px, 100px)");
  s.at(1001); await s.fire(s.overlay.expiry);
  assert.equal(s.box.hidden, true);
  assert.match(s.status.textContent, /Uncertain/);
});

test("one request stays in flight and stale responses never display contact", async () => {
  const s = harness(); let resolve;
  s.context.fetch = (url, options) => {
    s.requests.push({ url, options });
    return new Promise((done) => { resolve = done; });
  };
  s.overlay.start();
  const pending = s.fire(); await Promise.resolve(); await Promise.resolve();
  assert.equal(s.requests.length, 1);
  assert.equal(s.timers.has(s.overlay.nextTick), false);
  s.at(900); resolve({ ok: true, json: async () => s.known }); await pending;
  assert.equal(s.box.hidden, true);
  assert.match(s.status.textContent, /fresh frame/);
  assert.ok(s.timers.has(s.overlay.nextTick));
});

test("stopping aborts inference and discards any late reply", async () => {
  const s = harness(); let resolve;
  s.context.fetch = (url, options) => {
    s.requests.push({ url, options });
    return new Promise((done) => { resolve = done; });
  };
  s.overlay.start(); const pending = s.fire(); await Promise.resolve(); await Promise.resolve();
  s.overlay.stop();
  assert.equal(s.requests[0].options.signal.aborted, true);
  resolve({ ok: true, json: async () => s.known }); await pending;
  assert.equal(s.box.hidden, true);
  assert.equal(s.status.hidden, true);
  assert.equal(s.timers.size, 0);
});

test("blinks and tracking loss immediately clear contact", async () => {
  const s = harness(); s.overlay.start();
  await s.fire(); s.at(200); await s.fire();
  s.known.eye_contact.state = "uncertain";
  s.at(400); await s.fire();
  assert.match(s.labels.textContent, /Eye contact \(estimate\): Uncertain/);
  s.known.face_detected = false;
  s.at(600); await s.fire();
  assert.equal(s.box.hidden, true);
  assert.match(s.status.textContent, /no clear face/);
});

test("hidden tabs send no frames and server failures remain non-blocking", async () => {
  const s = harness(); s.document.hidden = true;
  s.overlay.start(); await s.fire();
  assert.equal(s.requests.length, 0);
  s.document.hidden = false;
  s.context.fetch = async () => ({ ok: false });
  s.at(200); await s.fire();
  assert.match(s.status.textContent, /Temporarily unavailable/);
  assert.ok(s.timers.has(s.overlay.nextTick));
  s.overlay.stop();
});

test("paused, ended and frozen video clear contact without sending duplicate frames", async () => {
  const s = harness(); s.overlay.start();
  await s.fire(); s.at(200); await s.fire();
  assert.match(s.labels.textContent, /Toward camera/);
  await s.fire();
  assert.equal(s.requests.length, 2);
  assert.equal(s.box.hidden, true);
  s.at(400); s.video.paused = true; await s.fire();
  s.at(600); s.video.paused = false; s.video.ended = true; await s.fire();
  assert.equal(s.requests.length, 2);
  s.at(800); s.video.ended = false; await s.fire();
  assert.match(s.labels.textContent, /Uncertain/);
  s.at(1000); await s.fire();
  assert.match(s.labels.textContent, /Toward camera/);
  s.overlay.stop();
});

test("movement labels use blendshapes and never reuse legacy emotion labels", async () => {
  const s = harness(); s.overlay.start();
  s.known.expression = "happy"; s.known.facial_movement = { state: "active" };
  await s.fire();
  assert.match(s.labels.textContent, /Facial movement: Active/);
  assert.doesNotMatch(s.labels.textContent, /happy|Emotion/);
  s.known.facial_movement = { state: "uncertain" };
  s.at(200); await s.fire();
  assert.match(s.labels.textContent, /Facial movement: Uncertain/);
  delete s.known.facial_movement;
  s.at(400); await s.fire();
  assert.match(s.labels.textContent, /Facial movement: Uncertain/);
  s.overlay.stop();
});
