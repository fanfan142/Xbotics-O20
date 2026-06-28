from xbotics_o20.joints import (
    HOME_POSITIONS,
    JOINT_COUNT,
    keep_puppet_pose_safe,
    motor17_to_public20,
    public20_to_motor17,
)


def test_public20_roundtrip_keeps_motor_values():
    public = motor17_to_public20(HOME_POSITIONS)
    recovered = public20_to_motor17(public)

    assert len(public) == 20
    assert len(recovered) == JOINT_COUNT
    assert recovered == HOME_POSITIONS


def test_ros2_public20_reserved_slots_do_not_add_wrist_joint():
    public = [0] * 20
    public[0] = 255
    public[15] = 255
    public[11] = 255
    public[12] = 255
    public[13] = 255
    public[14] = 255
    motor = public20_to_motor17(public)

    assert len(motor) == JOINT_COUNT
    assert motor[0] == 120.0
    assert motor[1] == 150.0


def test_puppet_safe_mode_locks_clearance_joints():
    positions = keep_puppet_pose_safe([0] * JOINT_COUNT)

    assert positions[:4] == [58.0, 80.0, 160.0, 0.0]
    assert positions[4] == -30.0
    assert positions[13] == -20.0
