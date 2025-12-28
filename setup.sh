#!/bin/bash
# 树莓派机器人系统环境配置脚本

echo "=========================================="
echo "树莓派机器人系统环境配置"
echo "=========================================="

# 检查Python版本
echo "检查Python版本..."
python3 --version
if [ $? -ne 0 ]; then
    echo "错误: 未找到Python3，请先安装Python3"
    exit 1
fi

# 创建虚拟环境
echo ""
echo "创建Python虚拟环境..."
if [ ! -d "venv" ]; then
    python3 -m venv venv
    echo "虚拟环境创建成功"
else
    echo "虚拟环境已存在"
fi

# 激活虚拟环境
echo ""
echo "激活虚拟环境..."
source venv/bin/activate

# 升级pip
echo ""
echo "升级pip..."
pip install --upgrade pip

# 安装依赖
echo ""
echo "安装Python依赖包..."
if [ -f "requirements.txt" ]; then
    pip install -r requirements.txt
    echo "依赖包安装完成"
else
    echo "警告: 未找到requirements.txt文件"
fi

# 检查系统依赖
echo ""
echo "检查系统配置..."
echo "请确保已启用以下功能："
echo "1. I2C接口: sudo raspi-config -> Interface Options -> I2C -> Enable"
echo "2. 摄像头: sudo raspi-config -> Interface Options -> Camera -> Enable"

# 检查I2C
echo ""
echo "检查I2C设备..."
if [ -c /dev/i2c-1 ]; then
    echo "I2C设备已找到: /dev/i2c-1"
    echo "运行 'sudo i2cdetect -y 1' 查看连接的设备"
else
    echo "警告: 未找到I2C设备，请检查I2C是否已启用"
fi

# 检查摄像头
echo ""
echo "检查摄像头..."
if [ -c /dev/video0 ] || [ -c /dev/video1 ]; then
    echo "摄像头设备已找到"
else
    echo "警告: 未找到摄像头设备，请检查摄像头连接"
fi

echo ""
echo "=========================================="
echo "环境配置完成！"
echo "=========================================="
echo ""
echo "下一步："
echo "1. 激活虚拟环境: source venv/bin/activate"
echo "2. 启动GUI: cd MasterPi_PC_Software && python3 Arm.py"
echo "3. 或启动主程序: cd MasterPi && python3 MasterPi.py"
echo ""
echo "详细说明请查看: 启动指南.md"

