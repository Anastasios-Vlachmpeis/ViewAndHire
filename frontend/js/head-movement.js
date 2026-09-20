/* Descriptive motion over a short window, independent of gaze and facial expressions. */
const HeadMovement = (() => {
  const presets = {
    high: { rotation: 3, position: .03 },
    balanced: { rotation: 6, position: .06 },
    low: { rotation: 10, position: .10 },
  };
  const median = (values) => [...values].sort((a, b) => a - b)[Math.floor(values.length / 2)];
  const angleDifference = (a, b) => Math.abs(((a - b + 540) % 360) - 180);
  const uncertain = () => ({ state: "uncertain", rotation: null, position: null });

  class Tracker {
    constructor(sensitivity = "balanced") {
      this.sensitivity = presets[sensitivity] ? sensitivity : "balanced";
      this.reset();
    }
    reset() {
      this.samples = [];
      this.filtered = [];
      this.lastTime = null;
      this.dimensions = null;
      this.result = uncertain();
    }
    setSensitivity(value) {
      this.sensitivity = presets[value] ? value : "balanced";
      this.reset();
    }
    update(frame, time) {
      const pose = frame?.head_pose, box = frame?.bbox;
      const values = [pose?.yaw, pose?.pitch, pose?.roll, box?.x, box?.y, box?.w, box?.h, time];
      if (!frame?.face_detected || !values.every(Number.isFinite) || box.w <= 0 || box.h <= 0) {
        this.reset();
        return this.result;
      }
      // Never compare across a lost track, seek, new stream size or long inference gap.
      const dimensions = `${frame.width || 0}:${frame.height || 0}`;
      if (this.lastTime !== null && (time < this.lastTime || time - this.lastTime > .6 || dimensions !== this.dimensions)) this.reset();
      if (time === this.lastTime) return this.result;
      this.lastTime = time;
      this.dimensions = dimensions;
      this.samples.push({ time, yaw: pose.yaw, pitch: pose.pitch, roll: pose.roll,
        x: box.x + box.w / 2, y: box.y + box.h / 2, size: Math.sqrt(box.w * box.h) });
      this.samples = this.samples.slice(-3);
      // Three-frame medians suppress isolated detector jumps; two filtered samples are needed.
      if (this.samples.length < 3) {
        this.result = uncertain();
        return this.result;
      }
      this.filtered.push({ time, ...Object.fromEntries(["yaw", "pitch", "roll", "x", "y", "size"].map((key) => [key, median(this.samples.map((s) => s[key]))])) });
      this.filtered = this.filtered.filter((sample) => time - sample.time <= .801);
      const filtered = this.filtered;
      if (filtered.length < 2 || time - filtered[0].time < .15) {
        this.result = uncertain();
        return this.result;
      }
      let rotation = 0, position = 0;
      for (let i = 0; i < filtered.length; i++) {
        for (let j = i + 1; j < filtered.length; j++) {
          const a = filtered[i], b = filtered[j];
          rotation = Math.max(rotation, ...["yaw", "pitch", "roll"].map((key) => angleDifference(a[key], b[key])));
          position = Math.max(position, Math.hypot(a.x - b.x, a.y - b.y) / ((a.size + b.size) / 2));
        }
      }
      const threshold = presets[this.sensitivity];
      this.result = { state: rotation >= threshold.rotation || position >= threshold.position ? "moving" : "steady", rotation, position };
      return this.result;
    }
  }

  function analyzeFrames(frames, sensitivity) {
    const tracker = new Tracker(sensitivity);
    return frames.map((frame) => tracker.update(frame, frame.time));
  }

  function bind(select, help, onChange) {
    let saved = "balanced";
    try { saved = localStorage.getItem("headMovementSensitivity") || saved; } catch (_) { /* Storage may be unavailable. */ }
    select.value = presets[saved] ? saved : "balanced";
    const change = () => {
      const value = presets[select.value] ? select.value : "balanced";
      const threshold = presets[value];
      help.textContent = `Detects smaller movements at High sensitivity. Current threshold: about ${threshold.rotation}° of rotation or ${Math.round(threshold.position * 100)}% of face size in position change. Descriptive only; no score impact.`;
      try { localStorage.setItem("headMovementSensitivity", value); } catch (_) { /* Keep the current page functional. */ }
      onChange(value);
    };
    select.addEventListener("change", change);
    change();
  }

  function label(result) {
    return { moving: "Moving", steady: "Steady", uncertain: "Uncertain" }[result?.state] || "Uncertain";
  }
  return { Tracker, analyzeFrames, bind, label, presets };
})();
