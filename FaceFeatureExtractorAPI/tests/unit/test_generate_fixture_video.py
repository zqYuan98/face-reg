from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from scripts.generate_fixture_video import derive_frame, generate_fixture_video


def test_derive_frame_is_deterministic_and_does_not_mutate_source() -> None:
    source = np.full((48, 64, 3), 100, dtype=np.uint8)
    before = source.copy()

    first = derive_frame(source, frame_index=7, total_frames=40)
    second = derive_frame(source, frame_index=7, total_frames=40)

    np.testing.assert_array_equal(source, before)
    np.testing.assert_array_equal(first, second)
    assert first.shape == source.shape
    assert first.dtype == np.uint8


def test_generator_writes_a_decodable_ten_second_video(tmp_path: Path) -> None:
    source = np.zeros((48, 64, 3), dtype=np.uint8)
    source[:, :, 1] = 180
    input_path = tmp_path / "synthetic.png"
    output_path = tmp_path / "derived.mp4"
    assert cv2.imwrite(str(input_path), source)

    metadata = generate_fixture_video(
        input_path=input_path,
        output_path=output_path,
        fps=4.0,
        duration_seconds=10.0,
    )

    capture = cv2.VideoCapture(str(output_path))
    try:
        assert capture.isOpened()
        assert int(capture.get(cv2.CAP_PROP_FRAME_COUNT)) == 40
        assert capture.get(cv2.CAP_PROP_FPS) == 4.0
        success, frame = capture.read()
        assert success and frame.shape == source.shape
    finally:
        capture.release()
    assert metadata.frame_count == 40
    assert metadata.width == 64
    assert metadata.height == 48
