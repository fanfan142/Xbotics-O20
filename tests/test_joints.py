from xbotics_o20.joints import (
    HOME_POSITIONS,
    JOINT_COUNT,
    JOINTS,
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


def test_home_positions_use_o20_neutral_abduction_space():
    reference_public20 = [0, 0, 0, 0, 0, 0, 193, 148, 105, 42, 245, 0, 0, 0, 0, 0, 0, 0, 0, 0]

    assert HOME_POSITIONS == public20_to_motor17(reference_public20)
    assert HOME_POSITIONS[4] == 15
    assert HOME_POSITIONS[7] == 5
    assert HOME_POSITIONS[10] == -4
    assert HOME_POSITIONS[13] == -13
    assert JOINTS[4].min_value < HOME_POSITIONS[4] < JOINTS[4].max_value
    assert JOINTS[7].min_value < HOME_POSITIONS[7] < JOINTS[7].max_value
    assert JOINTS[10].min_value < HOME_POSITIONS[10] < JOINTS[10].max_value
    assert JOINTS[13].min_value < HOME_POSITIONS[13] < JOINTS[13].max_value


def test_puppet_safe_mode_locks_clearance_joints():
    positions = keep_puppet_pose_safe([0] * JOINT_COUNT)

    assert positions[:4] == [58.0, 80.0, 160.0, 0.0]
    assert positions[4] == -30.0
    assert positions[13] == -20.0
