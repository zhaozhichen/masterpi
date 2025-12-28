#!/usr/bin/env python3
# coding=utf-8
"""
Gemini Live 硬件适配层：
- CameraStreamer：复用 Camera.Camera 获取帧并编码为 JPEG。
- RobotActionShim：最小化的机械臂/夹爪执行（默认干跑）。
- SonarMonitor：基于声呐距离的简单安全检查。
"""
import base64
import sys
import time
from pathlib import Path
from typing import Dict, Optional, Tuple

import cv2

# 路径：gemini_live 在 /home/pi，下一级为 MasterPi
BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent  # /home/pi
MASTERPI_ROOT = PROJECT_ROOT / "MasterPi"
if str(MASTERPI_ROOT) not in sys.path:
    sys.path.append(str(MASTERPI_ROOT))

try:
    import Camera  # uses calibration and threaded capture
except ImportError:
    Camera = None

try:
    import HiwonderSDK.Board as Board
except ImportError:
    Board = None

try:
    from ArmIK.ArmMoveIK import ArmIK
except ImportError:
    ArmIK = None

try:
    import HiwonderSDK.Sonar as Sonar
except ImportError:
    Sonar = None


class CameraStreamer:
    """用已有 Camera.Camera() 管线抓取帧并编码 JPEG。"""

    def __init__(self, width: int = 640, height: int = 480, jpeg_quality: int = 80):
        if Camera is None:
            raise ImportError("Camera module not found; make sure you run from /home/pi/MasterPi")
        self.width = width
        self.height = height
        self.jpeg_quality = int(jpeg_quality)
        self.camera = Camera.Camera(resolution=(self.width, self.height))
        self.camera.camera_open()

    def read_jpeg_bytes(self) -> Optional[bytes]:
        """返回最新帧的 JPEG bytes；若无画面则返回 None。"""
        frame = getattr(self.camera, "frame", None)
        if frame is None:
            return None
        ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), self.jpeg_quality])
        if not ok:
            return None
        return buf.tobytes()

    def read_b64(self) -> Optional[str]:
        """返回 base64 编码的 JPEG 字符串（少用，Live 通道直接用 bytes）。"""
        data = self.read_jpeg_bytes()
        if data is None:
            return None
        return base64.b64encode(data).decode("ascii")


class RobotActionShim:
    """
    把工具调用映射为机械臂/夹爪指令的最小执行层。
    - ArmIK：位置单位使用 cm。
    - Board：夹爪 PWM。
    - 默认干跑，避免无意动作。
    """

    WORKSPACE_LIMITS_CM: Tuple[Tuple[float, float], Tuple[float, float], Tuple[float, float]] = (
        (-16.0, 16.0),  # x
        (-4.0, 18.0),   # y
        (2.0, 20.0),    # z
    )

    def __init__(
        self,
        enable_hardware: bool = False,
        start_pose_cm: Tuple[float, float, float] = (0.0, 8.0, 10.0),
        gripper_open_pulse: int = 2000,
        gripper_close_pulse: int = 1500,
    ):
        # 只有明确开启且模块可用时才触硬件
        self.enable_hardware = enable_hardware and ArmIK is not None and Board is not None
        self.arm = ArmIK() if self.enable_hardware else None
        self.pose_cm = list(start_pose_cm)
        self.gripper_open = gripper_open_pulse
        self.gripper_close = gripper_close_pulse
        if self.enable_hardware:
            Board.setPWMServoPulse(1, self.gripper_open, 800)

    def _clamp_pose(self, pose: Tuple[float, float, float]) -> Tuple[float, float, float]:
        (xmin, xmax), (ymin, ymax), (zmin, zmax) = self.WORKSPACE_LIMITS_CM
        return (
            min(max(pose[0], xmin), xmax),
            min(max(pose[1], ymin), ymax),
            min(max(pose[2], zmin), zmax),
        )

    def move_relative(self, dx_m: float, dy_m: float, dz_m: float) -> Dict:
        """相对移动（单位 m），返回当前位姿等遥测。"""
        scale = 100.0  # meters -> centimeters
        target = (
            self.pose_cm[0] + dx_m * scale,
            self.pose_cm[1] + dy_m * scale,
            self.pose_cm[2] + dz_m * scale,
        )
        target = self._clamp_pose(target)
        self.pose_cm = list(target)

        info = {"pose_cm": {"x": target[0], "y": target[1], "z": target[2]}, "executed": self.enable_hardware}
        if self.enable_hardware and self.arm is not None:
            # Move with a moderate speed; adjust alpha range if needed for your rig.
            result = self.arm.setPitchRangeMoving(target, -90, -90, 0, 800)
            info["ik_result"] = result
        return info

    def control_gripper(self, action: str) -> Dict:
        """开合夹爪。"""
        action = action.lower()
        if action not in ("open", "close"):
            return {"status": "error", "message": f"unknown action {action}"}

        pulse = self.gripper_open if action == "open" else self.gripper_close
        info = {"status": "ok", "action": action, "pulse": pulse, "executed": self.enable_hardware}
        if self.enable_hardware and Board is not None:
            Board.setPWMServoPulse(1, pulse, 400)
        return info


class SonarMonitor:
    """声呐读数封装 + 简单的前向阻挡逻辑。"""

    def __init__(self, guard_threshold_mm: int = 120, enabled: bool = False):
        self.enabled = enabled and Sonar is not None
        self.guard_threshold_mm = guard_threshold_mm
        self.sonar = Sonar.Sonar() if self.enabled else None
        self._last_mm: Optional[int] = None
        if self.sonar:
            self.sonar.setRGBMode(0)

    def read_mm(self) -> Optional[int]:
        if not self.sonar:
            return None
        dist = self.sonar.getDistance()
        self._last_mm = dist
        return dist

    def should_block_move(self, dx_m: float, dy_m: float, dz_m: float) -> Tuple[bool, Optional[str], Dict]:
        """前向守卫：障碍过近且 dz>0 时阻挡。返回 (是否阻挡, 原因, 遥测)。"""
        dist = self.read_mm()
        telemetry = {"sonar_mm": dist}
        if not self.enabled or dist is None:
            return False, None, telemetry
        if dist < self.guard_threshold_mm and dz_m > 0:
            return True, f"Obstacle {dist} mm ahead; move blocked", telemetry
        return False, None, telemetry
