# Derived from ZHKKKe/MODNet src/models/backbones/__init__.py at commit
# 28165a451e4610c9d77cfdf925a94610bb2810fb.
# Licensed under Apache-2.0; see third_party/MODNet/LICENSE.
# Local changes are limited to package-relative imports and inference-only use.

from .wrapper import MobileNetV2Backbone


#------------------------------------------------------------------------------
#  Replaceable Backbones
#------------------------------------------------------------------------------

SUPPORTED_BACKBONES = {
    'mobilenetv2': MobileNetV2Backbone,
}
