#!/usr/bin/env python3
# coding=utf-8
"""
Gemini Live 硬件适配层：
- CameraStreamer：复用 Camera.Camera 获取帧并编码为 JPEG。
- RobotActionShim：最小化的机械臂/夹爪执行（默认干跑）。
- SonarMonitor：基于声呐距离的简单安全检查。
"""
import base64
import os
import sys
import time
import logging
from pathlib import Path
from typing import Dict, Optional, Tuple
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

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
    from HiwonderSDK.mecanum import MecanumChassis
except ImportError:
    MecanumChassis = None
try:
    import HiwonderSDK.Sonar as Sonar
except ImportError:
    Sonar = None


class Telemetry:
    """简单的全局遥测记录，用于工具响应和 HUD 叠加。"""

    def __init__(self) -> None:
        self.arm_pose_cm: Tuple[float, float, float] = (0.0, 8.0, 10.0)
        self.gripper_state: str = "open"
        self.chassis_velocity_mm_s: float = 0.0
        self.chassis_direction_deg: float = 0.0
        self.chassis_angular_rate: float = 0.0

    def snapshot(self) -> Dict:
        return {
            "arm_pose_cm": {
                "x": self.arm_pose_cm[0],
                "y": self.arm_pose_cm[1],
                "z": self.arm_pose_cm[2],
            },
            "gripper": self.gripper_state,
            "chassis": {
                "velocity_mm_s": self.chassis_velocity_mm_s,
                "direction_deg": self.chassis_direction_deg,
                "angular_rate": self.chassis_angular_rate,
            },
        }


TELEMETRY = Telemetry()


