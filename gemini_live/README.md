# Gemini Live 控制（原型）

轻量级的视觉-语言-动作框架：把相机帧流式发送给 Gemini Live，监听工具调用，转成机器人动作，并提供可选的安全层。与现有 `MasterPi` 代码并存，不改动旧逻辑。

## 能做什么
- 流式桥接：将 `Camera.Camera()` 的 JPEG 帧推到 Gemini 多模态 Live（WebSocket）。
- 工具协议：`move_arm`（相对位移 x/y/z）和 `control_gripper`（开/合），可自行扩展。
- 执行适配：把工具调用转为机械臂 + 夹爪指令，默认干跑避免误动作。
- 可选声呐守卫：距离过近时阻挡前向移动（纯相机模式也可用）。

## 文件说明
- `agent.py` —— Gemini Live 主循环、工具处理、安全反馈。
- `hardware.py` —— 相机 JPEG 推流器、简化的臂/夹爪执行、声呐辅助。

## 快速开始
1) 安装依赖（需网络）：
   ```bash
   pip install google-genai opencv-python
   ```
2) 把 API Key 写到 `/home/pi/.env`（推荐）：
   ```
   GOOGLE_API_KEY=your_key_here
   ```
   或在 shell 导出：
   ```bash
   export GOOGLE_API_KEY="YOUR_KEY"
   ```
3) 先用干跑模式查看流程（不动作硬件）：
   ```bash
   cd /home/pi
   python3 -m gemini_live.agent --task "捡起红色方块" --dry-run
   ```

## 配置提示
- 模型：默认 `gemini-2.0-flash-exp`，可用 `--model` 或修改 `DEFAULT_MODEL_ID`。
- 帧率与分辨率：用 `--fps`、`--width`、`--height`、`--jpeg-quality` 控制，2–5 FPS 通常够用。
- 声呐：加 `--sonar-guard` 启用，默认阈值 120 mm。
- 硬件执行：准备好再加 `--enable-hardware`，否则保持干跑。

## 与现有代码协同
- 复用 `Camera.Camera()`，保持标定不变。
- 动作适配用 `ArmIK.ArmMoveIK.ArmIK` 和 `Board` 夹爪脉宽，参数可在 `hardware.py` 调整。
- 可在你自己的调度器中启动/停止本模块（如通过 RPC）。

## 安全
- 默认干跑；未加 `--enable-hardware` 不会动。
- 启用声呐后，距离过近且前向移动会被阻挡，并把结果回传给 Gemini。
- 在真实环境前，再加一层硬件限位/校验以防模型幻觉。
