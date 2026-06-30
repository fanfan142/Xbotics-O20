from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

from .actions import (
    PRODUCT_REQUIRED_ACTION_NAMES,
    action_to_dict,
    draft_from_prompt,
    find_action,
    load_actions,
    load_demo_txt_action,
    save_actions,
    validate_action_library,
)
from .backends import build_backend
from .canfd_diag import format_canfd_diagnostics, run_canfd_diagnostics
from .config import DEFAULT_CONFIG_PATH, AppConfig, load_app_config, resolve_project_path
from .device_scan import build_scan_report, format_scan_report
from .joints import JOINT_COUNT, clamp_positions, joints_payload, motor17_to_public20
from .player import ActionPlayer
from .udev_rules import DEFAULT_UDEV_RULE_PATH, build_rules_for_targets


def _load_config(path: str | None) -> AppConfig:
    return load_app_config(Path(path).expanduser().resolve()) if path else load_app_config(DEFAULT_CONFIG_PATH)


def _actions_path(config: AppConfig) -> Path:
    return resolve_project_path(config.action_library.path)


def _print_json(payload) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _connect_backend(config: AppConfig, backend_name: str | None, side: str | None, canfd_device: int | None):
    if side:
        config.o20.side = side
    if canfd_device is not None:
        config.o20.canfd_device = canfd_device
    backend = build_backend(config.o20, backend_name)
    if not backend.connect():
        detail = ""
        try:
            detail = backend.get_state().error
        except Exception:
            detail = ""
        raise RuntimeError(f"连接失败{f'：{detail}' if detail else ''}")
    return backend


def _parse_positions(value: str) -> list[float]:
    parts = [part.strip() for part in value.split(",") if part.strip()]
    if len(parts) != JOINT_COUNT:
        raise argparse.ArgumentTypeError(f"需要 {JOINT_COUNT} 个逗号分隔值")
    return clamp_positions([float(item) for item in parts])


def cmd_scan(args: argparse.Namespace) -> int:
    report = build_scan_report(include_canfd_library=args.canfd_library, include_ros2_cli=args.ros2_cli)
    if args.json:
        _print_json(report.to_dict())
    else:
        print(format_scan_report(report))
    return 0


def cmd_joints(args: argparse.Namespace) -> int:
    _print_json(joints_payload())
    return 0


def cmd_list_actions(args: argparse.Namespace) -> int:
    config = _load_config(args.config)
    actions = load_actions(_actions_path(config), puppet_safe_mode=config.safety.puppet_safe_mode)
    if args.json:
        _print_json([action_to_dict(action) for action in actions])
        return 0
    for action in actions:
        duration = sum(frame.hold_sec for frame in action.frames) * action.loop
        print(f"{action.name:24s} {action.title:10s} {len(action.frames):3d}帧 {duration:.2f}s {action.category}")
    return 0


def cmd_validate_actions(args: argparse.Namespace) -> int:
    config = _load_config(args.config)
    path = Path(args.path).expanduser().resolve() if args.path else _actions_path(config)
    required_names = [*PRODUCT_REQUIRED_ACTION_NAMES] if args.product else []
    required_names.extend(args.require or [])
    issues = validate_action_library(path, required_names=required_names)
    if args.json:
        _print_json({"path": str(path), "ok": not issues, "issues": [issue.to_dict() for issue in issues]})
        return 0 if not issues else 2
    if not issues:
        print(f"动作库校验通过：{path}")
        return 0
    print(f"动作库校验失败：{path}")
    for issue in issues:
        print(f"- {issue.path}: {issue.message}")
    return 2


def cmd_state(args: argparse.Namespace) -> int:
    config = _load_config(args.config)
    backend = _connect_backend(config, args.backend, args.side, args.canfd_device)
    try:
        _print_json(asdict(backend.get_state()))
    finally:
        backend.disconnect()
    return 0


