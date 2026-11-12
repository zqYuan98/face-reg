"""Bound ORT CPU scheduling only inside the original image worker thread.

The original image wrappers create SessionOptions privately in that worker.
This adapter relies on that ownership: it changes those objects in place while
preserving graph optimization, providers and every unrelated session setting.
Do not pass options shared with another thread into the marked image worker.
"""

import functools
import logging
import threading


logger = logging.getLogger("FeatureServer.ImageRuntime")
_image_worker = threading.local()
_install_lock = threading.Lock()


def _already_bounded(options):
    """Sessions built by models/ort_options.py already disable spinning and take their thread count
    from FACE_ORT_THREADS; only the 1.1.0 image's own wrappers need forcing to one thread."""
    try:
        return options.get_session_config_entry("session.intra_op.allow_spinning") == "0"
    except RuntimeError:  # the entry is absent
        return False


def _install_thread_options_adapter(ort):
    with _install_lock:
        original_init = ort.InferenceSession.__init__
        if getattr(original_init, "_bounded_image_thread_options", False):
            return

        @functools.wraps(original_init)
        def bounded_init(self, *args, **kwargs):
            if not getattr(_image_worker, "enabled", False):
                return original_init(self, *args, **kwargs)
            supplied = (len(args) >= 2) + ("sess_options" in kwargs) + ("session_options" in kwargs)
            if supplied > 1:
                raise TypeError("conflicting ONNX Runtime session options arguments")
            arguments = list(args)
            options = arguments[1] if len(arguments) >= 2 else kwargs.pop("session_options", kwargs.get("sess_options"))
            if options is None:
                options = ort.SessionOptions()
            if not _already_bounded(options):
                options.intra_op_num_threads = 1
                options.inter_op_num_threads = 1
                options.add_session_config_entry("session.intra_op.allow_spinning", "0")
                options.add_session_config_entry("session.inter_op.allow_spinning", "0")
            if len(arguments) >= 2:
                arguments[1] = options
            else:
                kwargs["sess_options"] = options
            result = original_init(self, *arguments, **kwargs)
            if logger.isEnabledFor(logging.DEBUG):
                actual = self.get_session_options()
                logger.debug(
                    "image ORT intra_op=%d inter_op=%d intra_spin=%s inter_spin=%s",
                    actual.intra_op_num_threads, actual.inter_op_num_threads,
                    actual.get_session_config_entry("session.intra_op.allow_spinning"),
                    actual.get_session_config_entry("session.inter_op.allow_spinning"),
                )
            return result

        bounded_init._bounded_image_thread_options = True
        ort.InferenceSession.__init__ = bounded_init


def initialize_image_worker(factory):
    """Initialize the original model and retain this thread's scheduling policy.

    BoundedInference owns this dedicated thread for its lifetime, so the marker
    also covers rotation sessions created by later image requests. Other
    threads remain untouched. A failed factory restores the preceding marker.
    """
    import onnxruntime as ort

    _install_thread_options_adapter(ort)
    previous = getattr(_image_worker, "enabled", False)
    _image_worker.enabled = True
    try:
        return factory()
    except BaseException:
        _image_worker.enabled = previous
        raise
