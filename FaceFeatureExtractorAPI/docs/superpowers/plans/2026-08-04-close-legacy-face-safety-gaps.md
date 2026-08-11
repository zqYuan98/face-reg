# Close Legacy Face Safety Gaps Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the five reviewed Task 3 safety gaps while preserving the 1024-dimensional legacy API and excluding all Task 4 matching or business logic.

**Architecture:** Put reusable image-resource and embedding-output contracts in one core module, then make both `FacePipeline` and the legacy facade call those contracts. Keep ONNX output validation inside adapters, validate rotation geometry at the pipeline boundary, and cache the default pipeline together with its immutable configuration snapshot.

**Tech Stack:** Python 3.12, NumPy, Pydantic, ONNX Runtime adapters, pytest.

**Scope boundary:** These contracts begin at the decoded NumPy-image boundary used by Task 3. Encoded multipart byte limits belong to the HTTP/API layer and are not expanded in this Task 3 safety patch.

---

### Task 1: Shared image-resource contract for core and legacy entry points

**Files:**
- Create: `face_algorithm/core/contracts.py`
- Modify: `face_algorithm/core/pipeline.py`
- Modify: `face_feature_extractor.py`
- Test: `tests/unit/core/test_pipeline.py`

- [ ] Add failing tests proving invalid and oversized images stop `detect_faces`, `correct_image_rotation`, `extract_landmarks`, `align_face`, `extract_feature`, and `assess_quality` before their model dependency is called.
- [ ] Run the focused legacy tests and confirm failures are caused by the current validation bypass.
- [ ] Add `validate_image_input(image, max_image_pixels)` with distinct invalid-input and oversize `ValueError` subclasses; make the pipeline map them to its existing structured reasons.
- [ ] Call the shared validator before every legacy direct image inference; methods that historically propagate/list results may raise clear `ValueError`, while `align_face()` and `extract_feature()` preserve their legacy `None` result for invalid, oversized, or downstream failures.
- [ ] Run focused tests until green.

### Task 2: One strict 1024-dimensional feature extraction contract

**Files:**
- Modify: `face_algorithm/core/contracts.py`
- Modify: `face_algorithm/core/pipeline.py`
- Modify: `face_feature_extractor.py`
- Test: `tests/unit/core/test_pipeline.py`

- [ ] Add parameterized failing tests for wrong `feature_version`, wrong `feature_dim`, `(1,1024)`, float64, NaN, Inf, and zero raw vectors through legacy `extract_feature`; metadata mismatches must assert zero embedder calls.
- [ ] Add a passing regression asserting a finite float32 `(1024,)` vector is normalized with float64 arithmetic, has L2 approximately one, and neither the returned raw vector nor aligned image is modified by contract code.
- [ ] Extract a single `extract_normalized_embedding(embedder, aligned_face)` function with typed version/contract failures; do not cast or flatten invalid model output.
- [ ] Replace the pipeline's duplicated normalization block and legacy permissive reshape/cast with the shared function; map typed failures to the existing `FEATURE_VERSION_MISMATCH` or `FEATURE_FAILED` results, while legacy returns `None`.
- [ ] Run focused feature tests until green.

### Task 3: Fail closed on malformed detector and rotation model output

**Files:**
- Modify: `face_algorithm/core/adapters.py`
- Modify: `face_algorithm/core/pipeline.py`
- Create: `tests/unit/core/test_adapters.py`
- Modify: `tests/unit/core/test_pipeline.py`

- [ ] Add failing `FaceBoxesAdapter` tests for one valid plus one NaN box, non-positive extents, non-finite values, and scores outside `[0,1]`; separately prove finite negative coordinates with positive extents remain valid.
- [ ] Validate every raw box before returning any box; any malformed member raises and therefore maps through the pipeline to `false + REVIEW + INFERENCE_ERROR`.
- [ ] Add failing `RotationAdapter` tests requiring a real NumPy float32 array of exact shape `(1,4)` with all finite values.
- [ ] Add failing pipeline tests for quarter-turn/dimension mismatch (`0/2` preserve `(H,W)`, `1/3` require `(W,H)`) and corrected images that exceed `max_image_pixels`, asserting detector call count remains zero.
- [ ] Implement strict score validation and rotation output geometry/resource validation, then run adapter and pipeline focused tests until green.

### Task 4: Atomically cache the default runtime snapshot

**Files:**
- Modify: `face_algorithm/core/adapters.py`
- Modify: `face_feature_extractor.py`
- Modify: `tests/unit/core/test_pipeline.py`
- Modify: `tests/integration/test_existing_face_models.py`

- [ ] Add failing tests showing an environment/config change after first initialization cannot pair the old default pipeline with a newly loaded profile.
- [ ] Add a concurrent initialization regression that calls the default runtime from multiple threads and proves config loading, registry creation, and pipeline building happen once.
- [ ] Implement `get_default_runtime() -> tuple[FacePipeline, AlgorithmConfig]` under the existing lock; derive `get_default_pipeline()` from it.
- [ ] Make a default `FaceFeatureExtractor` take both pipeline and `entry_verify` profile from that same immutable config snapshot; keep explicitly injected `pipeline` and `profile` supported.
- [ ] Run focused snapshot, compatibility, and real-session reuse tests until green.

### Task 5: Verification, review, and commit

**Files:**
- Verify all changed production and test files above.

- [ ] Run all five focused groups and record the RED-to-GREEN counts.
- [ ] Run `python -m pytest tests/unit -q`, `python -m pytest tests/integration -q`, and `python -m pytest -q` using `.venv`.
- [ ] Run `compileall`, `pip check`, `git diff --check`, and the public-schema biometric-field scan.
- [ ] Request an independent read-only code review specifically against the five Important findings; fix any Critical or Important issue through another RED/GREEN cycle.
- [ ] Commit only Task 3 changes as `fix: close legacy face safety gaps` and confirm the worktree is clean.
