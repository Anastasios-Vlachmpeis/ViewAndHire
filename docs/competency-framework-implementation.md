# Executive recommendation implementation

Implements the two-layer recommendation in [the research report](emotion-and-competency-frameworks.md), 20 September 2026.

## Observable delivery

Live and replay overlays show facial movement, independent head orientation and estimated gaze. The seven-class emotion network is no longer loaded or used. MediaPipe `output_face_blendshapes` supplies jaw opening, mouth-corner movement, brow raising and eyelid closure coefficients. See the [official Python guide](https://ai.google.dev/edge/mediapipe/solutions/vision/face_landmarker/python).

The display says Active when a selected coefficient is at least 0.5, Low otherwise, and Uncertain for missing or invalid outputs. These are provisional coefficient display bands, not confidence, inner emotion, a temporal movement rate, or a desirable expression target. Speaking can activate jaw and mouth coefficients. Legacy emotion labels are never converted into movement data.

Optional delivery details show measured speaking rate, pauses, pitch and volume variation, gaze coverage and head direction. Voice confidence proxies and population-based delivery scores have been removed. Delivery observations are not sent to the overview or competency assessor, and do not contribute to overall answer quality. Older results hide legacy delivery scores; history displays answer quality only.

## Competency evidence

Each original question and transcript goes to a separate evidence-only assessment of all five report dimensions. No face, audio features, proposed rewrite, or previous score is an input. Each dimension includes question fit and reasoning, exact transcript quotations with character spans, missing evidence and one short next-attempt action. The UI describes evidence in this answer, not traits of the user, and has no combined competency score.

The executive recommendation's strict evidence gate takes precedence over showing a numerical 0 or 1: wrong-fit questions are Not assessed; claims and incomplete examples are Insufficient evidence with a null level. A displayed level requires good question fit, exact example/action/reasoning/outcome evidence, and a proposed level of at least 2. Level 3 additionally requires reflection; otherwise it is capped at 2. Honest unresolved status may be outcome evidence. Quote matching checks provenance, not whether the example is true or semantically sufficient.

Outputs are provisional and marked `validated: false`; confidence is capped at moderate and is not a calibrated probability. Invalid schema or unsupported quotations trigger one repair attempt. Repeated failure leaves competency evidence unavailable without losing the rest of the analysis. Silent answers never trigger a competency API call.

Saved analyses use version 4. Older results remain readable and can be refreshed using Re-analyze recording. Both UI and command-line reanalysis back up the previous analysis JSON.

## Verification and remaining work

Tests cover quote provenance, complete-evidence gates, question-fit abstention, empty answers, model failures, modality isolation, unsafe text rendering, missing movement outputs and historical compatibility. `python -m scripts.verify_movement INTERVIEW_ID ...` compares the same sampled frames with blendshapes enabled and disabled, without external calls or replacing results.

This implementation does not establish competency validity. The report's independent human ratings, agreement checks, subgroup evaluation, repeated-attempt baselines and usefulness pilot remain evaluation work. No affect research mode, face-to-trait classifier, employer ranking, or hiring recommendation is added.

### Checks performed on 20 September 2026

- 71 Python tests and 29 browser-script tests passed.
- The configured feedback service was checked using synthetic examples only: a detailed setback produced supported regulation/resilience evidence; an assertion received no level; a generic A/B-test answer received no competency levels. These are smoke checks, not a labelled validation dataset.
- Browser inspection verified separate optional delivery details, quoted evidence, and question-fit abstention.
- Two saved recordings were read locally for matched MediaPipe benchmarks (75 and 51 sampled frames). Median with blendshapes was 8.99 / 6.69 ms; without was 10.05 / 6.97 ms. Off ran before on, so warm-up and scheduling effects prevent interpreting the small differences as an incremental speed benefit. Neither run includes gaze, decoding, or rendering.
- A saved frame passed the local live endpoint with gaze plus movement. Warm round trips were 58.4 and 38.5 ms; initial model load took about 1.07 s, which the browser's stale-frame rule discards.
- Verification left existing saved analyses unchanged. Local benchmarks did not send frames externally.
