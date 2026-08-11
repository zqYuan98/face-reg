# Third-Party Notices

## MODNet

- Project: MODNet - Real-Time Trimap-Free Portrait Matting via Objective Decomposition
- Upstream: https://github.com/ZHKKKe/MODNet
- Pinned revision: `28165a451e4610c9d77cfdf925a94610bb2810fb`
- License: Apache License 2.0; the complete upstream license is included at
  `third_party/MODNet/LICENSE`.

This project vendors the minimum inference architecture closure derived from:

- `src/models/modnet.py`
- `src/models/backbones/__init__.py`
- `src/models/backbones/wrapper.py`
- `src/models/backbones/mobilenetv2.py`

Local changes are limited to package-relative imports and inference-only
packaging. Training-only external checkpoint loaders were disabled, and model
weights are loaded from registry-verified bytes with PyTorch's
`weights_only=True` mode. The photographic portrait checkpoint is not
redistributed in the wheel; an operator must explicitly install and verify it.

The upstream MobileNetV2 source records that it was adapted from
Human-Segmentation-PyTorch; that provenance comment remains in the vendored
source.