def cmd_probe(args: argparse.Namespace) -> int:
    config = _load_config(args.config)
    results = []
    original_backend = config.o20.backend
    original_monitoring = config.o20.start_monitoring
    config.o20.backend = "direct"
    config.o20.start_monitoring = False
    try:
        for device in range(args.max_device + 1):
            for side in ("left", "right"):
                config.o20.side = side
                config.o20.canfd_device = device
                backend = build_backend(config.o20, "direct")
                ok = False
                error = ""
                detected_side = side
                sdk_log = ""
                try:
                    ok = bool(backend.connect())
                    state = backend.get_state()
                    detected_side = state.side
                    error = state.error
                    sdk_log = getattr(backend, "sdk_log", "")
                except Exception as exc:
                    error = str(exc)
                    sdk_log = getattr(backend, "sdk_log", "")
                finally:
                    try:
                        backend.disconnect()
                    except Exception:
                        pass
                results.append(
                    {
                        "canfd_device": device,
                        "side": side,
                        "connected": ok,
                        "detected_side": detected_side,
                        "error": error,
                        "sdk_log": sdk_log,
                    }
                )
    finally:
        config.o20.backend = original_backend
        config.o20.start_monitoring = original_monitoring

    if args.json:
        _print_json(results)
    else:
        for item in results:
            status = "OK" if item["connected"] else "FAIL"
            detail = f" -> {item['detected_side']}" if item["connected"] else f" | {item['error']}"
            print(f"{status} device={item['canfd_device']} side={item['side']}{detail}")
            if args.verbose and item.get("sdk_log"):
                print("  SDK 日志:")
                for line in str(item["sdk_log"]).splitlines()[-18:]:
                    print(f"    {line}")
        if not any(item["connected"] for item in results):
            print("提示：直连模式已能加载控制器，但未收到 O20 回包；可继续运行 canfd-diag 查看 CANFD_Open/Init/Transmit/Receive 返回值。")
    return 0 if any(item["connected"] for item in results) else 2


def cmd_canfd_diag(args: argparse.Namespace) -> int:
    config = _load_config(args.config)
    canfd_device = config.o20.canfd_device if args.canfd_device is None else args.canfd_device
    if args.side == "both":
        sides = ("right", "left")
    else:
        sides = (args.side,)
    result = run_canfd_diagnostics(
        canfd_device=canfd_device,
        channel=args.channel,
        sides=sides,
        attempts=args.attempts,
        timeout_ms=args.timeout_ms,
        sdk_root=config.o20.sdk_root,
    )
    if args.json:
        _print_json(result)
    else:
        print(format_canfd_diagnostics(result))
    return 0 if result.get("detected") else 2


def cmd_udev_rule(args: argparse.Namespace) -> int:
    rules = build_rules_for_targets(
        vendor_id=args.vendor_id or "",
        product_id=args.product_id or "",
        mode=args.mode,
        group=args.group or "",
        symlink=args.symlink or "",
        include_serial=args.serial_specific,
    )
    if not args.install:
        print("CANFD udev 规则:")
        for rule in rules:
            print(rule)
        print()
        print("一键安装命令:")
        print(
            "cd "
            f"{Path.cwd()!s} && sudo env PYTHONPATH=src "
            f"{sys.executable} -m xbotics_o20 udev-rule --install"
        )
        if args.vendor_id or args.product_id or args.group or args.symlink or args.serial_specific or args.mode != "0666":
            print("注意：当前显示命令未自动附带自定义参数；如果用了自定义参数，安装时请原样加上。")
        print("安装后执行拔插 CANFD 适配器，再运行 scan 确认 write=True。")
        return 0

    if os.geteuid() != 0:
        raise RuntimeError("安装 udev 规则需要 root 权限；请用 sudo 运行本命令")
    rule_path = Path(args.rule_path).expanduser()
    if not rule_path.is_absolute():
        raise RuntimeError("udev 规则路径必须是绝对路径")
    rule_path.write_text("\n".join(rules) + "\n", encoding="utf-8")
    for command in (
        ["udevadm", "control", "--reload-rules"],
        ["udevadm", "trigger"],
    ):
        subprocess.run(command, check=True)
    print(f"已安装 {rule_path}")
    for rule in rules:
        print(rule)
    print("请拔插 CANFD 适配器，或重启后再运行：PYTHONPATH=src python -m xbotics_o20 scan --canfd-library")
    return 0


