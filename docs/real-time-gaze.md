# Real-time eye-contact replacement

Implemented and checked 20 September 2026. Intel OpenVINO inference is installed and integrated into live capture, uploaded recording analysis, and the replay overlay. No per-user calibration is required.

Sessions start with question preparation. Historical calibration metadata is used only to exclude old setup footage from summaries. Eye-contact estimates are descriptive and do not contribute to the overall score. Head direction remains a separate estimate.

## Implemented model

Intel Open Model Zoo's `gaze-estimation-adas-0002` runs locally through OpenVINO. It takes left/right eye images and head yaw/pitch/roll and predicts a 3D gaze vector. This uses eye appearance rather than iris-centre geometry. The model has 1.882 million parameters and requires 0.139 GFLOPs; these describe the gaze network, not the whole detection pipeline. [Intel model specification](https://docs.openvino.ai/2023.3/omz_models_model_gaze_estimation_adas_0002.html)

The implementation follows the official face detector, 35-point landmarks, head-pose model, open/closed-eye model and gaze network, including square face expansion, 1.8-times eye crops, BGR input, roll alignment and compensation. It uses Intel's head angles, not MediaPipe's angles. [Official demo](https://github.com/openvinotoolkit/open_model_zoo/tree/2023.0.0/demos/gaze_estimation_demo/cpp)

Two upstream documentation inconsistencies were checked against the executable reference and real local frames: straight-ahead gaze uses the negative z-axis in `src/utils.cpp`, and eye-state output index 1 means open in `src/eye_state_estimator.cpp`. The public ONNX eye model also needs `(BGR - 127) / 255` preprocessing from its model manifest; that normalization is not applied to the four IR models. Model downloads are pinned by published byte lengths and SHA-384 hashes. Licenses and provenance are in [third-party notices](../THIRD_PARTY_NOTICES.md).

## Alternatives considered

| Candidate | Evidence | Assessment for this app |
| --- | --- | --- |
| [L2CS-Net](https://github.com/Ahmednull/L2CS-Net) | Original gaze-estimation implementation, pretrained models and CPU/GPU webcam example; predicts gaze pitch/yaw. | Useful comparison model. Requires measuring its latency and camera-facing false positives on our clips. |
| [MobileGaze](https://github.com/yakhyo/gaze-estimation) | Lightweight models and ONNX exports trained on Gaze360. Author reports 12.58° MAE for MobileOne S0 and 11.33° for ResNet-34. | Convenient deployment option. Those errors do not establish that small lens-versus-screen differences will be resolved. Benchmark figures across different datasets are not directly comparable. |
| [Rehg Lab eye-contact CNN](https://github.com/rehg-lab/eye-contact-cnn) | Direct eye-contact classifier, trained on facial images of young individuals in egocentric social-interaction footage; includes a webcam demo. | More directly targets the binary question, but adult laptop interviews are a domain shift. Its [license](https://github.com/rehg-lab/eye-contact-cnn/blob/master/LICENSE) states noncommercial research use only. Not selected as the app default. |

## Overlay behavior

Current implementation:

1. Run the trained model on clear, open eyes. Keep the head-direction output independent.
2. Normalize its gaze vector, compensate roll and calculate the angle to `(0, 0, -1)`, matching the reference demo.
3. Use provisional display bands: at most 5 degrees is Toward camera, at least 15 degrees is Away, and the middle band is Uncertain. These are engineering defaults, not measured confidence bounds or validated accuracy thresholds. Eye contact is excluded from scoring until labelled validation supports a scoring rule.
4. Reject blinks, missing eyes, severe blur and unreliable crops. Use short causal smoothing without carrying a positive label over a blink or tracking loss.
5. Live preview samples up to five frames per second through the local endpoint. Only one request is in flight; results older than 800 ms are discarded and displayed labels expire at that age. Hidden tabs stop sending frames. Finishing or leaving the page aborts the browser request. Live and replay both require two consecutive matching observations within 600 ms before showing a known state. Temporal state is separate for each stream; blinks immediately reset it.

Paused, ended or frozen video clears the live overlay instead of repeatedly analysing the same frame. The local endpoint accepts JPEGs only, with a 512 KiB encoded limit and 1280 by 720 decoded dimension limits. Frames are decoded/inferred in a worker thread, never persisted, and never sent to an external API. Model/network failures leave the overlay unavailable without interrupting recording. Recording analysis also preserves speech/answer processing if gaze inference fails.

## Runtime verification

On this machine, the five-model pipeline processed 625 and 422 sampled frames from the two existing recordings. Median inference was 10.55 and 10.63 ms; p95 was 11.63 and 11.70 ms. This excludes network transfer, rendering, and the separate expression pipeline. Full sampled-video checks including decoding took 10.64 and 7.11 seconds for approximately 149.5 and 101.0 seconds of recorded video. These observations support the five-update-per-second target on this machine, not a universal FPS claim.

Both recordings produced all three states. Uncertain samples included borderline angles, transitions, blurred eyes, blinks and unreliable face crops. A local eye-crop inspection confirmed that the reference demo's eye-state ordering distinguishes an open eye from an almost-closed eye. Diagnostics are written by `python -m scripts.verify_gaze` under `data/gaze_verification/`.

Intel reports a 6.95° mean angular error, with validation on only two held-out people from a 60-person internal dataset. That is limited evidence, not a guarantee for this user. Camera-versus-nearby-screen targets can differ by a comparable angle. None of these sources establishes perfect binary eye contact on arbitrary webcams. [Validation details](https://docs.openvino.ai/2023.3/omz_models_model_gaze_estimation_adas_0002.html)

## Remaining accuracy validation

Use short clips with known lens, screen, left/right and up/down targets, plus blinks, glasses and head turns. These are development evaluation clips, not a per-interview calibration ritual. Separate tuning clips from the final check clips. Record false camera-contact labels, missed contact, uncertain coverage, switching delay and end-to-end latency. Existing unlabelled interviews can test decoding and speed but cannot establish eye-contact accuracy. The eye-contact contribution remains excluded from the aggregate.
