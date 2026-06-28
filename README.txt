Xbotics O20 控制台 使用说明
===========================

一、快速开始
------------
1. 进入目录：
   cd "xbotics_o20"

2. 安装依赖：
   python -m venv ".venv"
   source ".venv/bin/activate"
   pip install -r requirements.txt

3. 启动控制台：
   python run_console.py

二、连接方式
------------
- 直连模式：通过 USB-CANFD 直接连接 O20 实物，日常使用优先选择。
- ROS2节点模式：连接已经启动的官方 ROS2 节点，适合接入 ROS2 工作流。
- 虚拟模式：不连接硬件，用于离线查看界面、动作库和数字孪生。
- 接上 CANFD 后，选择左手/右手和设备号，再点连接。

三、主要功能
------------
- 实时读取：左侧完整显示 16 关节位置、电流、温度和故障状态。
- 手动控制：左侧 16 个关节滑杆支持实时发送、读取同步、发送当前姿态和保存动作。
- 数字孪生：左下嵌入 action_generate_yx 的 URDF/STL 模型；无三维运行环境时自动切到姿态视图。
- 日志：左下显示连接、诊断、动作执行和保护状态，并写入 runtime/logs/。
- 预设手势：右侧按分类显示预设、系统、demo、自定义动作，点击即可播放。
- 软件保护：设置里可配置单帧最大变化、最小发送间隔、电流阈值、温度阈值。
- 宏功能：可把多个预设/轨迹动作加入宏队列，设置重复次数后执行。
- MediaPipe 手势识别：手势识别和猜拳页启动摄像头后识别 Rock/Paper/Scissors。
- 猜拳：识别用户石头/布/剪刀后，O20 自动出克制动作。
- 保存为动作：把当前滑杆姿态保存到 runtime/action_library/actions.json。
- 扫描：检查 CANFD、udev 链接、ROS2 静态状态和动态库。

四、MediaPipe
-------------
- OpenCV/MediaPipe 已进入主依赖，执行 pip install -r requirements.txt 会安装桌面手势识别链路。
- 控制台“手势识别/猜拳”页会显示运行状态。
- MediaPipe 模型已放在 assets/hand_landmarker.task。
- 默认不开镜像；开启镜像后，视频画面和骨架使用同一帧数据同步翻转。
- 如果当前 Python 环境缺少对应 wheel，控制台仍会启动并在界面中提示缺失项。

五、实机注意
------------
- 首次执行请空载、低速，手指活动范围内不要放异物。
- 直连模式依赖官方 O20 ROS2 SDK 的 CANFD 控制器；控制台会优先使用本地已解包的 libcanbus/libusb。
- ROS2节点模式需要先 source ROS2 环境并启动官方 linker_hand_o20 节点。
- 虚拟模式不会连接硬件，也不会发送动作到实物。

六、交付前自检
--------------
- 动作库校验：
  PYTHONPATH=src python -m xbotics_o20 validate-actions
- 离线测试：
  PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src QT_QPA_PLATFORM=offscreen python -m pytest -q -p no:cacheprovider tests
