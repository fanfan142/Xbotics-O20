# Xbotics O20 控制台产品化进展

更新时间：2026-06-28

## 当前目标

继续把 `xbotics_o20` 做成 O20 灵巧手的产品化桌面配套软件。目标不是网页控制台，而是通过 `python run_console.py` 启动的 PySide6 桌面控制台，连接方式面向用户显示为：

- 直连模式
- ROS2节点模式
- 虚拟模式

## 已完成

- 桌面 UI 主结构已调整为产品布局：
  - 左侧上半区：实时读取 + 手动控制。
  - 左侧下半区：数字孪生 + 日志。
  - 右侧页签：预设手势、手势识别、猜拳、宏功能。
- UI 用户可见连接方式已改成中文产品文案，内部仍保留 `direct`、`ros2-topic`、`mock` 作为稳定枚举。
- MediaPipe 已嵌入桌面控制台：
  - 默认不开镜像。
  - 镜像后视频和骨架使用同一帧数据，避免不同步。
  - 手势遥控和手动实时发送有控制源互斥，动作/宏执行时不会抢控制。
- O20 关节模型按实物改为 16 路：
  - UI 只显示 16 路。
  - direct 后端发送给官方 SDK 时内部补第 17 路 0。
  - 动作库 strict 校验要求每帧 16 个关节值。
- 数字孪生已接入 `action_generate_yx` URDF/STL：
  - 修复非标准 STL 二进制前置 0 填充解析。
  - 左手时对右手 URDF 模型做镜像。
  - 无 QtWebEngine 时回退到 2D 姿态视图。
- 状态读取和设备诊断已后台化，避免 UI 卡顿。
- 诊断和日志已落盘到 `runtime/logs/`、`runtime/diagnostics/`。
- direct 后端已处理：
  - 连接失败时断开官方 controller。
  - 释放进程锁。
  - SDK 动态库优先使用本地解包版本。
- udev 权限逻辑已改成按当前 USB-CANFD 设备生成规则，不硬编码 bus/dev/serial。
- 配置容错已补：
  - 坏 JSON 会备份为 `.invalid-时间戳`。
  - 控制台回退默认配置继续启动。
  - 配置带 `schema_version`。
- 动作库 strict validator 已补：
  - 校验动作 ID 唯一。
  - 校验每帧 16 个关节。
  - 校验速度、停留时间、关节范围。
  - 校验界面必备快捷动作存在。
  - 新增命令：`PYTHONPATH=src python -m xbotics_o20 validate-actions`。
- 打包资源已初步补齐：
  - wheel 中包含 `assets/hand_landmarker.task`。
  - wheel 中包含默认 `runtime/config.json`。
  - wheel 中包含默认 `runtime/action_library/actions.json`。
- README/README.txt 已改成当前产品说明，移除本机绝对路径和旧布局描述。

## 最近验证结果

已通过的离线验证：

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src QT_QPA_PLATFORM=offscreen python -m pytest -q -p no:cacheprovider tests
```

结果：`48 passed`

```bash
PYTHONPATH=src python -m xbotics_o20 validate-actions --path runtime/action_library/actions.json --json
```

结果：`ok: true`

```bash
node --check ../action_generate_yx/web/urdf_viewer.js
node --check ../action_generate_yx/dist/linkerhand_life_o20_full_portable_20260511/web/urdf_viewer.js
```

结果：通过。

```bash
python -m pip wheel . --no-deps --no-build-isolation -w /tmp/xbotics_o20_wheel_check
```

结果：构建成功，并确认 wheel 内包含 MediaPipe 模型、默认配置、默认动作库。构建临时产物 `build/`、`src/xbotics_o20.egg-info/` 已清理。

## 当前暂停点

刚开始审计“安装态/便携态运行时写入语义”时暂停。已发现一个需要继续处理的产品化风险：

- 便携目录运行时，`runtime/action_library/actions.json` 是可写的，保存自定义动作正常。
- 安装态运行时，默认动作库可能来自 `share/xbotics_o20/runtime/action_library/actions.json`，这个位置通常是只读资源；如果直接保存自定义动作，可能写失败或不符合 Linux 安装习惯。
- 需要把“只读默认资源”和“用户可写运行时数据”明确分离。

## 下次继续的优先计划

1. 修运行时路径模型：
   - 增加可写运行时根目录，例如便携模式用项目 `runtime/`，安装模式用 XDG data/config 目录。
   - 默认动作库从只读资源复制/迁移到用户可写动作库路径。
   - 日志、诊断、锁文件、native_libs 都走明确的可写 runtime 位置。

2. 调整动作库读写：
   - `load_actions()` 可以读默认资源。
   - `save_actions()` 和“保存为动作”必须写用户可写动作库。
   - CLI 的 `draft --save`、`import-demo --save` 也要写同一份用户动作库。
   - UI 要显示/记录当前动作库实际写入路径。

3. 补安装态模拟测试：
   - 模拟无便携根目录时，配置、动作库、日志路径落到可写目录。
   - 测首次启动会从默认资源创建用户动作库。
   - 测保存自定义动作不会写入 `share/xbotics_o20`。

4. 继续 UI 产品审计：
   - 检查所有按钮和状态文字是否仍有工程味或内部枚举泄漏。
   - 检查手动控制、手势遥控、动作播放、宏执行的互斥状态是否有不可恢复路径。
   - 检查错误弹窗是否给出用户可执行动作，而不是 traceback。

5. 最后验证：
   - 全量 pytest。
   - `validate-actions`。
   - URDF JS `node --check`。
   - wheel 构建和资源清单检查。
   - 不启动 direct 或 ROS2 节点，除非用户明确要求。

## 注意事项

- direct 当前据用户反馈已经能连实物，后续不要随便运行会连接或驱动实物的命令。
- ROS2 节点模式和直连模式需要分开验证，避免 Anaconda Python 和 ROS2 Humble Python 版本混用。
- 不要硬编码当前电脑的 USB bus/dev/serial。
- 不要自动 git commit。
