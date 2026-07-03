from __future__ import annotations

import struct
import xml.etree.ElementTree as ET
from pathlib import Path


ASSET_ROOT = Path(__file__).resolve().parents[1] / "resources" / "urdf" / "model"
VIEWER_SCRIPT = Path(__file__).resolve().parents[1] / "resources" / "urdf" / "urdf_viewer.js"
URDF_PATH = ASSET_ROOT / "urdf" / "R20V10.6(完整)-jian24.urdf"
MESH_DIR = ASSET_ROOT / "meshes"


def _find_binary_stl_triangle_count(path: Path) -> int:
    data = path.read_bytes()
    if len(data) < 84:
        return 0

    candidates = [0]
    first_nonzero = next((index for index, value in enumerate(data) if value), 0)
    candidates.extend(range(max(0, first_nonzero - 128), min(len(data) - 84, first_nonzero + 128)))
    for start in dict.fromkeys(candidates):
        if start + 84 > len(data):
            continue
        triangle_count = struct.unpack_from("<I", data, start + 80)[0]
        if triangle_count > 0 and start + 84 + triangle_count * 50 == len(data):
            return int(triangle_count)

    for start in range(first_nonzero, max(first_nonzero - 2048, -1), -1):
        if start + 4 > len(data):
            continue
        triangle_count = struct.unpack_from("<I", data, start)[0]
        if triangle_count > 0 and start + 4 + triangle_count * 50 == len(data):
            return int(triangle_count)
    return 0


def test_o20_urdf_references_all_finger_meshes() -> None:
    robot = ET.parse(URDF_PATH).getroot()
    links = robot.findall("link")
    joints = robot.findall("joint")
    mesh_names = []
    for link in links:
        visual_mesh = link.find("./visual/geometry/mesh")
        assert visual_mesh is not None, link.attrib["name"]
        mesh_names.append(Path(visual_mesh.attrib["filename"]).name)

    assert len(links) == 17
    assert len(joints) == 16
    assert len(set(mesh_names)) == 17
    assert "hand_link.STL" in mesh_names
    assert any(name.startswith("thumb_") for name in mesh_names)
    assert any(name.startswith("index_") for name in mesh_names)
    assert any(name.startswith("middle_") for name in mesh_names)
    assert any(name.startswith("ring_") for name in mesh_names)
    assert any(name.startswith("pinky_") for name in mesh_names)


def test_o20_stl_assets_all_have_triangles() -> None:
    mesh_paths = sorted(MESH_DIR.glob("*.STL"))

    assert len(mesh_paths) == 17
    for mesh_path in mesh_paths:
        assert _find_binary_stl_triangle_count(mesh_path) > 0, mesh_path.name


def test_o20_urdf_orbit_drag_tracks_pointer_direction() -> None:
    script = VIEWER_SCRIPT.read_text(encoding="utf-8")

    assert "this.yaw += dx * 0.006;" in script
    assert "this.yaw -= dx * 0.006;" not in script
