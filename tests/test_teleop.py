from __future__ import annotations

from dataclasses import dataclass

from xbotics_o20.joints import HOME_POSITIONS, JOINTS
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


def _spread_hand(index_pip_x: float = -0.3, pinky_pip_x: float = 0.35) -> list[Landmark]:
    points = _base_landmarks()
    points[0] = Landmark(0.0, 0.4, 0.0)
    _set_finger(points, (1, 2, 3, 4), ((-0.18, 0.18, 0.0), (-0.28, -0.2, 0.0), (-0.38, -0.55, 0.0), (-0.48, -0.9, 0.0)))
    _set_finger(points, (5, 6, 7, 8), ((-0.3, 0.0, 0.0), (index_pip_x, -0.8, 0.0), (index_pip_x, -1.4, 0.0), (index_pip_x, -2.0, 0.0)))
    _set_finger(points, (9, 10, 11, 12), ((0.0, 0.0, 0.0), (0.0, -0.9, 0.0), (0.0, -1.5, 0.0), (0.0, -2.1, 0.0)))
    _set_finger(points, (13, 14, 15, 16), ((0.18, 0.0, 0.0), (0.18, -0.85, 0.0), (0.18, -1.45, 0.0), (0.18, -2.0, 0.0)))
    _set_finger(points, (17, 18, 19, 20), ((0.35, 0.0, 0.0), (pinky_pip_x, -0.8, 0.0), (pinky_pip_x, -1.35, 0.0), (pinky_pip_x, -1.9, 0.0)))
    return points


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


def test_teleop_uses_o20_joint_space_for_open_hand() -> None:
    open_hand = _base_landmarks()
    open_hand[0] = Landmark(0.0, 0.2, 0.0)
    _set_finger(open_hand, (1, 2, 3, 4), ((-0.4, 0.0, 0.0), (-0.8, -0.5, 0.0), (-1.2, -1.0, 0.0), (-1.6, -1.5, 0.0)))
    _set_finger(open_hand, (5, 6, 7, 8), ((0.0, 0.0, 0.0), (0.0, -1.0, 0.0), (0.0, -2.0, 0.0), (0.0, -3.0, 0.0)))

    pose = landmarks_to_o20_positions(open_hand, smoothing=1.0)

    assert pose.positions[0] >= HOME_POSITIONS[0]
    assert pose.positions[1] >= HOME_POSITIONS[1]
    assert pose.positions[2] <= HOME_POSITIONS[2]
    assert all(joint.min_value <= value <= joint.max_value for value, joint in zip(pose.positions, JOINTS))


def test_teleop_maps_finger_splay_to_abduction_joints() -> None:
    neutral = landmarks_to_o20_positions(_spread_hand(), smoothing=1.0)
    spread = landmarks_to_o20_positions(_spread_hand(index_pip_x=-0.78, pinky_pip_x=0.78), smoothing=1.0)

    assert spread.positions[4] < neutral.positions[4]
    assert spread.positions[13] < neutral.positions[13]
    assert JOINTS[4].min_value <= spread.positions[4] <= JOINTS[4].max_value
    assert JOINTS[13].min_value <= spread.positions[13] <= JOINTS[13].max_value
