#!/usr/bin/env python3
# coding=utf-8
from __future__ import annotations
"""
Gemini Live 视觉-语言-动作主循环（MasterPi）。
流式发送相机帧，接收工具调用，带可选声呐安全检查执行动作。
"""
import argparse
import asyncio
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
import logging

from google import genai
from google.genai import types, live

from .hardware import CameraStreamer, RobotActionShim, SonarMonitor, CarChassisShim, PROJECT_ROOT, TELEMETRY


DEFAULT_MODEL_ID = "gemini-2.0-flash-exp"
SYSTEM_PROMPT = """
You are a robotic arm agent with a wrist camera and an optional sonar sensor.
- You see the live camera feed; you can call tools to move and control the gripper.
- If the view is blank/uncertain, DO NOT move. Wait for a usable frame before acting.
- Coordinate system: +X right, +Y down (image space), +Z forward (toward objects).
- HUD overlay shows ARM (x,y,z), GRIPPER state, CAR velocity/direction/angular_rate.
- Use move_car for coarse reposition (e.g., target off-screen); keep velocity_mm_s small (20-80) and angular_rate modest; stop if uncertain.
- Use move_arm for fine adjustments near the target; move in small, smooth increments (0.02m-0.10m). Avoid repeating zero-effect moves; do not move if target not visible.
- Use control_gripper to open/close only when aligned; avoid toggling repeatedly.
- The mecanum chassis can move with polar velocity: move_car(velocity_mm_s, direction_deg, angular_rate) with 0deg right, 90deg forward, 180deg left, 270deg back.
- Respond ONLY with the provided tools. Do not emit free-form text.
"""

TOOLS: List[Dict[str, Any]] = [
    {
        "function_declarations": [
            {
                "name": "move_arm",
                "description": "Moves arm end-effector relative to current position (meters).",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "dx": {"type": "NUMBER", "description": "Right/left movement in meters (+right)."},
                        "dy": {"type": "NUMBER", "description": "Down/up movement in meters (+down)."},
                        "dz": {"type": "NUMBER", "description": "Forward/back movement in meters (+forward)."},
                    },
                    "required": ["dx", "dy", "dz"],
                },
            },
            {
                "name": "control_gripper",
                "description": "Opens or closes the gripper.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "action": {"type": "STRING", "enum": ["open", "close"]},
                    },
                    "required": ["action"],
                },
            },
            {
                "name": "move_car",
                "description": "Moves the mecanum chassis using polar velocity (mm/s, degrees) and optional spin.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "velocity_mm_s": {
                            "type": "NUMBER",
                            "description": "Linear speed in mm/s (0 = stop)."
                        },
                        "direction_deg": {
                            "type": "NUMBER",
                            "description": "Direction in degrees (0 right, 90 forward, 180 left, 270 back)."
                        },
                        "angular_rate": {
                            "type": "NUMBER",
                            "description": "Self-rotation rate (positive clockwise).",
                            "default": 0
                        },
                    },
                    "required": ["velocity_mm_s", "direction_deg"],
                },
            },
        ]
    }
]


try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None


def _load_env_files(paths: List[Path]) -> None:
    """从 .env 类文件加载环境变量；即使没有 python-dotenv 也可用。"""
    if load_dotenv:
        for p in paths:
            load_dotenv(dotenv_path=p, override=False)
        return

    for p in paths:
        if not p.exists():
            continue
        try:
            with p.open("r") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, _, val = line.partition("=")
                    key = key.strip()
                    val = val.strip().strip('"').strip("'")
                    if key and key not in os.environ:
                        os.environ[key] = val
        except Exception:
            # best-effort; ignore parse errors
            pass


