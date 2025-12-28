# MasterPi 树莓派机器人系统

树莓派机械臂机器人控制系统，支持舵机控制、摄像头视觉识别、颜色跟踪等功能。

## 快速开始

### 环境配置

```bash
cd /home/pi
./setup.sh
```

或手动配置：

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 推荐使用方式

**必须先启动主程序：**

```bash
# 终端1：启动主程序（必须）
cd /home/pi/MasterPi
sudo -E /home/pi/venv/bin/python MasterPi.py
```

主程序会启动：
- RPC 服务器（端口 9030）- 用于远程控制
- MJPG 流服务器（端口 8080）- 用于摄像头视频流
- 硬件初始化（摄像头、超声波传感器、RGB LED 等）

**然后通过以下方式使用：**
- 通过 RPC 接口调用功能（推荐）
- 通过其他客户端控制
- 使用 GUI 界面（`MasterPi_PC_Software/Arm.py`）

## 项目结构

```
/home/pi/
├── MasterPi/              # 机器人主程序
│   ├── MasterPi.py       # 主程序入口（必须运行）
│   ├── Camera.py         # 摄像头控制
│   ├── HiwonderSDK/      # 硬件SDK
│   └── Functions/        # 功能模块
├── MasterPi_PC_Software/ # PC端GUI控制软件
├── requirements.txt       # Python依赖
└── setup.sh              # 环境配置脚本
```

## 功能说明

### 硬件控制

- **舵机控制**：6个PWM舵机（ID: 1, 3, 4, 5, 6）
- **马达控制**：4个直流马达（麦轮底盘）
- **摄像头**：OpenCV图像采集和处理
- **超声波传感器**：距离检测
- **RGB LED**：状态指示

### 功能模块

- 颜色识别（ColorDetect）
- 颜色跟踪（ColorTracking）
- 颜色分拣（ColorSorting）
- 视觉巡线（VisualPatrol）
- 智能避障（Avoidance）
- 远程控制（RemoteControl）

## 详细文档

- [快速开始指南](快速开始.md)
- [启动指南](启动指南.md)
- [硬件控制参考](硬件控制参考.md)

## 注意事项

1. **必须使用 sudo 运行**：硬件控制需要 root 权限（访问 `/dev/mem`）
2. **必须先启动 MasterPi.py**：大部分功能依赖主程序提供的服务
3. **虚拟环境**：建议始终在 venv 中运行代码
4. **I2C 和摄像头**：需要在 `raspi-config` 中启用

## 许可证

请查看各子项目的许可证文件。

