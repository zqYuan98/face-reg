# User-provided face algorithm baseline

- Source: `http://<git-server>:3000/Yuanzq/face-reg-platform.git`
- Imported: 2026-08-04
- Scope: `face-reg-platform/FaceFeatureExtractorAPI`
- Repository state: the copied source tree did not contain nested Git metadata, so no upstream commit identifier was available.
- Credentials: none were copied into this provenance record.

## Required ONNX checkpoints

| File | Size (bytes) | SHA-256 |
|---|---:|---|
| `checkpoints/faceboxesv2-640x640.onnx` | 4,107,099 | `6d073d97dc31bc2e02064c0b5e3edb8d5b36581dde5c20090913fb008cfb9dfd` |
| `checkpoints/face_landmarker_pts5_net1.onnx` | 410,856 | `cfc73217d8771d2c2ee6ef4ba439172078c81dc0502d422a04cff7ec8df32a2a` |
| `checkpoints/face_landmarker_pts5_net2.onnx` | 409,270 | `7358437179b52192b254ee0b5e3482788038841295d0a94280c8399cb6f40eb9` |
| `checkpoints/face_recognizer.onnx` | 102,528,794 | `ecdbd21b1a9fb637b9780e9bd25cc1818fa3f16f54f8871f8c8658791b48bc37` |
| `checkpoints/fsanet-conv.onnx` | 1,247,488 | `d85e90cb4b9d3a87e81bbfdcfcd130cbe0e175407042e439a570aa396c44eda6` |
| `checkpoints/fsanet-var.onnx` | 1,246,963 | `2877b1db98e3dc66468e90df8db4a21059facd1c3f107af91c6d4fe6476a03e1` |
| `checkpoints/model_gray_mobilenetv2_rotcls.onnx` | 8,862,670 | `8108b82212b947d803b9af58b95f8e125ad6cbc42a0fc282cb59c12b779fbb65` |

These files are intentionally tracked because a fresh checkout must be able to run the existing face-model contract and integration smoke tests without retrieving private artifacts from an external service.
