from xbotics_o20.canfd_diag import create_frame_id, decode_frame_id


def test_canfd_frame_id_roundtrip() -> None:
    frame_id = create_frame_id(0x02, 0x06, True)

    assert frame_id == 0x0040D000
    assert decode_frame_id(frame_id) == (0x02, 0x06, True)


def test_canfd_read_frame_id_roundtrip() -> None:
    frame_id = create_frame_id(0x01, 0x00, False)

    assert frame_id == 0x00200000
    assert decode_frame_id(frame_id) == (0x01, 0x00, False)
