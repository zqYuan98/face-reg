# Integration fixture provenance

## `source/synthetic_person_holding_blank_card.png`

- Created: 2026-08-04
- Generator: Codex built-in image generation tool
- Subject: entirely synthetic adult; not based on or intended to identify a real person
- Size: 2,320,607 bytes
- SHA-256: `70d1518bbb32ef8c38792870a0dbb9e1a05466354d3f5cbfc0cc470e4b0c5773`
- License/use: project-local test fixture generated for this implementation; do not treat it as customer biometric data or production enrollment data.

Exact prompt:

> Use case: photorealistic-natural
> Asset type: private face-algorithm integration-test fixture; entirely synthetic person, not based on any real individual
> Primary request: Create one highly realistic documentary-style photograph of a fictional adult facing the camera while holding a completely blank bank-card-sized rectangle with both hands at mid-chest.
> Scene/backdrop: non-uniform modern office background with visible desk, shelving, soft shapes and texture, clearly separable from the person; no plain studio wall.
> Subject: exactly one adult, neutral expression, frontal face, eyes open, head upright, unobstructed face, head and shoulders plus upper torso and both complete arms/hands visible. The blank card is fully visible, centered near the torso, rectangular with realistic perspective, sharp edges, high contrast from clothing, and generous distance from every image boundary.
> Style/medium: natural photorealistic color photography, realistic skin and hair texture, no beauty retouching, no stylization.
> Composition/framing: vertical composition, one person centered, generous margin above head and around shoulders/hands/card; camera near eye level; face large enough for reliable face detection; no crop of head, shoulders, arms, hands, or card.
> Lighting/mood: soft even office daylight, clear facial features, no harsh shadow or overexposure.
> Constraints: image must be suitable for testing face detection, 5-point landmarks, portrait matting, held-card quadrilateral detection, and affine-derived video. Card must contain absolutely no text, numbers, logo, chip, portrait, barcode, watermark, or symbols. Exactly one face and one card-like object. No badges, screens, picture frames, papers, or other rectangular foreground objects near the person. No text or watermark anywhere.

## Generated signing-video fixture

`scripts/generate_fixture_video.py` derives a 10-second, 4 FPS MP4 only from the
fully synthetic still above. It applies deterministic sub-pixel translation and
small brightness changes. Generated MP4 files are ignored because codec bytes can
vary by OpenCV/FFmpeg build; integration tests generate them in a temporary folder
and retain no customer media.