class CameraStreamer:
    """用已有 Camera.Camera() 管线抓取帧并编码 JPEG。"""

    def __init__(
        self,
        width: int = 640,
        height: int = 480,
        jpeg_quality: int = 80,
        mjpg_fallback_url: str = "http://127.0.0.1:8080/?action=stream",
        prefer_mjpg: Optional[bool] = None,
        start_mjpeg_stream: bool = False,
        stream_host: str = "0.0.0.0",
        stream_port: int = 8090,
    ):
        self.width = width
        self.height = height
        self.jpeg_quality = int(jpeg_quality)
        self.mjpg_url = mjpg_fallback_url
        self.camera = None
        self.cap = None
        self.use_mjpg = False
        self.stream_host = stream_host
        self.stream_port = stream_port

        # 环境变量强制走 MJPG（避免 /dev/video0 被占用时的警告/阻塞）
        env_prefer = os.getenv("GEMINI_LIVE_CAMERA", "").lower() in ("mjpg", "http", "stream")
        prefer_mjpg = env_prefer if prefer_mjpg is None else prefer_mjpg

        primary_ok = False
        if Camera is not None and not prefer_mjpg:
            try:
                cam = Camera.Camera(resolution=(self.width, self.height))
                cam.camera_open()
                # 等待少量时间看是否有帧
                for _ in range(10):
                    if getattr(cam, "frame", None) is not None:
                        break
                    time.sleep(0.05)
                if getattr(cam, "frame", None) is not None:
                    self.camera = cam
                    primary_ok = True
                else:
                    raise RuntimeError("no frame from Camera")
            except Exception as e:
                logging.warning("Primary Camera open failed: %s; falling back to MJPG stream %s", e, self.mjpg_url)
                try:
                    cam.camera_close()
                except Exception:
                    pass

        if not primary_ok:
            self.cap = cv2.VideoCapture(self.mjpg_url)
            if not self.cap.isOpened():
                raise RuntimeError(f"Failed to open camera via MJPG stream {self.mjpg_url}")
            self.use_mjpg = True
            logging.info("CameraStreamer using MJPG stream %s", self.mjpg_url)
        else:
            logging.info("CameraStreamer using direct Camera module (V4L2)")

        if start_mjpeg_stream:
            try:
                self._start_hud_stream(self.stream_host, self.stream_port)
            except Exception as e:
                logging.warning("Failed to start HUD MJPEG stream on %s:%s: %s", self.stream_host, self.stream_port, e)

    def read_jpeg_bytes(self) -> Optional[bytes]:
        """返回最新帧的 JPEG bytes；若无画面则返回 None。"""
        if self.use_mjpg:
            if self.cap is None:
                return None
            ok_cap, frame = self.cap.read()
            if not ok_cap:
                return None
        else:
            frame = getattr(self.camera, "frame", None)
        if frame is None:
            return None
        frame = self._overlay_hud(frame)
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

    def _overlay_hud(self, frame):
        """在画面上叠加简要遥测，帮助模型/人类判断当前状态。"""
        tel = TELEMETRY.snapshot()
        overlay = frame.copy()
        y0 = 20
        dy = 18
        lines = [
            f"ARM x={tel['arm_pose_cm']['x']:.1f} y={tel['arm_pose_cm']['y']:.1f} z={tel['arm_pose_cm']['z']:.1f}",
            f"GRIPPER {tel['gripper']}",
            f"CAR v={tel['chassis']['velocity_mm_s']:.0f}mm/s dir={tel['chassis']['direction_deg']:.0f} ang={tel['chassis']['angular_rate']:.2f}",
        ]
        for i, text in enumerate(lines):
            y = y0 + i * dy
            cv2.putText(
                overlay,
                text,
                (10, y),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.5,
                (0, 255, 0),
                1,
                cv2.LINE_AA,
            )
        return overlay

    def _start_hud_stream(self, host: str, port: int) -> None:
        """启动简单的 MJPEG 流（带 HUD），便于浏览器查看。"""
        streamer = self
        boundary = "frame"

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self_inner):
                # 单帧快照：便于用 curl/浏览器快速检查 HUD
                if self_inner.path.endswith("snapshot"):
                    jpeg = streamer.read_jpeg_bytes()
                    if jpeg is None:
                        self_inner.send_response(503)
                        self_inner.end_headers()
                        return
                    self_inner.send_response(200)
                    self_inner.send_header("Content-Type", "image/jpeg")
                    self_inner.send_header("Content-Length", str(len(jpeg)))
                    self_inner.send_header("Cache-Control", "no-cache, private")
                    self_inner.send_header("Pragma", "no-cache")
                    self_inner.end_headers()
                    self_inner.wfile.write(jpeg)
                    return

                self_inner.send_response(200)
                self_inner.send_header("Age", 0)
                self_inner.send_header("Cache-Control", "no-cache, private")
                self_inner.send_header("Pragma", "no-cache")
                self_inner.send_header("Content-Type", f"multipart/x-mixed-replace; boundary={boundary}")
                self_inner.end_headers()
                try:
                    while True:
                        jpeg = streamer.read_jpeg_bytes()
                        if jpeg is None:
                            time.sleep(0.05)
                            continue
                        self_inner.wfile.write(b"--" + boundary.encode() + b"\r\n")
                        self_inner.wfile.write(b"Content-Type: image/jpeg\r\n")
                        self_inner.wfile.write(f"Content-Length: {len(jpeg)}\r\n".encode())
                        self_inner.wfile.write(b"\r\n")
                        self_inner.wfile.write(jpeg)
                        self_inner.wfile.write(b"\r\n")
                        self_inner.wfile.flush()
                        time.sleep(0.02)
                except (BrokenPipeError, ConnectionResetError):
                    return
                except Exception as e:
                    logging.warning("HUD stream error: %s", e)
                    return

            def log_message(self_inner, format, *args):
                return  # silence

        server = ThreadingHTTPServer((host, port), Handler)
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        logging.info("HUD MJPEG stream started on http://%s:%s/", host, port)


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
        (2.0, 16.0),    # z 上限收紧，避免过伸导致舵机极限
    )
    MAX_STEP_M = 0.05  # 单次移动最大幅度（米），避免模型一下子走太远
    OBSERVE_STEP_M = 0.04  # 环视单步（米）
    OBSERVE_DWELL_S = 0.6  # 环视停顿时间

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
        TELEMETRY.arm_pose_cm = tuple(self.pose_cm)
        TELEMETRY.gripper_state = "open"
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
        # 限制单步位移，避免大步导致 IK 失败或撞限位
        dx_m = max(min(dx_m, self.MAX_STEP_M), -self.MAX_STEP_M)
        dy_m = max(min(dy_m, self.MAX_STEP_M), -self.MAX_STEP_M)
        dz_m = max(min(dz_m, self.MAX_STEP_M), -self.MAX_STEP_M)

        scale = 100.0  # meters -> centimeters
        target = (
            self.pose_cm[0] + dx_m * scale,
            self.pose_cm[1] + dy_m * scale,
            self.pose_cm[2] + dz_m * scale,
        )
        target = self._clamp_pose(target)
        info = {
            "pose_cm": {"x": target[0], "y": target[1], "z": target[2]},
            "executed": False,
            "reachable": True,
        }

        if self.enable_hardware and self.arm is not None:
            # 先做 IK 可达性检查，不执行舵机
            check = self.arm.setPitchRange(target, -90, 0)
            if check is False:
                info["reachable"] = False
                info["ik_result"] = "no_solution"
                return info

            # 真正执行移动
            result = self.arm.setPitchRangeMoving(target, -90, -90, 0, 800)
            if result is False:
                info["reachable"] = False
                info["ik_result"] = "no_solution"
            else:
                servos, alpha, movetime = result
                info["ik_result"] = "ok"
                info["ik_time_ms"] = int(movetime) if movetime is not None else None
                info["executed"] = True

        # 更新内部位姿缓存（即便干跑也更新，方便下一步相对位移）
        self.pose_cm = list(target)
        TELEMETRY.arm_pose_cm = tuple(self.pose_cm)
        return info

    def observe_sweep(self, cycles: int = 1, step_m: Optional[float] = None, dwell: Optional[float] = None) -> None:
        """
        简单环视：左右小幅往返，帮助模型熟悉环境。
        仅在 enable_hardware=True 时生效，干跑时直接返回。
        """
        if not self.enable_hardware:
            logging.info("observe_sweep skipped (hardware disabled)")
            return
        step = step_m if step_m is not None else self.OBSERVE_STEP_M
        dwell = dwell if dwell is not None else self.OBSERVE_DWELL_S
        pattern = [step, -2 * step, step]  # 左->右->回中（净位移 0）
        for c in range(max(1, cycles)):
            for offset in pattern:
                res = self.move_relative(offset, 0.0, 0.0)
                logging.info("observe_sweep move dx=%.3f res=%s", offset, res)
                time.sleep(dwell)

    def control_gripper(self, action: str) -> Dict:
        """开合夹爪。"""
        action = action.lower()
        if action not in ("open", "close"):
            return {"status": "error", "message": f"unknown action {action}"}

        pulse = self.gripper_open if action == "open" else self.gripper_close
        info = {"status": "ok", "action": action, "pulse": pulse, "executed": self.enable_hardware}
        if self.enable_hardware and Board is not None:
            Board.setPWMServoPulse(1, pulse, 400)
        TELEMETRY.gripper_state = action
        return info


