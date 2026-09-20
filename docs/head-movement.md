# Head movement

Live preview and replay show **Head movement: Moving / Steady / Uncertain** separately from facial movement, head direction and estimated eye contact. It describes recent changes, not emotional state or answer quality, and never affects scores.

The sensitivity control is available on both pages and remembered in this browser:

| Sensitivity | Rotation threshold | Position threshold |
| --- | --- | --- |
| High | 3 degrees | 3% of face size |
| Balanced (default) | 6 degrees | 6% of face size |
| Low | 10 degrees | 10% of face size |

Choose High to pick up smaller movements. These are adjustable engineering thresholds, not validated accuracy claims.

## Calculation

The shared `frontend/js/head-movement.js` tracker uses yaw, pitch and roll plus the center of the detected face box. Position changes are divided by the average face size (square root of box area), so the same movement at different image resolutions has the same threshold. Either rotation or position can trigger Moving.

Three-sample median filtering suppresses isolated tracking jumps. The tracker measures the largest change between filtered samples over about 0.8 seconds. It needs three raw samples and at least two filtered samples spanning 0.15 seconds before reporting a state; at five updates per second this takes about 0.6 seconds. Filtering delays the response slightly, and Moving can remain briefly after a movement stops.

Missing or invalid poses, lost faces, gaps over 0.6 seconds, backwards timestamps and changes in image dimensions reset the tracker to Uncertain. Duplicate timestamps do not add samples. Live stale-frame handling also clears tracking.

Replay precomputes states from the saved chronological frames, so pausing or seeking cannot create movement. Changing sensitivity recalculates these states locally. Existing recordings with valid saved poses and face boxes need no new analysis, model download or feedback request. Recordings without that data show Uncertain.

## Limitations and verification

Position is relative to the image: moving the camera can also trigger movement. Keep the camera stationary. The measure tracks image-plane position and estimated rotation, not physical distance or movement toward/away from the camera. Pose estimation errors can still cause false detections.

Regression tests cover turns, nods, tilts, position changes, resolution normalization, sensitivity, isolated jitter, stopping, lost tracking, duplicate timestamps, deterministic replay and live overlay integration. A local check of three existing recordings found Moving states at every sensitivity, with progressively fewer at Balanced and Low. These checks establish functional behavior, not accuracy against human-labelled footage.
