const assert = require("node:assert/strict");
const { test } = require("node:test");
const vm = require("node:vm");
const fs = require("node:fs");
const path = require("node:path");
const context = vm.createContext({});
vm.runInContext(fs.readFileSync(path.join(__dirname, "../frontend/js/head-movement.js"), "utf8") + "\nvar motion = HeadMovement;", context);
const motion = context.motion;
function frame(time, {yaw=0, pitch=0, roll=0, x=100, y=100, scale=1} = {}) {
  return { time, face_detected: true, head_pose: {yaw, pitch, roll, facing_camera:true}, bbox: {x:x*scale, y:y*scale, w:200*scale, h:200*scale} };
}
function run(values, sensitivity="balanced") { return motion.analyzeFrames(values, sensitivity); }
const steady = () => Array.from({length:5}, (_,i) => frame(i*.2));

test("rotation, nods and tilts count as movement while still facing the camera", () => {
  for (const axis of ["yaw", "pitch", "roll"]) {
    const values = steady().concat([frame(1, {[axis]:8}), frame(1.2, {[axis]:16}), frame(1.4, {[axis]:8}), frame(1.6)]);
    assert.equal(run(values)[6].state, "moving");
    assert.equal(run(values)[8].state, "moving");
    assert.ok(values.every((f) => f.head_pose.facing_camera));
  }
});

test("position movement is normalized by face size and works at different resolutions", () => {
  for (const scale of [1, 2, .5]) {
    const values = Array.from({length:8}, (_,i) => frame(i*.2, {x:i < 5 ? 100 : 120, scale}));
    const result = run(values).at(-1);
    assert.equal(result.state, "moving");
    assert.equal(result.position, .1);
  }
});

test("sensitivity detects small motion at high and retains steady state at low", () => {
  const values = steady().concat([frame(1, {yaw:4}), frame(1.2, {yaw:4})]);
  assert.equal(run(values, "high").at(-1).state, "moving");
  assert.equal(run(values, "balanced").at(-1).state, "steady");
  assert.equal(run(values, "low").at(-1).state, "steady");
});

test("isolated detector jitter is suppressed and motion expires after stopping", () => {
  const jitter = steady().concat([frame(1, {yaw:50, x:150}), frame(1.2), frame(1.4)]);
  assert.equal(run(jitter).at(-1).state, "steady");
  const stop = steady().concat(Array.from({length:12}, (_,i) => frame(1+i*.2, {yaw:15})));
  assert.equal(run(stop)[6].state, "moving");
  assert.equal(run(stop).at(-1).state, "steady");
});

test("missing, invalid or stale tracks and backwards timestamps reset to uncertain", () => {
  const tracker = new motion.Tracker();
  steady().forEach((f) => tracker.update(f, f.time));
  assert.equal(tracker.update(frame(2), 2).state, "uncertain");
  assert.equal(tracker.update(frame(1), 1).state, "uncertain");
  for (const invalid of [{face_detected:false}, {...frame(0), head_pose:{yaw:NaN}}, {...frame(0), bbox:{x:0,y:0,w:0,h:1}}]) {
    assert.equal(tracker.update(invalid, 0).state, "uncertain");
  }
  assert.equal(tracker.samples.length, 0);
});

test("replay is deterministic when seeking and never treats duplicates as new movement", () => {
  const frames = steady().concat([frame(1, {yaw:15}), frame(1.2, {yaw:15})]);
  const states = run(frames);
  assert.equal(states[6].state, "moving");
  assert.equal(states[1].state, "uncertain");
  assert.deepEqual(run(frames), states);
  const tracker = new motion.Tracker();
  frames.forEach((f) => tracker.update(f, f.time));
  assert.equal(tracker.update(frame(1.2), 1.2).state, "moving");
  tracker.setSensitivity("low");
  assert.equal(tracker.result.state, "uncertain");
});

test("rotation crossing the angle boundary does not become a full turn", () => {
  const frames = Array.from({length:8}, (_,i) => frame(i*.2, {yaw:i < 5 ? 179 : -179}));
  assert.equal(run(frames).at(-1).state, "steady");
});
