# Xbotics O20 控制台审计记录

更新时间：2026-07-03

## 当前状态

- 桌面控制台使用 PySide6，入口为 `python run_console.py`。
- 仓库内包含默认动作库、手部识别模型、Windows 直连运行文件、URDF/STL 模型和预览图。
- 默认配置不依赖仓库外固定目录；如需外部运行组件，通过设置页“扩展路径”显式配置。
- `main` 已同步远端；本轮真机安全审计修复完成后发布 `v0.1.2`。

## 已完成的产品化能力

- 左侧实时读取、16 路手动滑块、URDF 模型、二维姿态视图和 20 位姿态数据同步。
- 手动实时、手势遥控、动作播放和宏执行互斥，避免多个控制源同时发送。
- 手势遥控开启时滑块切换为实时读数；关闭后恢复滑块控制。
- 默认回初始采用中性侧摆姿态，MediaPipe 侧摆围绕各关节中性值映射。
- 避让姿态保护保留为显式开关，默认不压住食指和小指侧摆。
- 动作播放从当前实物读数开始限步，动作循环边界也限步。
- 停止/失败后的回初始按当前读数分步执行。
- CLI `pose` 单帧发送走同一套读数、安全检查和最大步长拆分。
- 动作库严格校验 16 路关节、速度、停留时间、范围和快捷动作完整性。
- 设备诊断为只读查询，进程锁会阻止多个进程同时打开同一 CANFD。
- URDF 资源测试改为仓库内 `resources/urdf/model`，不依赖本机相邻目录。

## 2026-07-03 审计验证

已通过：

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src QT_QPA_PLATFORM=offscreen python -m pytest -q -p no:cacheprovider tests
```

结果：`68 passed`

```bash
PYTHONDONTWRITEBYTECODE=1 python -m compileall -q src tests
python -m pip wheel . --no-deps
```

结果：语法编译和 wheel 构建通过，wheel 内包含手部识别模型、URDF/STL、Windows 运行文件、默认配置和动作库。

只读硬件诊断：

```bash
PYTHONPATH=src python -m xbotics_o20 scan --canfd-library
PYTHONPATH=src python -m xbotics_o20 canfd-diag --canfd-device 0 --side both --attempts 1 --timeout-ms 150
```

当前机器可加载 Windows 直连运行文件，但未扫描到 USB-CANFD 适配器，因此没有完成 O20 实物回包验证。

## 上真机前门槛

- `scan --canfd-library` 能扫描到 CANFD 适配器。
- `canfd-diag` 能读到 O20 左手或右手设备信息。
- 控制台连接后实时读数能刷新 16 路位置、电流、温度、故障。
- 低速执行 `reset`，确认动作方向和手型选择正确。
- 再逐步测试预设动作、宏和手势遥控。

## 仍需实物确认的事项

- 当前环境没有 USB-CANFD 适配器回包，无法验证真实电流/温度读数随硬件变化。
- 无法在本轮确认 O20 本体供电、线束、终端电阻和左右手实际方向。
- 无法在本轮完成真实 MediaPipe 遥控驱动实物的长时间稳定性测试。
