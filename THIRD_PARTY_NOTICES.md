# Intel gaze inference

`backend/services/intel_gaze.py` adapts preprocessing and geometry from Intel Open Model Zoo's gaze estimation demo: face-box expansion, eye crop scale, roll alignment/compensation, eye-state output selection, and camera-relative angle convention. The Python integration, quality gates, temporal confirmation, API, and UI are modifications for ViewAndHired.

Upstream copyright: Copyright (C) 2018-2023 Intel Corporation.
Licensed under Apache License 2.0; see [Open Model Zoo license](third_party/open-model-zoo-LICENSE.txt).

Sources pinned to Open Model Zoo release `2023.0.0`:

- [Gaze demo source](https://github.com/openvinotoolkit/open_model_zoo/tree/2023.0.0/demos/gaze_estimation_demo/cpp)
- [Intel model manifests](https://github.com/openvinotoolkit/open_model_zoo/tree/2023.0.0/models/intel)
- [Public eye-state model manifest](https://github.com/openvinotoolkit/open_model_zoo/blob/2023.0.0/models/public/open-closed-eye-0001/model.yml)

The four Intel IR models are `gaze-estimation-adas-0002`, `head-pose-estimation-adas-0001`, `facial-landmarks-35-adas-0002`, and `face-detection-retail-0004`. The public ONNX eye-state model is `open-closed-eye-0001`, from OpenVINO Training Extensions under [Apache License 2.0](third_party/openvino-training-extensions-LICENSE.txt).

Model files are downloaded from Intel's official storage, verified against the published byte lengths and SHA-384 checksums in `backend/services/gaze_models.json`, and cached locally. Weights are not included in the repository. OpenVINO's runtime is installed separately through `requirements.txt` and retains its own license notices.
