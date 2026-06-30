# Xbotics O20 控制台

Xbotics O20 控制台是面向 O20 灵巧手的 PySide6 桌面程序，启动入口是 `python run_console.py`。界面采用 O6 控制台同类的桌面级布局：左侧实时读数、手动控制、数字孪生和日志，右侧集中放预设手势、手势识别、猜拳和宏功能。

![Xbotics O20 控制台界面](docs/ui-preview.png)

## 快速开始

```bash
cd "xbotics_o20"
python -m venv ".venv"
source ".venv/bin/activate"
pip install -r "requirements.txt"
python "run_console.py"
```

顶部“连接方式”提供三种模式：

- 直连模式：通过 USB-CANFD 直接连接 O20 实物，日常使用优先选择。
- ROS2节点模式：连接已经启动的 ROS2 节点，适合需要接入 ROS2 工作流的场景。
- 虚拟模式：不连接硬件，用于离线查看界面、动作库和数字孪生。

顶部“导入 hand_dance”会读取 hand_dance txt 动作目录，并导入当前动作库。

## 目录结构

```text
xbotics_o20/
├── run_console.py              # 桌面控制台启动脚本
├── assets/
│   └── hand_landmarker.task    # MediaPipe 手部识别模型
├── README.txt                  # 分发版简明说明
├── src/xbotics_o20/            # 控制库与桌面 UI
├── runtime/
│   ├── config.json             # 运行配置
│   └── action_library/
│       └── actions.json        # O20 动作库
├── tests/
├── pyproject.toml
└── requirements.txt            # 桌面控制台、OpenCV、MediaPipe 等依赖
```

## 界面功能

- 顶部连接栏：选择连接方式、手型、CANFD 设备号和速度。
- 左侧实时读取：完整显示 16 关节位置、电流、温度和故障状态。
- 左侧手动控制：16 个关节滑杆支持实时发送、读取同步、发送当前姿态和保存动作。
- 左下数字孪生：嵌入 `action_generate_yx` 的 URDF/STL 模型；无三维运行环境时自动切换到姿态视图。
- 左下日志：显示连接、诊断、动作执行和保护状态，并写入 `runtime/logs/`。
- 右侧预设手势：按分类播放预设、系统、demo 和自定义动作。
- 右侧手势识别：内嵌 OpenCV + MediaPipe，显示摄像头、手部骨架和识别结果；遥控映射按 O20 关节空间限制输出，食指/中指/无名指/小指侧摆会从手掌平面单独估计，不再和弯曲量绑定。
- 右侧猜拳：识别石头、布、剪刀后，O20 自动出克制动作。
- 右侧宏功能：把多个动作加入队列，设置重复次数后连续执行。

默认不开镜像。开启镜像后，视频画面和骨架使用同一帧数据同步翻转，左右手识别标签保留 MediaPipe 原始结果，不再二次反转。

## 连接方式

- 直连模式会延迟加载 CANFD 运行库。Windows 使用项目内置运行库；Linux 使用系统 `/usr/local/lib/libcanbus.so`，或自动从本地 CANFD 压缩包解包到 `runtime/native_libs/`。
- ROS2节点模式发布 `/cb_<side>_hand_control_cmd`，需要 ROS2 节点已经运行，并且当前 Python 能加载对应 ROS2 的 `rclpy`。ROS2 Humble 通常绑定 Python 3.10。
- 虚拟模式只在本地模拟关节状态，不会打开 CANFD，也不会发送动作到实物。

## ROS2 节点环境

ROS2 工作区可使用项目附带脚本配置 uv Python 3.10 环境和 colcon install 空间。启动单手节点：

```bash
cd "linkerhand-o20-ros2"
scripts/launch_o20_ros2.sh hand_type:=left canfd_device:=0 is_touch:=true
```

检查 ROS2 注册状态：

```bash
scripts/ros2_uv_doctor.sh
source "scripts/ros2_uv_env.sh"
PYTHONPATH="../xbotics_o20/src:${PYTHONPATH:-}" python -m xbotics_o20 scan --ros2-cli --canfd-library
```

详细说明见 `../linkerhand-o20-ros2/UV_ROS2_SETUP.md`。

## 实物连接

不需要为控制台全量安装 `../linkerhand-o20-ros2/requirements.txt`。那份依赖包含仿真和训练包，Python 3.13 下可能卡在 `dm_control/labmaze/bazel`，和直连模式无关。

当前控制台支持 Windows 和 Linux 直连模式。Windows 直连使用仓库内置运行库，无需依赖本机其它目录；如需替换运行库，可通过环境变量覆盖。Linux 直连使用 `libcanbus.so/libusb-1.0.so` 路线。

先做只读扫描：

```bash
cd "xbotics_o20"
PYTHONPATH=src python -m xbotics_o20 scan --canfd-library
PYTHONPATH=src python -m xbotics_o20 probe-direct --max-device 1
PYTHONPATH=src python -m xbotics_o20 canfd-diag --canfd-device 0
```

`scan --canfd-library` 会在 Windows 下调用内置 CANFD 运行库，在 Linux 下调用 `libcanbus.so`。能看到 CANFD 适配器只代表 USB-CANFD 已识别；`probe-direct` 成功才代表 O20 本体有 CANFD 响应。若打开和初始化成功但查询无回包，优先检查 O20 独立供电、CANH/CANL、终端电阻、线束方向、左右手选择、设备号和是否被其它程序占用。

如果 `scan` 里 USB 节点显示 `write=False`，普通用户无法打开 CANFD。安装一次 udev 规则即可：

```bash
PYTHONPATH=src python -m xbotics_o20 udev-rule
sudo env PYTHONPATH=src python -m xbotics_o20 udev-rule --install
```

`udev-rule` 默认根据当前扫描到的 USB-CANFD 设备生成权限规则，只设置访问权限，不绑定当前机器的 bus/dev 编号，也不默认写死序列号或 `/dev/L20D` 软链接。安装后拔插 CANFD 适配器，再确认 `scan` 显示 `write=True`。之后运行 `python run_console.py` 不需要 sudo。

## 命令行工具

```bash
PYTHONPATH=src python -m xbotics_o20 doctor
PYTHONPATH=src python -m xbotics_o20 validate-actions
PYTHONPATH=src python -m xbotics_o20 list-actions
PYTHONPATH=src python -m xbotics_o20 play wave_left --backend mock --progress
```

`validate-actions` 会校验动作 ID 唯一性、每帧 16 个关节值、速度、停留时间和界面必备快捷动作。命令行参数保留内部名称：`direct` 对应直连模式，`ros2-topic` 对应 ROS2节点模式，`mock` 对应虚拟模式。

## 动作库

默认动作库位于：

```text
runtime/action_library/actions.json
```

动作格式使用 O20 实物可控的 16 关节帧；17 路兼容格式导入时会自动丢弃腕部预留位：

```json
{
  "name": "wave_left",
  "title": "挥左手",
  "category": "preset",
  "loop": 1,
  "frames": [
    {
      "positions": [16个关节值],
      "speed": 60,
      "hold_sec": 0.18
    }
  ]
}
```

导入 demo 动作：

```bash
PYTHONPATH=src python -m xbotics_o20 import-demo "<demo 动作目录>" --save
```

## 测试

```bash
cd "xbotics_o20"
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src QT_QPA_PLATFORM=offscreen python -m pytest -q -p no:cacheprovider tests
```
