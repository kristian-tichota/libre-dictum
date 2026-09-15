import math

import numpy as np
import pytest

pytest.importorskip("cv2", reason="head-tracking extra not installed")
pytest.importorskip("mediapipe", reason="head-tracking extra not installed")

from libre_dictum.tracking.tracker import extract_yaw_pitch  # noqa: E402


def rotation(yaw_degrees: float = 0.0, pitch_degrees: float = 0.0) -> np.ndarray:
    """A 4x4 transformation matrix for a head turned by the given angles."""
    yaw, pitch = math.radians(yaw_degrees), math.radians(pitch_degrees)
    around_y = np.array(
        [[math.cos(yaw), 0, math.sin(yaw)], [0, 1, 0], [-math.sin(yaw), 0, math.cos(yaw)]]
    )
    around_x = np.array(
        [[1, 0, 0], [0, math.cos(pitch), -math.sin(pitch)], [0, math.sin(pitch), math.cos(pitch)]]
    )
    matrix = np.eye(4)
    matrix[:3, :3] = around_y @ around_x
    return matrix


class TestExtractYawPitch:
    def test_a_head_facing_forward_reads_as_zero(self):
        yaw, pitch = extract_yaw_pitch(np.eye(4))

        assert yaw == pytest.approx(0.0)
        assert pitch == pytest.approx(0.0, abs=1e-9)

    @pytest.mark.parametrize("degrees", [-30.0, -5.0, 5.0, 30.0])
    def test_yaw_is_recovered(self, degrees):
        yaw, _ = extract_yaw_pitch(rotation(yaw_degrees=degrees))
        assert yaw == pytest.approx(degrees)

    @pytest.mark.parametrize("degrees", [-30.0, -5.0, 5.0, 30.0])
    def test_pitch_is_recovered(self, degrees):
        _, pitch = extract_yaw_pitch(rotation(pitch_degrees=degrees))
        assert pitch == pytest.approx(degrees)

    def test_a_flat_matrix_is_reshaped(self):
        yaw, pitch = extract_yaw_pitch(rotation(yaw_degrees=10.0).flatten())
        assert yaw == pytest.approx(10.0)
