import json

from xbotics_o20.config import (
    CONFIG_SCHEMA_VERSION,
    PROJECT_ROOT,
    app_config_from_dict,
    load_app_config,
    resolve_resource_path,
    resource_roots,
    save_app_config,
)


def test_config_normalizes_bad_values():
    config = app_config_from_dict(
        {
            "o20": {
                "backend": "bad",
                "side": "up",
                "canfd_device": "x",
                "default_speed": 999,
            },
            "safety": {
                "max_step_per_frame": "abc",
                "min_frame_dt_s": -1,
                "return_home_speed": 999,
                "return_home_on_stop": "false",
            },
            "camera": {
                "camera_index": -2,
                "detection_fps": "fast",
                "mirror": "no",
            },
        }
    )

    assert config.o20.backend == "direct"
    assert config.schema_version == CONFIG_SCHEMA_VERSION
    assert config.o20.side == "left"
    assert config.o20.canfd_device == 0
    assert config.o20.default_speed == 130
    assert config.safety.max_step_per_frame == 45.0
    assert config.safety.min_frame_dt_s == 0.0
    assert config.safety.return_home_speed == 130
    assert config.safety.return_home_on_stop is False
    assert config.camera.camera_index == 0
    assert config.camera.detection_fps == 20.0
    assert config.camera.mirror is False


def test_load_app_config_backs_up_invalid_json(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("{bad json", encoding="utf-8")

    config = load_app_config(path)

    assert config.o20.backend == "direct"
    assert not path.exists()
    backups = list(tmp_path.glob("config.json.invalid-*"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == "{bad json"


def test_load_app_config_accepts_old_partial_config(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"o20": {"backend": "mock"}}), encoding="utf-8")

    config = load_app_config(path)

    assert config.schema_version == CONFIG_SCHEMA_VERSION
    assert config.o20.backend == "mock"
    assert config.o20.side == "left"
    assert config.camera.mirror is False


def test_save_app_config_writes_complete_schema(tmp_path):
    path = tmp_path / "config.json"
    config = app_config_from_dict({"o20": {"backend": "mock"}})

    save_app_config(path, config)

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == CONFIG_SCHEMA_VERSION
    assert set(payload) == {"schema_version", "o20", "safety", "action_library", "ui", "camera"}
    assert payload["o20"]["backend"] == "mock"


def test_resource_roots_include_project_root():
    assert PROJECT_ROOT in resource_roots()


def test_resolve_resource_path_finds_bundled_mediapipe_model():
    path = resolve_resource_path("assets/hand_landmarker.task")

    assert path.exists()
    assert path.name == "hand_landmarker.task"
