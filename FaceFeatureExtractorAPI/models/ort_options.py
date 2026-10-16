"""ONNX Runtime session options shared by every model."""
import os

import onnxruntime as ort


def default_threads(fallback=4):
    """Intra-op threads per session; FACE_ORT_THREADS overrides so workers x threads fit the cores."""
    value = os.environ.get("FACE_ORT_THREADS", "").strip()
    return int(value) if value else fallback


def session_options(num_threads=None):
    options = ort.SessionOptions()
    if num_threads:
        options.intra_op_num_threads = num_threads
    options.inter_op_num_threads = 1
    # Idle pool threads spin by default. With seven sessions run back to back, the spinners of one
    # session steal the cores the next one needs (measured 89 -> 25 ms per image). Spinning only
    # affects scheduling, never results.
    options.add_session_config_entry("session.intra_op.allow_spinning", "0")
    return options