def cmd_pose(args: argparse.Namespace) -> int:
    config = _load_config(args.config)
    positions = args.positions
    if positions is None:
        actions = load_actions(_actions_path(config), puppet_safe_mode=config.safety.puppet_safe_mode)
        positions = find_action(actions, args.name).frames[-1].positions
    if args.print_public20:
        _print_json({"positions": positions, "public20": motor17_to_public20(positions)})
        return 0
    backend = _connect_backend(config, args.backend, args.side, args.canfd_device)
    try:
        ok = backend.send_positions(positions, speed=args.speed or config.o20.default_speed)
        print("发送成功" if ok else "发送失败")
        return 0 if ok else 2
    finally:
        backend.disconnect()


def cmd_play(args: argparse.Namespace) -> int:
    config = _load_config(args.config)
    actions = load_actions(_actions_path(config), puppet_safe_mode=config.safety.puppet_safe_mode)
    action = find_action(actions, args.name)
    backend = _connect_backend(config, args.backend, args.side, args.canfd_device)
    try:
        result = ActionPlayer(config.safety).play(
            action,
            backend,
            progress_callback=(lambda sent, total: print(f"{sent}/{total}", end="\r")) if args.progress else None,
        )
        if args.progress:
            print()
        print(result.message)
        return 0 if result.ok else 2
    finally:
        backend.disconnect()


def cmd_draft(args: argparse.Namespace) -> int:
    config = _load_config(args.config)
    path = _actions_path(config)
    actions = load_actions(path, puppet_safe_mode=config.safety.puppet_safe_mode)
    draft = draft_from_prompt(args.prompt, actions)
    if args.save:
        actions = [action for action in actions if action.name != draft.name]
        actions.append(draft)
        save_actions(path, actions)
    _print_json(action_to_dict(draft))
    return 0


def cmd_import_demo(args: argparse.Namespace) -> int:
    config = _load_config(args.config)
    path = _actions_path(config)
    actions = load_actions(path, puppet_safe_mode=config.safety.puppet_safe_mode)
    imported = []
    source_paths: list[Path] = []
    for raw in args.paths:
        source = Path(raw).expanduser()
        if source.is_dir():
            source_paths.extend(sorted(source.glob("*.txt")))
        else:
            source_paths.append(source)
    for source in source_paths:
        action = load_demo_txt_action(source)
        imported.append(action)
        actions = [item for item in actions if item.name != action.name]
        actions.append(action)
    if args.save:
        save_actions(path, actions)
    _print_json([action_to_dict(action) for action in imported])
    return 0