class GeminiLiveAgent:
    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_MODEL_ID,
        fps: float = 3.0,
        frame_size: tuple = (640, 480),
        jpeg_quality: int = 80,
        enable_hardware: bool = False,
        sonar_guard: bool = False,
        sonar_threshold_mm: int = 120,
        observe_seconds: float = 2.0,
        observe_sweep: bool = True,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.interval = 1.0 / max(fps, 0.1)  # 帧发送周期（秒）
        self.observe_seconds = observe_seconds  # 初始观察时间
        self.observe_deadline = time.time()  # 将在 run 中刷新
        self.observe_sweep = observe_sweep
        # 感知：从 Camera.Camera 读帧、JPEG 编码
        hud_port = int(os.getenv("GEMINI_LIVE_HUD_PORT", "8090"))
        self.camera = CameraStreamer(
            width=frame_size[0],
            height=frame_size[1],
            jpeg_quality=jpeg_quality,
            start_mjpeg_stream=True,
            stream_port=hud_port,
        )
        # 执行：机械臂/夹爪（默认干跑，不触硬件）
        self.robot = RobotActionShim(enable_hardware=enable_hardware)
        # 执行：底盘
        self.chassis = CarChassisShim(enable_hardware=enable_hardware)
        # 安全：可选声呐守卫
        self.sonar = SonarMonitor(enabled=sonar_guard, guard_threshold_mm=sonar_threshold_mm)
        logging.info(
            "Agent init: model=%s fps=%.2f size=%sx%s quality=%s hardware=%s sonar=%s",
            model,
            fps,
            frame_size[0],
            frame_size[1],
            jpeg_quality,
            enable_hardware,
            sonar_guard,
        )

    def _make_client(self) -> genai.Client:
        return genai.Client(api_key=self.api_key, http_options={"api_version": "v1alpha"})

    async def _send_loop(self, session: live.AsyncSession, task_prompt: str) -> None:
        """不断发送帧（必要时附带声呐上下文）给 Gemini。"""
        user_content = types.Content(role="user", parts=[types.Part(text=task_prompt)])
        await session.send_client_content(turns=user_content, turn_complete=True)
        last_send = 0.0
        sent_frames = 0
        while True:
            now = time.time()
            if now - last_send >= self.interval:
                jpeg_bytes = self.camera.read_jpeg_bytes()
                if jpeg_bytes:
                    # 实时通道推视频帧，模型可随时打断下发指令
                    await session.send_realtime_input(
                        video=types.Blob(data=jpeg_bytes, mime_type="image/jpeg")
                    )
                    sent_frames += 1
                else:
                    # If no frame is ready, still yield control.
                    await asyncio.sleep(0.02)
                last_send = now
                if sent_frames % 10 == 0 and sent_frames > 0:
                    logging.info("Sent %d frames to Gemini", sent_frames)
            await asyncio.sleep(0.001)

    async def _handle_tool_calls(
        self,
        session: live.AsyncSession,
        tool_calls: List[types.LiveServerToolCall],
    ) -> None:
        for call in tool_calls:
            fcalls = getattr(call, "function_calls", []) or []
            for fcall in fcalls:
                logging.info("Tool call: %s args=%s id=%s", fcall.name, fcall.args, fcall.id)
                logging.debug("Tool call raw: %s", fcall)
                name = fcall.name or ""
                args = fcall.args or {}
                call_id = fcall.id

                # 初始观察阶段：阻塞执行，仅反馈状态
                now = time.time()
                if now < self.observe_deadline:
                    await session.send_tool_response(
                        function_responses=types.FunctionResponse(
                            name=name,
                            response={
                                "status": "blocked",
                                "reason": "observe_phase",
                                "observe_remaining_s": round(self.observe_deadline - now, 2),
                            },
                            id=call_id,
                        )
                    )
                    continue

                if name == "move_arm":
                    dx = float(args.get("dx", 0))
                    dy = float(args.get("dy", 0))
                    dz = float(args.get("dz", 0))
                    block, reason, telemetry = self.sonar.should_block_move(dx, dy, dz)
                    if block:
                        await session.send_tool_response(
                            function_responses=types.FunctionResponse(
                                name=name,
                                response={"status": "blocked", "reason": reason, **telemetry, "telemetry": TELEMETRY.snapshot()},
                                id=call_id,
                            )
                        )
                        continue

                    result = self.robot.move_relative(dx, dy, dz)
                    # 如果不可达，不执行并提示模型重新规划
                    if not result.get("reachable", True):
                        await session.send_tool_response(
                            function_responses=types.FunctionResponse(
                                name=name,
                                response={"status": "blocked", "reason": "ik_unreachable", **result, **telemetry, "telemetry": TELEMETRY.snapshot()},
                                id=call_id,
                            )
                        )
                    else:
                        await session.send_tool_response(
                            function_responses=types.FunctionResponse(
                                name=name,
                                response={"status": "ok", **result, **telemetry, "telemetry": TELEMETRY.snapshot()},
                                id=call_id,
                            )
                        )

                elif name == "control_gripper":
                    action = str(args.get("action", ""))
                    result = self.robot.control_gripper(action)
                    await session.send_tool_response(
                        function_responses=types.FunctionResponse(
                            name=name,
                            response={**result, "telemetry": TELEMETRY.snapshot()},
                            id=call_id,
                        )
                    )
                elif name == "move_car":
                    velocity = float(args.get("velocity_mm_s", 0))
                    direction = float(args.get("direction_deg", 0))
                    angular_rate = float(args.get("angular_rate", 0))
                    result = self.chassis.set_velocity(velocity, direction, angular_rate)
                    await session.send_tool_response(
                        function_responses=types.FunctionResponse(
                            name=name,
                            response={"status": "ok", **result, "telemetry": TELEMETRY.snapshot()},
                            id=call_id,
                        )
                    )

    async def _receive_loop(self, session: live.AsyncSession) -> None:
        logging.info("Receive loop started.")
        async for response in session.receive():
            if getattr(response, "server_content", None):
                logging.debug("Server content: %s", response.server_content)
            if getattr(response, "text", None):
                logging.info("[Gemini text] %s", response.text)

            # SDK exposes single tool_call; also keep compatibility with tool_calls list.
            calls: List[Any] = []
            if getattr(response, "tool_call", None):
                calls.append(response.tool_call)
            calls.extend(getattr(response, "tool_calls", []) or [])
            if calls:
                logging.info("Received %d tool call batch(es)", len(calls))
                await self._handle_tool_calls(session, calls)

    async def run(self, task_prompt: str) -> None:
        client = self._make_client()
        config = {"tools": TOOLS, "system_instruction": SYSTEM_PROMPT}
        logging.info("Connecting to Gemini Live model=%s ...", self.model)
        async with client.aio.live.connect(model=self.model, config=config) as session:
            logging.info("Connected. Starting send/receive loops.")
            # 初始观察阶段，给模型一些时间“看环境”
            self.observe_deadline = time.time() + max(self.observe_seconds, 0.0)
            # 给模型一个文字提示：正在观察环境
            observe_note = f"开始前先观察环境 {self.observe_seconds:.1f}s，请耐心等待后再下达动作。任务：{task_prompt}"
            observe_content = types.Content(role="user", parts=[types.Part(text=observe_note)])
            await session.send_client_content(turns=observe_content, turn_complete=True)
            # 硬件环视（可选）：左右小幅扫视，帮助模型建立环境感
            sweep_task = None
            if self.robot.enable_hardware and self.observe_sweep and self.observe_seconds > 0:
                loop = asyncio.get_running_loop()
                sweep_task = loop.run_in_executor(None, self.robot.observe_sweep, 1, None, None)
            sender = asyncio.create_task(self._send_loop(session, task_prompt))
            receiver = asyncio.create_task(self._receive_loop(session))
            if sweep_task:
                await asyncio.gather(sender, receiver, sweep_task)
            else:
                await asyncio.gather(sender, receiver)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Gemini Live control loop for MasterPi")
    parser.add_argument("--task", "-t", required=True, help="Natural language task (e.g., '捡起红色方块').")
    parser.add_argument("--model", default=DEFAULT_MODEL_ID, help="Gemini model id.")
    parser.add_argument("--fps", type=float, default=3.0, help="Frame send rate to Gemini.")
    parser.add_argument("--width", type=int, default=640, help="Camera width.")
    parser.add_argument("--height", type=int, default=480, help="Camera height.")
    parser.add_argument("--jpeg-quality", type=int, default=80, help="JPEG quality 1-100.")
    parser.add_argument("--enable-hardware", action="store_true", help="Enable real arm + gripper motion.")
    parser.add_argument("--dry-run", action="store_true", help="Force hardware off (default is off).")
    parser.add_argument("--sonar-guard", action="store_true", help="Enable sonar-based forward guard.")
    parser.add_argument("--sonar-threshold-mm", type=int, default=120, help="Guard threshold in mm.")
    parser.add_argument("--observe-seconds", type=float, default=2.0, help="初始观察环境时间，期间阻塞动作。")
    parser.add_argument("--observe-sweep", action="store_true", help="初始观察阶段执行左右扫视（仅硬件开启时生效）。")
    parser.add_argument("--api-key", default=None, help="Gemini API key (overrides .env/ENV).")
    parser.add_argument("--log-level", default="INFO", help="Logging level (DEBUG, INFO, WARNING...).")
    return parser.parse_args()


async def _main_async(args: argparse.Namespace) -> None:
    # Load .env (current dir, project root, and /home/pi) so GOOGLE_API_KEY is available.
    _load_env_files(
        [
            Path.cwd() / ".env",
            Path(PROJECT_ROOT) / ".env",
            Path("/home/pi/.env"),
        ]
    )

    api_key = args.api_key or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        logging.error("Missing API key. Set GOOGLE_API_KEY in .env or pass --api-key.")
        sys.exit(1)
    else:
        logging.info("API key detected (length=%d)", len(api_key))

    agent = GeminiLiveAgent(
        api_key=api_key,
        model=args.model,
        fps=args.fps,
        frame_size=(args.width, args.height),
        jpeg_quality=args.jpeg_quality,
        enable_hardware=args.enable_hardware and not args.dry_run,
        sonar_guard=args.sonar_guard,
        sonar_threshold_mm=args.sonar_threshold_mm,
        observe_seconds=args.observe_seconds,
        observe_sweep=args.observe_sweep,
    )
    await agent.run(args.task)


def main() -> None:
    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
        force=True,
    )
    try:
        asyncio.run(_main_async(args))
    except KeyboardInterrupt:
        print("\nStopped by user.")


if __name__ == "__main__":
    main()