class CarChassisShim:
    """小车底盘控制封装（麦克纳姆轮）。"""

    MAX_SPEED_MM_S = 150.0  # 速度上限，避免过快
    MAX_ANGULAR_RATE = 1.5  # 旋转速率上限（经验值）

    def __init__(self, enable_hardware: bool = False):
        self.enable_hardware = enable_hardware and MecanumChassis is not None and Board is not None
        self.chassis = MecanumChassis() if self.enable_hardware else None

    def set_velocity(self, velocity_mm_s: float, direction_deg: float, angular_rate: float) -> Dict:
        """
        以极坐标方式控制车身：
        - velocity_mm_s: 线速度（mm/s）
        - direction_deg: 行进方向 0-360deg（0 右，90 前，180 左，270 后）
        - angular_rate: 底盘自旋速度，正值顺时针
        """
        # 归一化并限幅
        vel = max(0.0, min(float(velocity_mm_s), self.MAX_SPEED_MM_S))
        ang_rate = max(-self.MAX_ANGULAR_RATE, min(float(angular_rate), self.MAX_ANGULAR_RATE))
        direction = float(direction_deg) % 360.0
        info = {
            "velocity_mm_s": vel,
            "direction_deg": direction,
            "angular_rate": ang_rate,
            "executed": False,
            "hardware": self.enable_hardware,
        }
        if self.enable_hardware and self.chassis is not None:
            self.chassis.set_velocity(vel, direction, ang_rate)
            info["executed"] = True
        else:
            info["message"] = "hardware_disabled"
        TELEMETRY.chassis_velocity_mm_s = vel
        TELEMETRY.chassis_direction_deg = direction
        TELEMETRY.chassis_angular_rate = ang_rate
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
        telemetry = {"sonar_mm": int(dist) if dist is not None else None}
        if not self.enabled or dist is None:
            return False, None, telemetry
        if dist < self.guard_threshold_mm and dz_m > 0:
            return True, f"Obstacle {dist} mm ahead; move blocked", telemetry
        return False, None, telemetry
