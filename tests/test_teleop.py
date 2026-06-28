from __future__ import annotations

from dataclasses import dataclass

from xbotics_o20.teleop import landmarks_to_o20_positions


@dataclass
class Landmark:
    x: float
    y: float
    z: float = 0.0


def _base_landmarks() -> list[Landmark]:
    return [Landmark(0.0, 0.0, 0.0) for _ in range(21)]


def _set_finger(points: list[Landmark], indexes: tuple[int, int, int, int], coords) -> None:
    for index, coord in zip(indexes, coords):
        points[index] = Landmark(*coord)


def test_index_finger_curl_increases_o20_flexion() -> None:
    open_hand = _base_landmarks()
    curled = _base_landmarks()
    open_hand[0] = Landmark(0.0, 0.2, 0.0)
    curled[0] = Landmark(0.0, 0.2, 0.0)
    _set_finger(open_hand, (5, 6, 7, 8), ((0.0, 0.0, 0.0), (0.0, -1.0, 0.0), (0.0, -2.0, 0.0), (0.0, -3.0, 0.0)))
    _set_finger(curled, (5, 6, 7, 8), ((0.0, 0.0, 0.0), (0.0, -1.0, 0.0), (0.8, -1.1, 0.0), (1.1, -0.5, 0.0)))

    open_pose = landmarks_to_o20_positions(open_hand, smoothing=1.0)
    curled_pose = landmarks_to_o20_positions(curled, smoothing=1.0)

    assert curled_pose.positions[5] > open_pose.positions[5]
    assert curled_pose.positions[6] > open_pose.positions[6]
