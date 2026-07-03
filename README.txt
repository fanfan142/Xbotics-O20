Xbotics O20 控制台 使用说明
===========================

快速启动
--------
Windows PowerShell:
  cd "xbotics_o20"
  python -m venv ".venv"
  .\.venv\Scripts\Activate.ps1
  python -m pip install -r requirements.txt
  python run_console.py

Linux:
  cd "xbotics_o20"
  python3 -m venv ".venv"
  source ".venv/bin/activate"
  python -m pip install -r requirements.txt
  python run_console.py

主要功能
--------
- 直连模式、ROS2节点模式、虚拟模式。
- 16 路关节读数、滑块调姿、实时发送、保存动作。
- URDF 三维模型、二维姿态视图、滑块和 20 位姿态数据同步显示。
- 手势遥控开启时滑块进入实时读数状态，避免手动抢控制。
- 预设动作、demo 动作、自定义动作和宏队列。
- 最大步长、电流、温度、读数缺失、控制源互斥和进程锁保护。
- 设备诊断、动作库校验、日志和诊断报告落盘。

真机前自检
----------
先只做诊断，不发送动作:
  PYTHONPATH=src python -m xbotics_o20 scan --canfd-library
  PYTHONPATH=src python -m xbotics_o20 probe-direct --max-device 1
  PYTHONPATH=src python -m xbotics_o20 canfd-diag --canfd-device 0

首次带实物运行建议:
- 空载、低速。
- 手指活动范围内不要放异物。
- 先确认读数刷新正常，再播放动作或开启手势遥控。
- 触发电流、温度或读数缺失保护时，先排查硬件状态，不要反复强发。

常用命令
--------
  PYTHONPATH=src python -m xbotics_o20 validate-actions
  PYTHONPATH=src python -m xbotics_o20 list-actions
  PYTHONPATH=src python -m xbotics_o20 pose reset --backend mock
  PYTHONPATH=src python -m xbotics_o20 play reset --backend mock --progress

离线测试
--------
  PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src QT_QPA_PLATFORM=offscreen python -m pytest -q -p no:cacheprovider tests