def _add_common_backend_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--backend", choices=["mock", "direct", "ros2-topic"], help="覆盖配置中的连接方式：direct=直连模式，ros2-topic=ROS2节点模式，mock=虚拟模式")
    parser.add_argument("--side", choices=["left", "right"], help="左右手")
    parser.add_argument("--canfd-device", type=int, help="CANFD 设备序号")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="xbotics-o20", description="Xbotics O20 控制工具")
    parser.add_argument("--config", help=f"配置文件路径，默认 {DEFAULT_CONFIG_PATH}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    scan = subparsers.add_parser("scan", help="扫描 CANFD/ROS2 环境")
    scan.add_argument("--json", action="store_true", help="输出 JSON")
    scan.add_argument("--canfd-library", action="store_true", help="调用 libcanbus 扫描 CANFD")
    scan.add_argument("--ros2-cli", action="store_true", help="执行 ros2 pkg prefix 检查")
    scan.set_defaults(func=cmd_scan)

    doctor = subparsers.add_parser("doctor", help="扫描 CANFD/ROS2 环境")
    doctor.add_argument("--json", action="store_true", help="输出 JSON")
    doctor.add_argument("--canfd-library", action="store_true", help="调用 libcanbus 扫描 CANFD")
    doctor.add_argument("--ros2-cli", action="store_true", help="执行 ros2 pkg prefix 检查")
    doctor.set_defaults(func=cmd_scan)

    joints = subparsers.add_parser("joints", help="输出 16 关节定义")
    joints.set_defaults(func=cmd_joints)

    list_actions = subparsers.add_parser("list-actions", help="列出动作库")
    list_actions.add_argument("--json", action="store_true", help="输出 JSON")
    list_actions.set_defaults(func=cmd_list_actions)

    validate_actions = subparsers.add_parser("validate-actions", help="校验动作库")
    validate_actions.add_argument("--path", help="动作库 JSON 路径；默认读取配置")
    validate_actions.add_argument("--product", action="store_true", default=True, help="按控制台必备动作校验，默认开启")
    validate_actions.add_argument("--no-product", dest="product", action="store_false", help="只校验动作格式，不检查界面快捷动作")
    validate_actions.add_argument("--require", action="append", default=[], help="额外要求存在的动作 ID，可重复")
    validate_actions.add_argument("--json", action="store_true", help="输出 JSON")
    validate_actions.set_defaults(func=cmd_validate_actions)

    state = subparsers.add_parser("state", help="读取设备状态")
    _add_common_backend_args(state)
    state.set_defaults(func=cmd_state)

    probe = subparsers.add_parser("probe-direct", help="只读探测直连模式的左右手和设备号，不发送动作")
    probe.add_argument("--max-device", type=int, default=1, help="最大 CANFD 设备号，默认 1")
    probe.add_argument("--json", action="store_true", help="输出 JSON")
    probe.add_argument("--verbose", action="store_true", help="显示直连初始化日志尾部")
    probe.set_defaults(func=cmd_probe)

    canfd_diag = subparsers.add_parser("canfd-diag", help="底层只读诊断 CANFD 适配器和 O20 回包")
    canfd_diag.add_argument("--canfd-device", type=int, help="CANFD 设备序号，默认读取配置")
    canfd_diag.add_argument("--channel", type=int, default=0, help="CANFD 通道，默认 0")
    canfd_diag.add_argument("--side", choices=["both", "left", "right"], default="both", help="只读查询的 O20 侧")
    canfd_diag.add_argument("--attempts", type=int, default=3, help="每侧查询次数")
    canfd_diag.add_argument("--timeout-ms", type=int, default=250, help="每次接收等待毫秒数")
    canfd_diag.add_argument("--json", action="store_true", help="输出 JSON")
    canfd_diag.set_defaults(func=cmd_canfd_diag)

    udev_rule = subparsers.add_parser("udev-rule", help="显示或安装 CANFD 普通用户访问规则")
    udev_rule.add_argument("--install", action="store_true", help="写入 /etc/udev/rules.d 并 reload udev，需要 sudo")
    udev_rule.add_argument("--vendor-id", help="手工指定 USB vendor id；默认自动扫描")
    udev_rule.add_argument("--product-id", help="手工指定 USB product id；默认自动扫描")
    udev_rule.add_argument("--mode", default="0666", help="udev MODE，默认 0666")
    udev_rule.add_argument("--group", default="", help="可选 GROUP；默认不绑定系统组，仅设置 MODE")
    udev_rule.add_argument("--symlink", default="", help="可选 SYMLINK；默认不创建固定设备名")
    udev_rule.add_argument("--serial-specific", action="store_true", help="按当前检测到的 USB serial 生成更窄规则")
    udev_rule.add_argument("--rule-path", default=str(DEFAULT_UDEV_RULE_PATH), help="udev 规则写入路径")
    udev_rule.set_defaults(func=cmd_udev_rule)

    pose = subparsers.add_parser("pose", help="发送单帧姿态")
    pose.add_argument("name", nargs="?", default="reset", help="动作 ID，默认 reset")
    pose.add_argument("--positions", type=_parse_positions, help="16 个关节位置，逗号分隔")
    pose.add_argument("--speed", type=int, help="发送速度")
    pose.add_argument("--print-public20", action="store_true", help="只打印 ROS2 20 位 position")
    _add_common_backend_args(pose)
    pose.set_defaults(func=cmd_pose)

    play = subparsers.add_parser("play", help="播放动作")
    play.add_argument("name", help="动作 ID")
    play.add_argument("--progress", action="store_true", help="显示进度")
    _add_common_backend_args(play)
    play.set_defaults(func=cmd_play)

    draft = subparsers.add_parser("draft", help="按文本生成动作草稿")
    draft.add_argument("prompt", help="动作描述")
    draft.add_argument("--save", action="store_true", help="保存到动作库")
    draft.set_defaults(func=cmd_draft)

    import_demo = subparsers.add_parser("import-demo", help="导入 hand_dance txt 动作")
    import_demo.add_argument("paths", nargs="+", help="txt 文件或目录")
    import_demo.add_argument("--save", action="store_true", help="保存到动作库")
    import_demo.set_defaults(func=cmd_import_demo)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except (KeyError, ValueError, RuntimeError, OSError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
