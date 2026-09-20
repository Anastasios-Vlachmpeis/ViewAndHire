/* Local frame inference, with one in-flight request and no stale positive labels. */
class LiveGazeOverlay {
  constructor(video, box, labels, status) {
    this.video = video;
    this.box = box;
    this.labels = labels;
    this.status = status;
    this.canvas = document.createElement("canvas");
    this.running = false;
    this.generation = 0;
    this.previous = null;
    this.previousTime = -Infinity;
    this.lastVideoTime = -1;
  }

  start() {
    this.stop();
    this.running = true;
    this.status.hidden = false;
    this.status.textContent = "Eye contact (estimate): Preparing analysis…";
    this.schedule(0);
  }

  stop() {
    this.running = false;
    this.generation += 1;
    clearTimeout(this.nextTick);
    clearTimeout(this.expiry);
    clearTimeout(this.deadline);
    this.controller?.abort();
    this.previous = null;
    this.previousTime = -Infinity;
    this.lastVideoTime = -1;
    this.box.hidden = true;
    this.status.hidden = true;
  }

  schedule(delay) {
    if (this.running) this.nextTick = setTimeout(() => this.tick(), delay);
  }

  unavailable(message = "Uncertain") {
    this.box.hidden = true;
    this.previous = null;
    this.previousTime = -Infinity;
    this.status.hidden = false;
    this.status.textContent = `Eye contact (estimate): ${message}`;
  }

  draw(result, capturedAt) {
    if (!result.face_detected || !result.bbox) {
      this.unavailable(result.eye_contact?.reason === "multiple_faces" ? "Uncertain — multiple faces" : "Uncertain — no clear face");
      return;
    }
    const rawState = result.eye_contact?.state || "uncertain";
    let state = rawState;
    if (state !== "uncertain" && (state !== this.previous || capturedAt - this.previousTime > 600 || capturedAt <= this.previousTime)) {
      state = "uncertain";
    }
    this.previous = rawState;
    this.previousTime = capturedAt;
    const labels = { toward_lens: "Toward camera", away: "Away", uncertain: "Uncertain" };
    const facing = result.head_pose?.facing_camera;
    const scaleX = this.video.clientWidth / result.width;
    const scaleY = this.video.clientHeight / result.height;
    const { x, y, w, h } = result.bbox;
    this.box.hidden = false;
    this.box.style.transform = `translate(${x * scaleX}px, ${y * scaleY}px)`;
    this.box.style.width = `${w * scaleX}px`;
    this.box.style.height = `${h * scaleY}px`;
    this.box.style.setProperty("--tracking-color", state === "toward_lens" ? "#22c55e" : state === "away" ? "#f59e0b" : "#94a3b8");
    const emotion = result.expression && result.expression_confidence >= .5 ? result.expression : "Uncertain";
    this.labels.textContent = `Emotion (estimate): ${emotion}\nHead facing camera: ${facing == null ? "Uncertain" : facing ? "Yes" : "No"}\nEye contact (estimate): ${labels[state] || "Uncertain"}`;
    this.box.dataset.labelInside = "false";
    this.box.dataset.labelInside = String(y * scaleY < this.labels.offsetHeight);
    this.status.textContent = "Live eye-contact estimate · Does not affect your score";
  }

  async tick() {
    if (!this.running) return;
    const generation = this.generation;
    if (document.hidden || this.video.paused || this.video.ended || this.video.readyState < 2 || !this.video.videoWidth || this.video.currentTime === this.lastVideoTime) {
      this.unavailable();
      this.schedule(200);
      return;
    }
    this.lastVideoTime = this.video.currentTime;
    const capturedAt = performance.now();
    try {
      const scale = Math.min(640 / this.video.videoWidth, 480 / this.video.videoHeight, 1);
      this.canvas.width = Math.round(this.video.videoWidth * scale);
      this.canvas.height = Math.round(this.video.videoHeight * scale);
      this.canvas.getContext("2d").drawImage(this.video, 0, 0, this.canvas.width, this.canvas.height);
      const blob = await new Promise((resolve) => this.canvas.toBlob(resolve, "image/jpeg", .75));
      if (!this.running || generation !== this.generation) return;
      if (!blob) throw new Error("Frame capture unavailable");
      this.controller = new AbortController();
      this.deadline = setTimeout(() => this.controller.abort(), 5000);
      const response = await fetch("/api/gaze/frame", {
        method: "POST", headers: { "Content-Type": "image/jpeg" }, body: blob, signal: this.controller.signal,
      });
      if (!response.ok) throw new Error("Gaze temporarily unavailable");
      const result = await response.json();
      if (!this.running || generation !== this.generation) return;
      const age = performance.now() - capturedAt;
      if (age > 800 || document.hidden || this.video.paused || this.video.ended) {
        this.unavailable("Uncertain — waiting for a fresh frame");
      } else {
        this.draw(result, capturedAt);
        clearTimeout(this.expiry);
        this.expiry = setTimeout(() => this.unavailable(), 800 - age);
      }
    } catch (error) {
      if (this.running && generation === this.generation) this.unavailable("Temporarily unavailable");
    } finally {
      if (generation === this.generation) {
        clearTimeout(this.deadline);
        this.controller = null;
        this.schedule(Math.max(0, 200 - (performance.now() - capturedAt)));
      }
    }
  }
}
