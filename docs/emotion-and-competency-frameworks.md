# Emotion and competency frameworks for ViewAndHired

Research review, 20 September 2026

## Executive recommendation

Do not train or relabel a facial-emotion model to output **Self-Regulation and Calmness, Passion and Enthusiasm, Self-Awareness and Humility, Empathy and Social Awareness, or Positivity and Resilience**. These are competencies or dispositions, not facial-expression classes. A face-only mapping would create precise-looking but unsupported personality judgments.

Use two separate layers instead:

1. **Observable delivery layer:** local face movement, head pose, gaze, and acoustic measures. Describe only what was observed and use it for optional coaching, not competency scores.
2. **Competency evidence layer:** question-aware transcript analysis using behaviorally anchored rubrics. Require a concrete example, action, reasoning, and outcome before giving a score; report insufficient evidence otherwise.

For the current overlay, the best product default is to replace the seven-emotion word with either no affect label or a neutral **facial movement** view based on MediaPipe blendshapes. If an affect research mode is still wanted, EmotiEffLib's valence/arousal model is the best permissively licensed experiment. Neither output should feed the five competency scores.

This recommendation follows the central scientific limitation: facial movements carry useful social information, but the same movement can occur in different emotions and expressions vary by person, situation, and culture. The major 2019 evidence review therefore warns against directly inferring emotional state from facial configuration ([Barrett et al., APS](https://www.psychologicalscience.org/journals/pspi/1529100619832930/)).

## What the current implementation actually produces

ViewAndHired uses the OpenCV Zoo MobileFaceNet FER model to select one of seven expression labels at five sampled frames per second. The UI shows the label when its computed softmax value is at least 0.50. That number is **not calibrated probability or established certainty**; the repository correctly keeps expression out of scoring and tells the feedback model not to infer emotion, confidence, personality, or ability from facial or voice measures.

The current implementation already runs MediaPipe Face Landmarker, but `output_face_blendshapes` is not enabled. This makes blendshapes the lowest-cost upgrade: MediaPipe exposes 52 coefficients such as jaw open, mouth smile, brow movement, eye blink, and eye wide ([MediaPipe reference](https://ai.google.dev/edge/api/mediapipe/python/mp/tasks/vision/drawing_styles/face_landmarker/Blendshapes)). They describe visible movement more honestly than an inner-state label and help distinguish a talking mouth from smile-related movement.

## Additional candidates beyond the supplied comparison

| Candidate | Adds | Product fit | Decision |
|---|---|---|---|
| **AUCanvas** | Local ONNX action-unit detector using MediaPipe; its authors report more than 5 FPS on CPU | Fits the sampling target and is easy to prototype, but the project and model are explicitly non-commercial and still immature | Research benchmark only ([repository](https://github.com/awakening-ai/AUCanvas), [license](https://github.com/awakening-ai/AUCanvas/blob/main/LICENSE)) |
| **MorphCast Emotion AI SDK** | Browser-side processing, claimed 100+ affective signals, 10 FPS mobile and up to 30 FPS desktop | Frames can remain on-device, removing the cloud objection. It requires a license key, uses a proprietary engine, and creates vendor and legal dependence | Plausible commercial proof-of-concept, not the default ([SDK](https://ai-sdk.morphcast.com/v1.17/docs/index.html), [on-device terms](https://www.morphcast.com/terms-of-use/)) |
| **Noldus FaceReader 10** | Local live/video analysis, 20 AUs, valence/arousal, custom expressions, and optional voice analysis; typical live rate stated as 5–10 FPS | Strong research workbench and useful external comparator; commercial, Windows-oriented, not a redistributable model | Best paid validation/reference tool, not an embedded dependency ([capabilities](https://noldus.com/shared/resources/book/noldus-product-documentation/chapter/facereader/page/facereader-10-core-features-and-capabilities), [AU module](https://noldus.com/shared/resources/book/noldus-product-documentation/chapter/facereader/page/facereader-10-introduction-to-action-unit-classification)) |
| **AFFDEX 2.0 / iMotions** | AUs, basic expressions, sentimentality, confusion, blink, attention, and head pose | Commercial and more feature-rich, but still facial behavior rather than a validated measure of the five competencies | External benchmark only ([AFFDEX 2.0](https://imotions.com/support/document-library/affdex-2-0-a-real-time-facial-expression-analysis-toolkit/)) |
| **SpeechBrain wav2vec2 emotion model** | Local speech emotion labels from audio; Apache-2.0 model card | Large at about 378 MB and trained on acted dyadic IEMOCAP English speech. Domain shift to solo webcam answers is substantial | Offline experiment only; do not score competencies ([model card](https://huggingface.co/speechbrain/emotion-recognition-wav2vec2-IEMOCAP)) |
| **openSMILE / eGeMAPS** | Standardized prosodic and voice-quality features designed for affective voice research | More interpretable than a speech-emotion label, but current open-source terms exclude product use without a commercial license. Existing Parselmouth features cover much of the useful delivery layer | Use eGeMAPS as a research specification, or reproduce only independently justified features; do not add openSMILE to a product without licensing ([documentation](https://audeering.github.io/opensmile/about.html), [license](https://github.com/audeering/opensmile/blob/master/LICENSE)) |
| **Visage FaceAnalysis** | On-device seven-class emotion probability across desktop, mobile, HTML5, and embedded platforms | Broad platform support, but largely duplicates the current categorical output and is proprietary | No compelling upgrade for this use case ([documentation](https://docs.visagetechnologies.com/display/vtd/visage%7CSDK%2BFaceAnalysis)) |

These candidates do not change the earlier ranking. MediaPipe blendshapes remain the best immediate movement representation; EmotiEffLib remains the best lightweight permissive valence/arousal experiment. EmotiEffLib supports ONNX and reports 61.93% AffectNet-8 and 64.94% AffectNet-7 accuracy for its `enet_b0_8_va_mtl` model, with a 16 MB model and roughly 60 ms inference on the authors' phone benchmark ([official repository](https://github.com/sb-ai-lab/EmotiEffLib)). Those figures are dataset-specific and not evidence that the model measures interview traits.

Py-Feat v2 and OpenFace 3.0 are useful research comparators but unsuitable product defaults. Py-Feat's current multitask weight card marks the weights **research-only** despite the surrounding package being MIT, and OpenFace 3.0 explicitly limits the entire software to non-commercial research ([Py-Feat model card](https://github.com/cosanlab/py-feat/blob/main/feat/multitask/MODEL_CARD.md), [OpenFace 3.0 license](https://github.com/CMU-MultiComp-Lab/OpenFace-3.0/blob/main/LICENSE)).

## Catered ViewAndHired framework

Call this a **Behavioral Competency Evidence framework**, not emotion detection. Each dimension should be assessed only on questions capable of eliciting it. A generic technical answer should return “not enough evidence” for empathy or resilience rather than a low score.

Use a four-level behaviorally anchored scale:

- **0 — no evidence:** no relevant example, or the answer is too incomplete to assess.
- **1 — assertion:** claims the quality but gives little observable behavior.
- **2 — partial evidence:** gives a relevant situation and action, but reasoning, personal contribution, or outcome is unclear.
- **3 — strong evidence:** gives a specific situation, personal action and reasoning, result, and reflection appropriate to the competency.

This follows structured-interview practice: ask the same job-related questions and evaluate responses using the same predetermined standards. The U.S. Office of Personnel Management recommends competency-linked questions and behavioral examples that differentiate proficiency levels ([OPM overview](https://www.opm.gov/policy-data-oversight/assessment-and-selection/structured-interviews), [OPM guide](https://www.opm.gov/policy-data-oversight/assessment-and-selection/structured-interviews/guide.pdf)).

### 1. Self-Regulation and Calmness

**Evidence to seek in the answer**

- Recognizes pressure, uncertainty, or an emotional trigger without dramatizing it.
- Describes a deliberate regulation action: pausing, prioritizing, seeking data, reframing, or asking for help.
- Maintains decision quality and explains the trade-off.
- Reflects on recovery or what changed next time.

**Useful coaching signals, not score evidence**

- Speaking-rate and pause changes within the same person's session.
- Repeated restarts, unusually long pauses, or a sharp change in vocal variation.
- Recovery after a difficult question, measured against that person's own baseline.

**Do not infer** calmness from a neutral face, low pitch, stillness, lack of eye contact, or low arousal. These may reflect speaking style, disability, culture, camera conditions, or deliberate concentration.

### 2. Passion and Enthusiasm

**Evidence to seek in the answer**

- Gives role- or domain-specific reasons rather than generic excitement.
- Describes sustained voluntary effort, curiosity, depth, or follow-through.
- Connects motivation to the actual work and organization.
- Uses concrete details showing informed interest.

**Useful coaching signals, not score evidence**

- Within-person changes in pitch range, intensity variation, pace, and gesture activity.
- Flat or rushed delivery can trigger a suggestion to vary emphasis, never a judgment that passion is absent.

**Do not infer** passion from smiling, high arousal, loudness, extroversion, or fast speech.

### 3. Self-Awareness and Humility

**Evidence to seek in the answer**

- Identifies a real limitation, mistake, or uncertainty.
- Separates personal contribution from team contribution accurately.
- Explains feedback received and a concrete change made.
- Balances ownership with credit to others and avoids false modesty.

This dimension is almost entirely semantic and contextual. Facial emotion and voice affect should contribute nothing to its score.

### 4. Empathy and Social Awareness

**Evidence to seek in the answer**

- Identifies another person's or stakeholder's perspective and needs.
- Checks assumptions rather than claiming to know another person's feelings.
- Adapts communication or action to the context.
- Considers impact, inclusion, and the outcome for other people.

This requires an interpersonal or stakeholder question. A one-way recorded interview cannot directly observe listening, reciprocity, or live adaptation, so the result must be framed as **evidence in the answer**, not measured empathy.

### 5. Positivity and Resilience

**Evidence to seek in the answer**

- Describes a meaningful setback without minimizing it.
- Shows adaptive action, help-seeking, learning, or revised strategy.
- Demonstrates sustained effort while changing an ineffective approach.
- Gives an outcome or honest unresolved status and a transferable lesson.

**Useful coaching signals, not score evidence**

- Recovery in delivery after a hard question can be shown privately as a session pattern.
- Valence/arousal trajectories may be explored in research mode only, because positive expression is not resilience.

**Do not infer** resilience from smiling, positive-valence output, suppression of negative affect, or uninterrupted speech.

## Recommended scoring and user interface

For each competency, return:

```json
{
  "level": 0,
  "evidence": ["short transcript-grounded quotation or paraphrase"],
  "missing_evidence": ["what the next answer should demonstrate"],
  "confidence": "insufficient|low|moderate|high",
  "question_fit": "not_applicable|weak|good",
  "coaching_action": "one behavior to try in the next answer"
}
```

Rules:

- Score only when `question_fit` is `good`; otherwise display “not assessed.”
- Require transcript spans for every positive judgment.
- Treat missing evidence as missing, not negative.
- Keep facial and acoustic features out of the competency score.
- Use delivery metrics only for specific, user-controllable coaching such as pacing, pauses, vocal emphasis, framing, and camera setup.
- Compare delivery with the same user's baseline or earlier attempts, not population norms.
- Never collapse the five dimensions into a hiring recommendation or personality profile.

Suggested overlay wording:

- Replace `Emotion (estimate): happy` with `Facial movement: active / low / uncertain`, or remove this line entirely.
- If valence/arousal research mode is enabled, label it `Expression pattern estimate`, add a visible “not an inner-state reading” note, smooth over several seconds, and expose “uncertain.”
- Show the five competency results only after the interview, next to the specific answer evidence that supports them.

## Validation plan

1. **Define intended use:** self-coaching only, not candidate ranking or employer selection.
2. **Create question sets:** at least two behaviorally targeted questions per competency, tied to job context.
3. **Develop anchors with subject-matter experts:** create examples for levels 0–3 and counterexamples that look expressive but lack evidence.
4. **Human-label a diverse set:** use at least two trained raters, retain disagreements, and measure agreement per competency.
5. **Evaluate the evidence extractor:** exact evidence support, false positive rate, calibration/abstention, subgroup error, and question-type performance. Overall accuracy is not enough.
6. **Run modality ablations:** transcript only, transcript plus acoustic descriptions, and full video. If face data does not add reliable, fair coaching value, do not use it.
7. **Test repeatability and sensitivity:** camera, lighting, microphone, accent, speech disability, neurodivergence, skin tone, glasses, facial hair, and language.
8. **Pilot as unscored coaching:** let users contest or hide observations and collect whether advice is useful and actionable.
9. **Revalidate before any employment use:** structured interview assessments require job analysis, reliability, and criterion evidence; a self-coaching pilot does not validate selection use.

## Legal and ethical boundary

The EU AI Act prohibits AI systems used to infer emotions in workplace and education settings except for medical or safety reasons ([Regulation (EU) 2024/1689, Article 5(1)(f)](https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX%3A32024R1689)). A private self-practice tool may have a different legal analysis from an employer's assessment system, but adding employer-facing emotion or trait scores would create substantial regulatory risk and requires legal review. In the United States, the EEOC explicitly identifies evaluation of facial expression, voice, or movement as an AI employment practice that can implicate discrimination and accommodation law ([EEOC worker guidance](https://www.eeoc.gov/sites/default/files/2024-04/20240429_Employment%20Discrimination%20and%20AI%20for%20Workers.pdf)).

The safer product claim is: **ViewAndHired helps a user practice how clearly their answers demonstrate selected competencies. It does not detect personality, private emotion, honesty, mental state, or employability.**

## Staged decision

### Now

- Keep the current safeguard that expressions do not affect scores.
- Remove or relabel the seven-class live emotion word.
- Enable MediaPipe blendshapes in a feature branch and benchmark the incremental latency on the two existing recordings.
- Add the transcript-grounded five-competency rubric with abstention and answer evidence.

### Next experiment

- Compare current MobileFaceNet, MediaPipe movement summaries, and EmotiEffLib valence/arousal only for stability, latency, uncertainty, and user usefulness.
- Use Noldus FaceReader or AFFDEX as an external comparator if budget allows; do not treat agreement between models as ground truth.
- Test within-person delivery trends across retakes rather than population-based emotional norms.

### Do not build

- A face-to-trait classifier for the five competencies.
- “Calm,” “humble,” “empathetic,” “passionate,” or “resilient” labels from facial or vocal affect.
- Micro-expression, deception, authenticity, or concealed-emotion claims.
- Employer-facing ranking based on biometric or paralinguistic signals.
