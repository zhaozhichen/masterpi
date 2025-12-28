#!/usr/bin/python3
# coding=utf8
import os
import sys
sys.path.append('/home/pi/MasterPi/')
import time
import logging
import threading
from werkzeug.wrappers import Request, Response
from werkzeug.serving import run_simple
from jsonrpc import JSONRPCResponseManager, dispatcher
from ArmIK.ArmMoveIK import *
import HiwonderSDK as hwsdk
import HiwonderSDK.Misc as Misc
import HiwonderSDK.Board as Board
import HiwonderSDK.mecanum as mecanum
import Functions.Running as Running
import Functions.lab_adjust as lab_adjust
import Functions.ColorDetect as ColorDete
import Functions.ColorTracking as ColorTrack
import Functions.ColorSorting as ColorSort
import Functions.VisualPatrol as VisualPat
import Functions.Avoidance as Avoidan



if sys.version_info.major == 2:
    print('Please run this program with python3!')
    sys.exit(0)

__RPC_E01 = "E01 - Invalid number of parameter!"
__RPC_E02 = "E02 - Invalid parameter!"
__RPC_E03 = "E03 - Operation failed!"
__RPC_E04 = "E04 - Operation timeout!"
__RPC_E05 = "E05 - Not callable"

HWSONAR = None
QUEUE = None

# 初始化ArmIK实例用于机械臂逆运动学控制
try:
    AK = ArmIK()
except Exception as e:
    print(f"Warning: Failed to initialize ArmIK: {e}")
    AK = None

ColorDete.initMove()
ColorDete.setBuzzer(0.3)

chassis = mecanum.MecanumChassis()

@dispatcher.add_method
def map(x, in_min, in_max, out_min, out_max):
    """
    数值映射工具函数（内部使用）
    
    将输入值从一个范围映射到另一个范围。
    
    Args:
        x: 输入值
        in_min: 输入范围最小值
        in_max: 输入范围最大值
        out_min: 输出范围最小值
        out_max: 输出范围最大值
    
    Returns:
        映射后的值
    """
    return (x - in_min) * (out_max - out_min) / (in_max - in_min) + out_min

data = []
@dispatcher.add_method
def SetPWMServo(*args, **kwargs):
    """
    批量设置PWM舵机位置（通过角度值）
    
    功能描述：
        同时控制多个PWM舵机，通过角度值（-90到90度）设置位置。
        角度值会自动转换为脉冲值（500-2500）。
    
    参数格式：
        SetPWMServo(use_time, servo_count, servo_id1, angle1, servo_id2, angle2, ...)
    
    参数说明：
        use_time (int): 运行时间，单位毫秒，范围 0-30000
        servo_count (int): 要控制的舵机数量
        servo_id (int): 舵机ID，范围 1-6
        angle (float): 角度值，范围 -90 到 90 度
            - 90度对应脉冲值2500
            - 0度对应脉冲值1500（中位）
            - -90度对应脉冲值500
    
    返回值：
        tuple: (成功标志, 空元组, 方法名)
            - (True, (), 'SetPWMServo'): 成功
            - (False, 'E03 - Operation failed!', 'SetPWMServo'): 失败
    
    使用示例：
        # 控制2个舵机，用时1000ms
        SetPWMServo(1000, 2, 1, 90, 2, -90)
        # 舵机1转到90度，舵机2转到-90度
    
    注意事项：
        - 角度值会自动映射到脉冲值范围
        - 可以同时控制最多6个舵机
        - 所有舵机使用相同的运行时间
    """
    ret = (True, (), 'SetPWMServo')
    print("SetPWMServo:",args)
    arglen = len(args)
    try:
        servos = args[2:arglen:2]
        pulses = args[3:arglen:2]
        use_times = args[0]
        servos_num =  args[1]
        data.insert(0, use_times)
        data.insert(1, servos_num)
        
        dat = zip(servos, pulses)
        for (s, p) in dat:
            pulses = int(map(p,90,-90,500,2500))
            data.append(s)
            data.append(pulses)
            
        Board.setPWMServosPulse(data)
        data.clear()
        
    except Exception as e:
        print('error3:', e)
        ret = (False, __RPC_E03, 'SetPWMServo')
    return ret

@dispatcher.add_method
def SetPWMServoPulseSingle(servo_id, pulse, use_time):
    """
    单个PWM舵机脉冲控制
    
    功能描述：
        控制单个PWM舵机，通过脉冲值直接设置位置。
        适用于需要精确控制单个舵机的场景。
    
    参数说明：
        servo_id (int): 舵机ID，范围 1-6
        pulse (int): 脉冲值，范围 500-2500
            - 500: 最小位置（-90度）
            - 1500: 中位（0度）
            - 2500: 最大位置（90度）
        use_time (int): 运行时间，单位毫秒，范围 0-30000
    
    返回值：
        tuple: (成功标志, 空元组, 方法名)
            - (True, (), 'SetPWMServoPulseSingle'): 成功
            - (False, 'E02 - Invalid parameter!', 'SetPWMServoPulseSingle'): 参数错误
            - (False, 'E03 - Operation failed!', 'SetPWMServoPulseSingle'): 操作失败
    
    使用示例：
        # 控制舵机1转到中位，用时500ms
        SetPWMServoPulseSingle(1, 1500, 500)
        
        # 控制舵机3转到最大位置，用时1000ms
        SetPWMServoPulseSingle(3, 2500, 1000)
    
    注意事项：
        - 脉冲值会被自动限制在500-2500范围内
        - 运行时间会被自动限制在0-30000ms范围内
        - 如果舵机ID超出范围，返回E02错误
    """
    ret = (True, (), 'SetPWMServoPulseSingle')
    try:
        if servo_id < 1 or servo_id > 6:
            return (False, __RPC_E02, 'SetPWMServoPulseSingle')
        Board.setPWMServoPulse(servo_id, pulse, use_time)
    except Exception as e:
        print('error:', e)
        ret = (False, __RPC_E03, 'SetPWMServoPulseSingle')
    return ret

@dispatcher.add_method
def SetPWMServoAngle(servo_id, angle):
    """
    PWM舵机角度控制
    
    功能描述：
        通过角度值控制PWM舵机，更直观的控制方式。
        角度值会自动转换为对应的脉冲值。
    
    参数说明：
        servo_id (int): 舵机ID，范围 1-6
        angle (float): 角度值，范围 0-180度
            - 0度: 最小位置
            - 90度: 中位
            - 180度: 最大位置
    
    返回值：
        tuple: (成功标志, 空元组, 方法名)
            - (True, (), 'SetPWMServoAngle'): 成功
            - (False, 'E02 - Invalid parameter!', 'SetPWMServoAngle'): 参数错误
            - (False, 'E03 - Operation failed!', 'SetPWMServoAngle'): 操作失败
    
    使用示例：
        # 控制舵机1转到90度（中位）
        SetPWMServoAngle(1, 90)
        
        # 控制舵机2转到0度
        SetPWMServoAngle(2, 0)
    
    注意事项：
        - 角度值会被自动限制在0-180度范围内
        - 如果舵机ID超出范围，返回E02错误
        - 此方法使用默认运行时间
    """
    ret = (True, (), 'SetPWMServoAngle')
    try:
        if servo_id < 1 or servo_id > 6:
            return (False, __RPC_E02, 'SetPWMServoAngle')
        Board.setPWMServoAngle(servo_id, angle)
    except Exception as e:
        print('error:', e)
        ret = (False, __RPC_E03, 'SetPWMServoAngle')
    return ret

@dispatcher.add_method
def GetPWMServoPulse(servo_id):
    """
    获取PWM舵机当前脉冲值
    
    功能描述：
        读取指定PWM舵机当前的脉冲值，用于查询舵机位置。
    
    参数说明：
        servo_id (int): 舵机ID，范围 1-6
    
    返回值：
        tuple: (成功标志, 脉冲值, 方法名)
            - (True, pulse_value, 'GetPWMServoPulse'): 成功，返回脉冲值（500-2500）
            - (False, 'E02 - Invalid parameter!', 'GetPWMServoPulse'): 参数错误
            - (False, 'E03 - Operation failed!', 'GetPWMServoPulse'): 操作失败
    
    使用示例：
        # 读取舵机1的当前脉冲值
        result = GetPWMServoPulse(1)
        if result[0]:  # 检查是否成功
            pulse = result[1]  # 获取脉冲值
            print(f"舵机1当前脉冲值: {pulse}")
    
    注意事项：
        - 返回的是内部缓存的脉冲值，可能不是实时读取
        - 如果舵机ID超出范围，返回E02错误
    """
    ret = (True, 0, 'GetPWMServoPulse')
    try:
        if servo_id < 1 or servo_id > 6:
            return (False, __RPC_E02, 'GetPWMServoPulse')
        pulse = Board.getPWMServoPulse(servo_id)
        ret = (True, pulse, 'GetPWMServoPulse')
    except Exception as e:
        print('error:', e)
        ret = (False, __RPC_E03, 'GetPWMServoPulse')
    return ret

@dispatcher.add_method
def GetPWMServoAngle(servo_id):
    """
    获取PWM舵机当前角度
    
    功能描述：
        读取指定PWM舵机当前的角度值，用于查询舵机位置。
        角度值由脉冲值计算得出。
    
    参数说明：
        servo_id (int): 舵机ID，范围 1-6
    
    返回值：
        tuple: (成功标志, 角度值, 方法名)
            - (True, angle_value, 'GetPWMServoAngle'): 成功，返回角度值（0-180度）
            - (False, 'E02 - Invalid parameter!', 'GetPWMServoAngle'): 参数错误
            - (False, 'E03 - Operation failed!', 'GetPWMServoAngle'): 操作失败
    
    使用示例：
        # 读取舵机1的当前角度
        result = GetPWMServoAngle(1)
        if result[0]:  # 检查是否成功
            angle = result[1]  # 获取角度值
            print(f"舵机1当前角度: {angle}度")
    
    注意事项：
        - 返回的角度值由脉冲值计算得出，可能不是实时读取
        - 如果舵机ID超出范围，返回E02错误
    """
    ret = (True, 0, 'GetPWMServoAngle')
    try:
        if servo_id < 1 or servo_id > 6:
            return (False, __RPC_E02, 'GetPWMServoAngle')
        angle = Board.getPWMServoAngle(servo_id)
        ret = (True, angle, 'GetPWMServoAngle')
    except Exception as e:
        print('error:', e)
        ret = (False, __RPC_E03, 'GetPWMServoAngle')
    return ret

@dispatcher.add_method
def SetMovementAngle(angle):
    """
    设置底盘移动方向（简化版，固定速度）
    
    功能描述：
        控制麦克纳姆轮底盘向指定方向移动，速度固定为70mm/s。
        这是简化版的移动控制，如需自定义速度请使用SetMecanumVelocity。
    
    参数说明：
        angle (float): 移动方向角度，范围 0-360度
            - 0度: 正前方
            - 90度: 正左方
            - 180度: 正后方
            - 270度: 正右方
            - -1: 停止移动
    
    返回值：
        tuple: (成功标志, 空元组, 方法名)
            - (True, (), 'SetMovementAngle'): 成功
            - (False, 'E03 - Operation failed!', 'SetMovementAngle'): 操作失败
    
    使用示例：
        # 向45度方向移动（右前方）
        SetMovementAngle(45)
        
        # 停止移动
        SetMovementAngle(-1)
        
        # 向正前方移动
        SetMovementAngle(0)
    
    注意事项：
        - 速度固定为70mm/s，无法自定义
        - 角速度固定为0，无法旋转
        - 如需更灵活的控制，请使用SetMecanumVelocity
    """
    print(angle)
    try:
        if angle == -1:
            chassis.set_velocity(0,0,0)
            
        else:
            chassis.set_velocity(70,angle,0)
       
    except:
        ret = (False, __RPC_E03, 'SetMovementAngle')
        return ret

@dispatcher.add_method
def SetMecanumVelocity(velocity, direction, angular_rate):
    """
    设置Mecanum底盘速度（完整极坐标控制）
    
    功能描述：
        使用极坐标方式控制麦克纳姆轮底盘，支持自定义速度、方向和角速度。
        这是最灵活的底盘控制方式。
    
    参数说明：
        velocity (float): 移动速度，单位 mm/s，范围建议 0-200
            - 正值：向前移动
            - 0: 停止移动
        direction (float): 移动方向角度，范围 0-360度
            - 0度: 正前方（X轴正方向）
            - 90度: 正左方（Y轴正方向）
            - 180度: 正后方（X轴负方向）
            - 270度: 正右方（Y轴负方向）
        angular_rate (float): 角速度，单位 度/秒
            - 正值: 逆时针旋转
            - 负值: 顺时针旋转
            - 0: 不旋转
    
    返回值：
        tuple: (成功标志, 空元组, 方法名)
            - (True, (), 'SetMecanumVelocity'): 成功
            - (False, 'E03 - Operation failed!', 'SetMecanumVelocity'): 操作失败
    
    使用示例：
        # 以100mm/s速度向45度方向移动，同时以10度/秒逆时针旋转
        SetMecanumVelocity(100, 45, 10)
        
        # 以50mm/s速度向正前方移动，不旋转
        SetMecanumVelocity(50, 0, 0)
        
        # 停止移动
        SetMecanumVelocity(0, 0, 0)
        
        # 原地旋转，不移动
        SetMecanumVelocity(0, 0, 30)
    
    注意事项：
        - 速度、方向和角速度可以同时设置，实现复杂运动
        - 建议速度不超过200mm/s，避免失控
        - 角速度建议不超过50度/秒
    """
    ret = (True, (), 'SetMecanumVelocity')
    try:
        chassis.set_velocity(velocity, direction, angular_rate)
    except Exception as e:
        print('error:', e)
        ret = (False, __RPC_E03, 'SetMecanumVelocity')
    return ret

@dispatcher.add_method
def SetMecanumTranslation(velocity_x, velocity_y):
    """
    设置Mecanum底盘平移速度（笛卡尔坐标控制）
    
    功能描述：
        使用笛卡尔坐标（X、Y方向）控制麦克纳姆轮底盘平移。
        更直观的控制方式，适合需要精确控制X、Y方向速度的场景。
    
    参数说明：
        velocity_x (float): X方向速度，单位 mm/s
            - 正值: 向右移动
            - 负值: 向左移动
            - 0: X方向不移动
        velocity_y (float): Y方向速度，单位 mm/s
            - 正值: 向前移动
            - 负值: 向后移动
            - 0: Y方向不移动
    
    返回值：
        tuple: (成功标志, 空元组, 方法名)
            - (True, (), 'SetMecanumTranslation'): 成功
            - (False, 'E03 - Operation failed!', 'SetMecanumTranslation'): 操作失败
    
    使用示例：
        # 向右前方移动（X=50, Y=50）
        SetMecanumTranslation(50, 50)
        
        # 只向右移动，不前后移动
        SetMecanumTranslation(50, 0)
        
        # 只向前移动，不左右移动
        SetMecanumTranslation(0, 50)
        
        # 停止移动
        SetMecanumTranslation(0, 0)
    
    注意事项：
        - 此方法只控制平移，不控制旋转
        - 速度会自动转换为极坐标形式
        - 建议速度不超过200mm/s
    """
    ret = (True, (), 'SetMecanumTranslation')
    try:
        chassis.translation(velocity_x, velocity_y)
    except Exception as e:
        print('error:', e)
        ret = (False, __RPC_E03, 'SetMecanumTranslation')
    return ret

@dispatcher.add_method
def ResetMecanumMotors():
    """
    重置Mecanum底盘所有电机
    
    功能描述：
        停止所有电机并重置底盘状态（速度、方向、角速度归零）。
        用于紧急停止或初始化底盘状态。
    
    参数说明：
        无参数
    
    返回值：
        tuple: (成功标志, 空元组, 方法名)
            - (True, (), 'ResetMecanumMotors'): 成功
            - (False, 'E03 - Operation failed!', 'ResetMecanumMotors'): 操作失败
    
    使用示例：
        # 紧急停止底盘
        ResetMecanumMotors()
        
        # 初始化底盘状态
        ResetMecanumMotors()
    
    注意事项：
        - 会立即停止所有4个电机
        - 重置内部状态变量
        - 建议在程序开始和结束时调用
    """
    ret = (True, (), 'ResetMecanumMotors')
    try:
        chassis.reset_motors()
    except Exception as e:
        print('error:', e)
        ret = (False, __RPC_E03, 'ResetMecanumMotors')
    return ret

@dispatcher.add_method
def GetMecanumStatus():
    """
    获取Mecanum底盘当前状态
    
    功能描述：
        查询底盘当前的移动状态，包括速度、方向和角速度。
        用于监控底盘运动状态。
    
    参数说明：
        无参数
    
    返回值：
        tuple: (成功标志, 状态字典, 方法名)
            成功时返回字典包含：
            - velocity (float): 当前速度 (mm/s)
            - direction (float): 当前方向角度 (0-360度)
            - angular_rate (float): 当前角速度 (度/秒)
            - (False, 'E03 - Operation failed!', 'GetMecanumStatus'): 操作失败
    
    使用示例：
        # 查询底盘状态
        result = GetMecanumStatus()
        if result[0]:
            status = result[1]
            print(f"速度: {status['velocity']} mm/s")
            print(f"方向: {status['direction']} 度")
            print(f"角速度: {status['angular_rate']} 度/秒")
    
    注意事项：
        - 返回的是内部状态变量，可能不是实时硬件状态
        - 如果底盘未初始化，可能返回默认值
    """
    ret = (True, (), 'GetMecanumStatus')
    try:
        status = {
            'velocity': chassis.velocity,
            'direction': chassis.direction,
            'angular_rate': chassis.angular_rate
        }
        ret = (True, status, 'GetMecanumStatus')
    except Exception as e:
        print('error:', e)
        ret = (False, __RPC_E03, 'GetMecanumStatus')
    return ret

@dispatcher.add_method
def SetBrushMotor(*args, **kwargs):
    """
    设置刷式电机速度
    
    功能描述：
        控制扩展板上的刷式电机速度，可以同时控制多个电机。
        电机编号：1-4，对应底盘的4个轮子。
    
    参数格式：
        SetBrushMotor(motor_id1, speed1, motor_id2, speed2, ...)
    
    参数说明：
        motor_id (int): 电机ID，范围 1-4
            - 1: 电机1
            - 2: 电机2
            - 3: 电机3
            - 4: 电机4
        speed (int): 电机速度，范围 -100 到 100
            - 正值: 正转
            - 负值: 反转
            - 0: 停止
            - 绝对值越大，速度越快
    
    返回值：
        tuple: (成功标志, 空元组, 方法名)
            - (True, (), 'SetBrushMotor'): 成功
            - (False, 'E01 - Invalid number of parameter!', 'SetBrushMotor'): 参数数量错误
            - (False, 'E02 - Invalid parameter!', 'SetBrushMotor'): 参数值错误
            - (False, 'E03 - Operation failed!', 'SetBrushMotor'): 操作失败
    
    使用示例：
        # 控制电机1以速度50正转
        SetBrushMotor(1, 50)
        
        # 同时控制电机1和2
        SetBrushMotor(1, 50, 2, -50)
        
        # 停止所有电机
        SetBrushMotor(1, 0, 2, 0, 3, 0, 4, 0)
    
    注意事项：
        - 参数必须是成对出现（电机ID + 速度）
        - 速度会被自动限制在-100到100范围内
        - 电机2和4的方向会自动反转（硬件特性）
    """
    ret = (True, (), 'SetBrushMotor')
    arglen = len(args)
    print(args)
    if 0 != (arglen % 2):
        return (False, __RPC_E01, 'SetBrushMotor')
    try:
        motors = args[0:arglen:2]
        speeds = args[1:arglen:2]
        
        for m in motors:
            if m < 1 or m > 4:
                return (False, __RPC_E02, 'SetBrushMotor')
            
        dat = zip(motors, speeds)
        for m, s in dat:
            Board.setMotor(m, s)
            
    except:
        ret = (False, __RPC_E03, 'SetBrushMotor')
    return ret

@dispatcher.add_method
def GetMotor(index):
    """
    获取电机当前速度
    
    功能描述：
        读取指定电机的当前速度值，用于查询电机状态。
    
    参数说明：
        index (int): 电机ID，范围 1-4
    
    返回值：
        tuple: (成功标志, 速度值, 方法名)
            - (True, speed_value, 'GetMotor'): 成功，返回速度值（-100到100）
            - (False, 'E02 - Invalid parameter!', 'GetMotor'): 参数错误
            - (False, 'E03 - Operation failed!', 'GetMotor'): 操作失败
    
    使用示例：
        # 读取电机1的当前速度
        result = GetMotor(1)
        if result[0]:
            speed = result[1]
            print(f"电机1当前速度: {speed}")
    
    注意事项：
        - 返回的是内部缓存的速度值，可能不是实时硬件状态
        - 如果电机ID超出范围，返回E02错误
    """
    ret = (True, 0, 'GetMotor')
    try:
        if index < 1 or index > 4:
            return (False, __RPC_E02, 'GetMotor')
        speed = Board.getMotor(index)
        ret = (True, speed, 'GetMotor')
    except Exception as e:
        print('error:', e)
        ret = (False, __RPC_E03, 'GetMotor')
    return ret

@dispatcher.add_method
def StopAllMotors():
    """
    停止所有电机
    
    功能描述：
        立即停止所有4个刷式电机，用于紧急停止。
    
    参数说明：
        无参数
    
    返回值：
        tuple: (成功标志, 空元组, 方法名)
            - (True, (), 'StopAllMotors'): 成功
            - (False, 'E03 - Operation failed!', 'StopAllMotors'): 操作失败
    
    使用示例：
        # 紧急停止所有电机
        StopAllMotors()
    
    注意事项：
        - 会立即停止所有4个电机（ID 1-4）
        - 等同于 SetBrushMotor(1, 0, 2, 0, 3, 0, 4, 0)
    """
    ret = (True, (), 'StopAllMotors')
    try:
        for i in range(1, 5):
            Board.setMotor(i, 0)
    except Exception as e:
        print('error:', e)
        ret = (False, __RPC_E03, 'StopAllMotors')
    return ret

@dispatcher.add_method
def GetSonarDistance():
    """
    获取超声波传感器距离
    
    功能描述：
        读取超声波传感器检测到的距离值。
        需要确保超声波传感器已正确初始化。
    
    参数说明：
        无参数
    
    返回值：
        tuple: (成功标志, 距离值, 方法名)
            - (True, distance, 'GetSonarDistance'): 成功，返回距离值（单位：cm）
            - (False, 'E03 - Operation failed!', 'GetSonarDistance'): 操作失败
    
    使用示例：
        # 读取距离
        result = GetSonarDistance()
        if result[0]:
            distance = result[1]
            print(f"检测到距离: {distance} cm")
    
    注意事项：
        - 需要确保HWSONAR已正确初始化
        - 距离值单位是厘米（cm）
        - 检测范围通常为2-400cm
        - 如果传感器未初始化，会返回错误
    """
    global HWSONAR
    ret = (True, 0, 'GetSonarDistance')
    try:
        ret = (True, HWSONAR.getDistance(), 'GetSonarDistance')
    except:
        ret = (False, __RPC_E03, 'GetSonarDistance')
    return ret

@dispatcher.add_method
def GetBatteryVoltage():
    """
    获取电池电压
    
    功能描述：
        读取扩展板检测到的电池电压值。
        用于监控电池电量，防止电压过低损坏设备。
    
    参数说明：
        无参数
    
    返回值：
        tuple: (成功标志, 电压值, 方法名)
            - (True, voltage, 'GetBatteryVoltage'): 成功，返回电压值（单位：mV，毫伏）
            - (False, 'E03 - Operation failed!', 'GetBatteryVoltage'): 操作失败
    
    使用示例：
        # 读取电池电压
        result = GetBatteryVoltage()
        if result[0]:
            voltage_mv = result[1]
            voltage_v = voltage_mv / 1000.0
            print(f"电池电压: {voltage_v:.2f} V")
            
            # 检查电压是否过低（例如低于7.2V）
            if voltage_v < 7.2:
                print("警告：电池电压过低！")
    
    注意事项：
        - 返回值单位是毫伏（mV），需要除以1000得到伏特（V）
        - 正常电压范围：7.0V - 8.4V（2S锂电池）
        - 建议电压低于7.2V时停止使用，避免过放
    """
    ret = (True, 0, 'GetBatteryVoltage')
    try:
        ret = (True, Board.getBattery(), 'GetBatteryVoltage')
    except Exception as e:
        print(e)
        ret = (False, __RPC_E03, 'GetBatteryVoltage')
    return ret

@dispatcher.add_method
def SetSonarRGBMode(mode = 0):
    """
    设置超声波传感器RGB灯模式
    
    功能描述：
        设置超声波传感器上RGB灯的工作模式。
    
    参数说明：
        mode (int): 模式值，默认0
            - 0: 关闭模式
            - 其他值: 根据具体实现定义
    
    返回值：
        tuple: (成功标志, (mode,), 方法名)
            - (True, (mode,), 'SetSonarRGBMode'): 成功
    
    使用示例：
        # 关闭RGB灯
        SetSonarRGBMode(0)
    
    注意事项：
        - 需要确保HWSONAR已正确初始化
    """
    global HWSONAR
    
    HWSONAR.setRGBMode(mode)
    return (True, (mode,), 'SetSonarRGBMode')

@dispatcher.add_method
def SetSonarRGB(index, r, g, b):
    """
    设置超声波传感器RGB灯颜色
    
    功能描述：
        设置超声波传感器上RGB灯的颜色。
        如果index为0，会同时设置两个LED灯。
    
    参数说明：
        index (int): LED索引
            - 0: 同时设置两个LED
            - 1: 设置LED 1
            - 2: 设置LED 2
        r (int): 红色分量，范围 0-255
        g (int): 绿色分量，范围 0-255
        b (int): 蓝色分量，范围 0-255
    
    返回值：
        tuple: (成功标志, (r, g, b), 方法名)
            - (True, (r, g, b), 'SetSonarRGB'): 成功
    
    使用示例：
        # 设置为红色
        SetSonarRGB(0, 255, 0, 0)
        
        # 设置为绿色
        SetSonarRGB(1, 0, 255, 0)
        
        # 设置为蓝色
        SetSonarRGB(2, 0, 0, 255)
    
    注意事项：
        - 需要确保HWSONAR已正确初始化
        - RGB值会被限制在0-255范围内
    """
    global HWSONAR
    print((r,g,b))
    if index == 0:
        HWSONAR.setPixelColor(0, Board.PixelColor(r, g, b))
        HWSONAR.setPixelColor(1, Board.PixelColor(r, g, b))
    else:
        HWSONAR.setPixelColor(index, (r, g, b))
    return (True, (r, g, b), 'SetSonarRGB')

@dispatcher.add_method
def SetSonarRGBBreathCycle(index, color, cycle):
    """
    设置超声波RGB灯呼吸效果
    
    功能描述：
        设置超声波传感器RGB灯的呼吸效果参数。
        呼吸效果是指LED灯逐渐变亮再变暗的循环效果。
    
    参数说明：
        index (int): LED索引，范围 0-1
        color (tuple): 颜色值，格式 (r, g, b)，范围 0-255
        cycle (int): 呼吸周期，单位毫秒
    
    返回值：
        tuple: (成功标志, (index, color, cycle), 方法名)
    
    使用示例：
        # 设置LED 0为红色呼吸效果，周期1000ms
        SetSonarRGBBreathCycle(0, (255, 0, 0), 1000)
    
    注意事项：
        - 需要调用SetSonarRGBStartSymphony启动效果
        - 需要确保HWSONAR已正确初始化
    """
    global HWSONAR
    
    HWSONAR.setBreathCycle(index, color, cycle)
    return (True, (index, color, cycle), 'SetSonarRGBBreathCycle')

@dispatcher.add_method
def SetSonarRGBStartSymphony():
    """
    启动超声波RGB灯呼吸效果
    
    功能描述：
        启动已设置的RGB灯呼吸效果。
        需要先调用SetSonarRGBBreathCycle设置参数。
    
    参数说明：
        无参数
    
    返回值：
        tuple: (成功标志, 空元组, 方法名)
    
    使用示例：
        # 先设置呼吸效果
        SetSonarRGBBreathCycle(0, (255, 0, 0), 1000)
        # 然后启动
        SetSonarRGBStartSymphony()
    
    注意事项：
        - 需要先设置呼吸效果参数
        - 需要确保HWSONAR已正确初始化
    """
    global HWSONAR
    
    HWSONAR.startSymphony()    
    return (True, (), 'SetSonarRGBStartSymphony')

@dispatcher.add_method
def SetAvoidanceSpeed(speed=50):
    """
    设置避障功能移动速度
    
    功能描述：
        设置避障功能模块的移动速度。
        避障功能会根据超声波传感器检测到的障碍物自动调整移动方向。
    
    参数说明：
        speed (int): 移动速度，默认50，范围建议 0-100
            - 值越大，移动速度越快
            - 0: 停止
    
    返回值：
        tuple: (成功标志, 结果, 方法名)
            通过主线程队列执行，返回结果
    
    使用示例：
        # 设置避障速度为50
        SetAvoidanceSpeed(50)
        
        # 设置较慢的避障速度
        SetAvoidanceSpeed(30)
    
    注意事项：
        - 需要先加载避障功能（LoadFunc(6)）
        - 速度值建议在30-70之间，过快可能导致避障不及时
    """
    print(speed)
    return runbymainth(Avoidan.setSpeed, (speed,))

@dispatcher.add_method
def SetSonarDistanceThreshold(new_threshold=30):
    """
    设置避障距离阈值
    
    功能描述：
        设置避障功能触发的最小距离阈值。
        当检测到障碍物距离小于此阈值时，会触发避障动作。
    
    参数说明：
        new_threshold (int): 距离阈值，单位 cm，默认30
            - 值越小，越接近障碍物才避障
            - 值越大，越早开始避障
            - 建议范围：20-50 cm
    
    返回值：
        tuple: (成功标志, 结果, 方法名)
            通过主线程队列执行，返回结果
    
    使用示例：
        # 设置阈值为30cm
        SetSonarDistanceThreshold(30)
        
        # 设置较敏感的阈值（40cm）
        SetSonarDistanceThreshold(40)
    
    注意事项：
        - 需要先加载避障功能（LoadFunc(6)）
        - 阈值过小可能导致碰撞
        - 阈值过大可能导致频繁避障
    """
    print(new_threshold)
    return runbymainth(Avoidan.setThreshold, (new_threshold,))

@dispatcher.add_method
def GetSonarDistanceThreshold():
    """
    获取当前避障距离阈值
    
    功能描述：
        查询当前设置的避障距离阈值。
    
    参数说明：
        无参数
    
    返回值：
        tuple: (成功标志, 阈值, 方法名)
            通过主线程队列执行，返回当前阈值（单位：cm）
    
    使用示例：
        # 查询当前阈值
        result = GetSonarDistanceThreshold()
        if result[0]:
            threshold = result[1]
            print(f"当前避障阈值: {threshold} cm")
    
    注意事项：
        - 需要先加载避障功能（LoadFunc(6)）
    """
    return runbymainth(Avoidan.getThreshold, ())

def runbymainth(req, pas):
    if callable(req):
        event = threading.Event()
        ret = [event, pas, None]
        QUEUE.put((req, ret))
        count = 0
        while ret[2] is None:
            time.sleep(0.01)
            count += 1
            if count > 200:
                break
        if ret[2] is not None:
            if ret[2][0]:
                return ret[2]
            else:
                return (False, __RPC_E03 + " " + ret[2][1])
        else:
            return (False, __RPC_E04)
    else:
        return (False, __RPC_E05)

@dispatcher.add_method
def SetBusServoPulse(*args, **kwargs):
    """
    批量设置总线舵机脉冲值
    
    功能描述：
        同时控制多个总线舵机（串口舵机），通过脉冲值设置位置。
        总线舵机通常用于机械臂等需要精确控制的场景。
    
    参数格式：
        SetBusServoPulse(use_time, servo_count, servo_id1, pulse1, servo_id2, pulse2, ...)
    
    参数说明：
        use_time (int): 运行时间，单位毫秒，范围 0-30000
        servo_count (int): 要控制的舵机数量
        servo_id (int): 舵机ID，范围 1-6
        pulse (int): 脉冲值，范围 0-1000
            - 0: 最小位置
            - 500: 中位
            - 1000: 最大位置
    
    返回值：
        tuple: (成功标志, 空元组, 方法名)
            - (True, (), 'SetBusServoPulse'): 成功
            - (False, 'E01 - Invalid number of parameter!', 'SetBusServoPulse'): 参数数量错误
            - (False, 'E02 - Invalid parameter!', 'SetBusServoPulse'): 参数值错误
            - (False, 'E03 - Operation failed!', 'SetBusServoPulse'): 操作失败
    
    使用示例：
        # 控制2个总线舵机，用时1000ms
        SetBusServoPulse(1000, 2, 1, 500, 2, 500)
        # 舵机1和2都转到中位
    
    注意事项：
        - 脉冲值会被自动限制在0-1000范围内
        - 所有舵机使用相同的运行时间
        - 总线舵机与PWM舵机不同，使用串口通信
    """
    ret = (True, (), 'SetBusServoPulse')
    arglen = len(args)
    if (args[1] * 2 + 2) != arglen or arglen < 4:
        return (False, __RPC_E01, 'SetBusServoPulse')
    try:
        servos = args[2:arglen:2]
        pulses = args[3:arglen:2]
        use_times = args[0]
        for s in servos:
           if s < 1 or s > 6:
                return (False, __RPC_E02)
        dat = zip(servos, pulses)
        for (s, p) in dat:
            Board.setBusServoPulse(s, p, use_times)
    except Exception as e:
        print(e)
        ret = (False, __RPC_E03, 'SetBusServoPulse')
    return ret

@dispatcher.add_method
def SetBusServoDeviation(*args):
    """
    设置总线舵机偏差值
    
    功能描述：
        设置总线舵机的角度偏差值，用于校准舵机位置。
        偏差值会在设置脉冲值时自动应用。
    
    参数说明：
        servo_id (int): 舵机ID，范围 1-6
        deviation (int): 偏差值，范围通常 -125 到 125
    
    返回值：
        tuple: (成功标志, 空元组, 方法名)
            - (True, (), 'SetBusServoDeviation'): 成功
            - (False, 'E01 - Invalid number of parameter!', 'SetBusServoDeviation'): 参数数量错误
            - (False, 'E03 - Operation failed!', 'SetBusServoDeviation'): 操作失败
    
    使用示例：
        # 设置舵机1的偏差为10
        SetBusServoDeviation(1, 10)
        
        # 设置舵机2的偏差为-5
        SetBusServoDeviation(2, -5)
    
    注意事项：
        - 偏差值只是临时设置，不会保存
        - 要永久保存，需要调用SaveBusServosDeviation
        - 偏差值会在下次设置脉冲时应用
    """
    ret = (True, (), 'SetBusServoDeviation')
    arglen = len(args)
    if arglen != 2:
        return (False, __RPC_E01, 'SetBusServoDeviation')
    try:
        servo = args[0]
        deviation = args[1]
        Board.setBusServoDeviation(servo, deviation)
    except Exception as e:
        print(e)
        ret = (False, __RPC_E03, 'SetBusServoDeviation')

@dispatcher.add_method
def GetBusServosDeviation(args):
    """
    获取所有总线舵机偏差值
    
    功能描述：
        读取所有总线舵机（1-6）的当前偏差值。
    
    参数说明：
        args (str): 必须为字符串 "readDeviation"
    
    返回值：
        tuple: (成功标志, 偏差值列表, 方法名)
            - (True, [dev1, dev2, ..., dev6], 'GetBusServosDeviation'): 成功
                返回6个舵机的偏差值列表
            - (False, 'E01 - Invalid number of parameter!', 'GetBusServosDeviation'): 参数错误
            - (False, 'E03 - Operation failed!', 'GetBusServosDeviation'): 操作失败
    
    使用示例：
        # 读取所有舵机偏差
        result = GetBusServosDeviation("readDeviation")
        if result[0]:
            deviations = result[1]
            for i, dev in enumerate(deviations, 1):
                print(f"舵机{i}偏差: {dev}")
    
    注意事项：
        - 如果读取失败，对应位置返回999
        - 需要确保总线舵机已正确连接
    """
    ret = (True, (), 'GetBusServosDeviation')
    data = []
    if args != "readDeviation":
        return (False, __RPC_E01, 'GetBusServosDeviation')
    try:
        for i in range(1, 7):
            dev = Board.getBusServoDeviation(i)
            if dev is None:
                dev = 999
            data.append(dev)
        ret = (True, data, 'GetBusServosDeviation')
    except Exception as e:
        print(e)
        ret = (False, __RPC_E03, 'GetBusServosDeviation')
    return ret 

@dispatcher.add_method
def SaveBusServosDeviation(args):
    """
    保存所有总线舵机偏差值（掉电保护）
    
    功能描述：
        将当前设置的所有总线舵机偏差值保存到舵机内部存储器。
        保存后，偏差值会在断电后仍然保留。
    
    参数说明：
        args (str): 必须为字符串 "downloadDeviation"
    
    返回值：
        tuple: (成功标志, 空元组, 方法名)
            - (True, (), 'SaveBusServosDeviation'): 成功
            - (False, 'E01 - Invalid number of parameter!', 'SaveBusServosDeviation'): 参数错误
            - (False, 'E03 - Operation failed!', 'SaveBusServosDeviation'): 操作失败
    
    使用示例：
        # 先设置偏差
        SetBusServoDeviation(1, 10)
        SetBusServoDeviation(2, -5)
        
        # 保存偏差值
        SaveBusServosDeviation("downloadDeviation")
    
    注意事项：
        - 保存操作会应用到所有6个舵机
        - 保存需要一定时间，请等待操作完成
        - 保存后偏差值会永久生效，直到重新设置
    """
    ret = (True, (), 'SaveBusServosDeviation')
    if args != "downloadDeviation":
        return (False, __RPC_E01, 'SaveBusServosDeviation')
    try:
        for i in range(1, 7):
            dev = Board.saveBusServoDeviation(i)
    except Exception as e:
        print(e)
        ret = (False, __RPC_E03, 'SaveBusServosDeviation')
    return ret 

@dispatcher.add_method
def UnloadBusServo(args):
    """
    卸载所有总线舵机（掉电）
    
    功能描述：
        使所有总线舵机进入掉电状态，舵机会失去保持力。
        用于节省功耗或安全停止。
    
    参数说明：
        args (str): 必须为字符串 "servoPowerDown"
    
    返回值：
        tuple: (成功标志, 空元组, 方法名)
            - (True, (), 'UnloadBusServo'): 成功
            - (False, 'E01 - Invalid number of parameter!', 'UnloadBusServo'): 参数错误
            - (False, 'E03 - Operation failed!', 'UnloadBusServo'): 操作失败
    
    使用示例：
        # 卸载所有舵机
        UnloadBusServo("servoPowerDown")
    
    注意事项：
        - 掉电后舵机会失去保持力，可能因重力下垂
        - 需要重新设置脉冲值才能恢复工作
        - 适用于长时间不使用的情况
    """
    ret = (True, (), 'UnloadBusServo')
    if args != 'servoPowerDown':
        return (False, __RPC_E01, 'UnloadBusServo')
    try:
        for i in range(1, 7):
            Board.unloadBusServo(i)
    except Exception as e:
        print(e)
        ret = (False, __RPC_E03, 'UnloadBusServo')

@dispatcher.add_method
def GetBusServosPulse(args):
    """
    获取所有总线舵机当前脉冲值
    
    功能描述：
        读取所有总线舵机（1-6）的当前脉冲值，用于查询舵机位置。
    
    参数说明：
        args (str): 必须为字符串 "angularReadback"
    
    返回值：
        tuple: (成功标志, 脉冲值列表, 方法名)
            - (True, [pulse1, pulse2, ..., pulse6], 'GetBusServosPulse'): 成功
                返回6个舵机的脉冲值列表（0-1000）
            - (False, 'E01 - Invalid number of parameter!', 'GetBusServosPulse'): 参数错误
            - (False, 'E04 - Operation timeout!', 'GetBusServosPulse'): 读取超时
            - (False, 'E03 - Operation failed!', 'GetBusServosPulse'): 操作失败
    
    使用示例：
        # 读取所有舵机位置
        result = GetBusServosPulse("angularReadback")
        if result[0]:
            pulses = result[1]
            for i, pulse in enumerate(pulses, 1):
                print(f"舵机{i}位置: {pulse}")
    
    注意事项：
        - 读取操作可能需要一定时间
        - 如果某个舵机读取失败，会返回E04超时错误
        - 需要确保总线舵机已正确连接
    """
    ret = (True, (), 'GetBusServosPulse')
    data = []
    if args != 'angularReadback':
        return (False, __RPC_E01, 'GetBusServosPulse')
    try:
        for i in range(1, 7):
            pulse = Board.getBusServoPulse(i)
            if pulse is None:
                ret = (False, __RPC_E04, 'GetBusServosPulse')
                return ret
            else:
                data.append(pulse)
        ret = (True, data, 'GetBusServosPulse')
    except Exception as e:
        print(e)
        ret = (False, __RPC_E03, 'GetBusServosPulse')
    return ret

@dispatcher.add_method
def SetBusServoID(oldid, newid):
    """
    设置总线舵机ID
    
    功能描述：
        修改总线舵机的ID号。用于配置多个舵机时区分不同舵机。
        出厂默认ID为1。
    
    参数说明：
        oldid (int): 原ID号
        newid (int): 新ID号，范围 1-253
    
    返回值：
        tuple: (成功标志, 空元组, 方法名)
            - (True, (), 'SetBusServoID'): 成功
            - (False, 'E03 - Operation failed!', 'SetBusServoID'): 操作失败
    
    使用示例：
        # 将ID为1的舵机改为ID 2
        SetBusServoID(1, 2)
    
    注意事项：
        - 修改ID后需要重新连接才能使用新ID
        - ID范围：1-253
        - 确保总线上只有一个舵机时才能修改ID
    """
    ret = (True, (), 'SetBusServoID')
    try:
        Board.setBusServoID(oldid, newid)
    except Exception as e:
        print('error:', e)
        ret = (False, __RPC_E03, 'SetBusServoID')
    return ret

# 总线舵机：读取舵机ID
@dispatcher.add_method
def GetBusServoID(id=None):
    """
    读取总线舵机ID
    
    功能描述：
        读取指定总线舵机的ID号，或读取总线上唯一舵机的ID。
    
    参数说明：
        id (int, optional): 舵机ID，如果为None则读取总线上唯一舵机
    
    返回值：
        tuple: (成功标志, ID值, 方法名)
    
    使用示例：
        # 读取总线上唯一舵机的ID
        result = GetBusServoID()
        
        # 读取指定ID的舵机
        result = GetBusServoID(1)
    
    注意事项：
        - 如果id为None，总线上只能有一个舵机
        - 读取可能需要多次尝试
    """
    ret = (True, 0, 'GetBusServoID')
    try:
        servo_id = Board.getBusServoID(id)
        ret = (True, servo_id, 'GetBusServoID')
    except Exception as e:
        print('error:', e)
        ret = (False, __RPC_E03, 'GetBusServoID')
    return ret

# 总线舵机：设置角度限制
@dispatcher.add_method
def SetBusServoAngleLimit(id, low, high):
    """
    设置总线舵机角度限制
    
    功能描述：
        设置总线舵机的转动角度范围，限制舵机只能在指定范围内转动。
        用于保护硬件，防止舵机转动超出安全范围。
    
    参数说明：
        id (int): 舵机ID，范围 1-6
        low (int): 最小角度，单位 0.24度
        high (int): 最大角度，单位 0.24度
    
    返回值：
        tuple: (成功标志, 空元组, 方法名)
    
    使用示例：
        # 限制舵机1在0-240度范围内（0-1000脉冲）
        SetBusServoAngleLimit(1, 0, 1000)
    
    注意事项：
        - 角度单位是0.24度，1000对应240度
        - 设置后舵机只能在此范围内转动
        - 用于保护硬件和防止碰撞
    """
    ret = (True, (), 'SetBusServoAngleLimit')
    try:
        Board.setBusServoAngleLimit(id, low, high)
    except Exception as e:
        print('error:', e)
        ret = (False, __RPC_E03, 'SetBusServoAngleLimit')
    return ret

# 总线舵机：读取角度限制
@dispatcher.add_method
def GetBusServoAngleLimit(id):
    """
    读取总线舵机角度限制
    
    功能描述：
        读取指定总线舵机的角度限制范围。
    
    参数说明：
        id (int): 舵机ID，范围 1-6
    
    返回值：
        tuple: (成功标志, (low, high), 方法名)
            返回元组 (最小角度, 最大角度)
    
    使用示例：
        # 读取舵机1的角度限制
        result = GetBusServoAngleLimit(1)
        if result[0]:
            low, high = result[1]
            print(f"角度范围: {low} - {high}")
    
    注意事项：
        - 角度单位是0.24度
        - 如果读取失败，可能需要多次尝试
    """
    ret = (True, (), 'GetBusServoAngleLimit')
    try:
        limits = Board.getBusServoAngleLimit(id)
        ret = (True, limits, 'GetBusServoAngleLimit')
    except Exception as e:
        print('error:', e)
        ret = (False, __RPC_E03, 'GetBusServoAngleLimit')
    return ret

# 总线舵机：设置电压限制
@dispatcher.add_method
def SetBusServoVinLimit(id, low, high):
    ret = (True, (), 'SetBusServoVinLimit')
    try:
        Board.setBusServoVinLimit(id, low, high)
    except Exception as e:
        print('error:', e)
        ret = (False, __RPC_E03, 'SetBusServoVinLimit')
    return ret

# 总线舵机：读取电压限制
@dispatcher.add_method
def GetBusServoVinLimit(id):
    ret = (True, (), 'GetBusServoVinLimit')
    try:
        limits = Board.getBusServoVinLimit(id)
        ret = (True, limits, 'GetBusServoVinLimit')
    except Exception as e:
        print('error:', e)
        ret = (False, __RPC_E03, 'GetBusServoVinLimit')
    return ret

# 总线舵机：设置最高温度
@dispatcher.add_method
def SetBusServoMaxTemp(id, temp):
    ret = (True, (), 'SetBusServoMaxTemp')
    try:
        Board.setBusServoMaxTemp(id, temp)
    except Exception as e:
        print('error:', e)
        ret = (False, __RPC_E03, 'SetBusServoMaxTemp')
    return ret

# 总线舵机：读取温度限制
@dispatcher.add_method
def GetBusServoTempLimit(id):
    ret = (True, 0, 'GetBusServoTempLimit')
    try:
        temp = Board.getBusServoTempLimit(id)
        ret = (True, temp, 'GetBusServoTempLimit')
    except Exception as e:
        print('error:', e)
        ret = (False, __RPC_E03, 'GetBusServoTempLimit')
    return ret

# 总线舵机：读取当前温度
@dispatcher.add_method
def GetBusServoTemp(id):
    ret = (True, 0, 'GetBusServoTemp')
    try:
        temp = Board.getBusServoTemp(id)
        ret = (True, temp, 'GetBusServoTemp')
    except Exception as e:
        print('error:', e)
        ret = (False, __RPC_E03, 'GetBusServoTemp')
    return ret

# 总线舵机：读取当前电压
@dispatcher.add_method
def GetBusServoVin(id):
    ret = (True, 0, 'GetBusServoVin')
    try:
        vin = Board.getBusServoVin(id)
        ret = (True, vin, 'GetBusServoVin')
    except Exception as e:
        print('error:', e)
        ret = (False, __RPC_E03, 'GetBusServoVin')
    return ret

# 总线舵机：停止单个舵机
@dispatcher.add_method
def StopBusServoSingle(id):
    ret = (True, (), 'StopBusServoSingle')
    try:
        Board.stopBusServo(id)
    except Exception as e:
        print('error:', e)
        ret = (False, __RPC_E03, 'StopBusServoSingle')
    return ret

# 总线舵机：读取负载状态
@dispatcher.add_method
def GetBusServoLoadStatus(id):
    ret = (True, 0, 'GetBusServoLoadStatus')
    try:
        status = Board.getBusServoLoadStatus(id)
        ret = (True, status, 'GetBusServoLoadStatus')
    except Exception as e:
        print('error:', e)
        ret = (False, __RPC_E03, 'GetBusServoLoadStatus')
    return ret

# 总线舵机：重置舵机位置
@dispatcher.add_method
def ResetBusServoPulse(id):
    ret = (True, (), 'ResetBusServoPulse')
    try:
        Board.restBusServoPulse(id)
    except Exception as e:
        print('error:', e)
        ret = (False, __RPC_E03, 'ResetBusServoPulse')
    return ret 

@dispatcher.add_method
def StopBusServo(args):
    """
    停止动作组执行
    
    功能描述：
        停止当前正在执行的动作组。
        用于中断正在运行的动作序列。
    
    参数说明：
        args (str): 必须为字符串 "stopAction"
    
    返回值：
        tuple: (成功标志, 空元组, 方法名)
            - (True, (), 'StopBusServo'): 成功
            - (False, 'E01 - Invalid number of parameter!', 'StopBusServo'): 参数错误
            - (False, 'E03 - Operation failed!', 'StopBusServo'): 操作失败
    
    使用示例：
        # 停止当前动作组
        StopBusServo("stopAction")
    
    注意事项：
        - 会立即停止当前动作组
        - 舵机可能停留在中间位置
        - 需要确保AGC（动作组控制）已初始化
    """
    ret = (True, (), 'StopBusServo')
    if args != 'stopAction':
        return (False, __RPC_E01, 'StopBusServo')
    try:     
        AGC.stop_action_group()
    except Exception as e:
        print(e)
        ret = (False, __RPC_E03, 'StopBusServo')

@dispatcher.add_method
def RunAction(args):
    """
    运行动作组
    
    功能描述：
        执行指定的动作组文件。动作组是预定义的舵机动作序列。
        动作在独立线程中执行，不会阻塞。
    
    参数说明：
        args (str 或 list): 动作组名称或动作组名称列表
            - 字符串: 单个动作组名称，如 "action1"
            - 列表: 多个动作组名称，如 ["action1", "action2"]
    
    返回值：
        tuple: (成功标志, 空元组, 方法名)
            - (True, (), 'RunAction'): 成功（动作已在后台启动）
            - (False, 'E01 - Invalid number of parameter!', 'RunAction'): 参数错误
            - (False, 'E03 - Operation failed!', 'RunAction'): 操作失败
    
    使用示例：
        # 运行单个动作组
        RunAction("wave_hand")
        
        # 运行多个动作组
        RunAction(["stand_up", "walk_forward"])
    
    注意事项：
        - 动作在后台线程执行，立即返回
        - 可以同时运行多个动作组
        - 动作组文件需要预先加载到系统中
        - 使用StopBusServo可以停止动作组
    """
    ret = (True, (), 'RunAction')
    if len(args) == 0:
        return (False, __RPC_E01, 'RunAction')
    try:
        threading.Thread(target=AGC.runAction, args=(args, )).start()
    except Exception as e:
        print(e)
        ret = (False, __RPC_E03, 'RunAction')
        
@dispatcher.add_method
def ArmMoveIk(*args):   
    """
    机械臂逆运动学控制
    
    功能描述：
        通过逆运动学算法控制机械臂末端执行器移动到指定位置和姿态。
        使用笛卡尔坐标和欧拉角定义目标位置。
    
    参数说明（7个参数）：
        x (float): 末端执行器X坐标，单位 cm
        y (float): 末端执行器Y坐标，单位 cm
        z (float): 末端执行器Z坐标，单位 cm
        pitch (float): 俯仰角，单位 度
        roll (float): 横滚角，单位 度
        yaw (float): 偏航角，单位 度
        speed (int): 移动速度，单位 ms，范围建议 500-3000
    
    返回值：
        tuple: (成功标志, 结果, 方法名)
            - (True, result, 'ArmMoveIk'): 成功，result包含运动结果信息
            - (False, 'E01 - Invalid number of parameter!', 'ArmMoveIk'): 参数数量错误
            - (False, 'E03 - Operation failed!', 'ArmMoveIk'): 操作失败
    
    使用示例：
        # 移动到位置(10, 5, 15)，姿态(0, -90, 90)，速度1500ms
        ArmMoveIk(10, 5, 15, 0, -90, 90, 1500)
        
        # 回到初始位置
        ArmMoveIk(0, 6, 18, 0, -90, 90, 1500)
    
    注意事项：
        - 坐标系统：X向右，Y向前，Z向上
        - 角度定义：pitch俯仰，roll横滚，yaw偏航
        - 目标位置必须在机械臂工作空间内
        - 如果目标位置不可达，会返回失败
    """
    global AK
    ret = (True, (), 'ArmMoveIk')
    if len(args) != 7:
        return (False, __RPC_E01, 'ArmMoveIk')
    if AK is None:
        return (False, __RPC_E03, 'ArmMoveIk')
    try:
        result = AK.setPitchRangeMoving((args[0], args[1], args[2]), args[3], args[4], args[5], args[6])
        if result == False:
            ret = (False, __RPC_E03, 'ArmMoveIk')
        else:
            ret = (True, result, 'ArmMoveIk')
    except Exception as e:
        print(f"ArmMoveIk error: {e}")
        ret = (False, __RPC_E03, 'ArmMoveIk')
    return ret
        
@dispatcher.add_method
def GetSonarDistance():
    global HWSONAR
    
    ret = (True, 0, 'GetSonarDistance')
    try:
        ret = (True, HWSONAR.getDistance(), 'GetSonarDistance')
    except:
        ret = (False, __RPC_E03, 'GetSonarDistance')
    return ret

@dispatcher.add_method
def GetBatteryVoltage():
    ret = (True, 0, 'GetBatteryVoltage')
    try:
        ret = (True, Board.getBattery(), 'GetBatteryVoltage')
    except Exception as e:
        print(e)
        ret = (False, __RPC_E03, 'GetBatteryVoltage')
    return ret

def runbymainth(req, pas):
    if callable(req):
        event = threading.Event()
        ret = [event, pas, None]
        QUEUE.put((req, ret))
        count = 0
        #ret[2] =  req(pas)
        #print('ret', ret)
        while ret[2] is None:
            time.sleep(0.01)
            count += 1
            if count > 200:
                break
        if ret[2] is not None:
            if ret[2][0]:
                return ret[2]
            else:
                return (False, __RPC_E03 + " " + ret[2][1])
        else:
            return (False, __RPC_E04)
    else:
        return (False, __RPC_E05)


@dispatcher.add_method
def LoadFunc(new_func = 0):
    """
    加载功能模块
    
    功能描述：
        加载指定的功能模块。功能模块包括各种视觉识别和自动控制功能。
        加载后需要调用StartFunc才能启动功能。
    
    参数说明：
        new_func (int): 功能ID
            - 0: 无功能
            - 1: 遥控功能（RemoteControl）
            - 2: 颜色检测（ColorDetect）
            - 3: 颜色分拣（ColorSorting）
            - 4: 颜色跟踪（ColorTracking）
            - 5: 视觉巡线（VisualPatrol）
            - 6: 智能避障（Avoidance）
            - 9: LAB颜色校准（lab_adjust）
    
    返回值：
        tuple: (成功标志, (功能ID,), 方法名)
            通过主线程队列执行，返回结果
    
    使用示例：
        # 加载颜色检测功能
        LoadFunc(2)
        
        # 加载避障功能
        LoadFunc(6)
    
    注意事项：
        - 加载功能会先停止当前运行的功能
        - 加载后需要调用StartFunc启动
        - 功能ID必须在有效范围内（1-9，除了7、8）
    """
    return runbymainth(Running.loadFunc, (new_func, ))

@dispatcher.add_method
def UnloadFunc():
    """
    卸载当前功能模块
    
    功能描述：
        卸载当前加载的功能模块，停止所有相关功能。
    
    参数说明：
        无参数
    
    返回值：
        tuple: (成功标志, (0,), 方法名)
            通过主线程队列执行，返回结果
    
    使用示例：
        # 卸载当前功能
        UnloadFunc()
    
    注意事项：
        - 会先停止当前功能，然后卸载
        - 卸载后功能ID变为0
    """
    return runbymainth(Running.unloadFunc, ())

@dispatcher.add_method
def StartFunc():
    """
    启动当前加载的功能模块
    
    功能描述：
        启动已加载的功能模块，开始执行功能逻辑。
    
    参数说明：
        无参数
    
    返回值：
        tuple: (成功标志, (功能ID,), 方法名)
            通过主线程队列执行，返回当前功能ID
    
    使用示例：
        # 先加载功能
        LoadFunc(2)  # 加载颜色检测
        # 然后启动
        StartFunc()
    
    注意事项：
        - 需要先加载功能（LoadFunc）才能启动
        - 如果未加载功能，启动会失败
    """
    return runbymainth(Running.startFunc, ())

@dispatcher.add_method
def StopFunc():
    """
    停止当前运行的功能模块
    
    功能描述：
        停止当前正在运行的功能模块，但保持加载状态。
        可以再次调用StartFunc重新启动。
    
    参数说明：
        无参数
    
    返回值：
        tuple: (成功标志, (功能ID,), 方法名)
            通过主线程队列执行，返回当前功能ID
    
    使用示例：
        # 停止当前功能
        StopFunc()
        # 稍后可以重新启动
        StartFunc()
    
    注意事项：
        - 停止后功能模块仍然加载，可以重新启动
        - 与UnloadFunc不同，UnloadFunc会完全卸载功能
    """
    return runbymainth(Running.stopFunc, ())

@dispatcher.add_method
def FinishFunc():
    return runbymainth(Running.finishFunc, ())

@dispatcher.add_method
def Heartbeat():
    return runbymainth(Running.doHeartbeat, ())

@dispatcher.add_method
def GetRunningFunc():
    """
    获取当前运行的功能ID
    
    功能描述：
        查询当前加载的功能模块ID。
    
    参数说明：
        无参数
    
    返回值：
        tuple: (成功标志, (功能ID,), 方法名)
            通过主线程队列执行，返回当前功能ID
            - 0: 无功能
            - 1-9: 对应功能ID
    
    使用示例：
        # 查询当前功能
        result = GetRunningFunc()
        if result[0]:
            func_id = result[1][0]
            print(f"当前功能ID: {func_id}")
    
    注意事项：
        - 返回的是已加载的功能ID，不一定是正在运行的
        - 要检查是否运行，需要结合StartFunc/StopFunc状态
    """
    return runbymainth(Running.getLoadedFunc, ())

@dispatcher.add_method
def ColorTracking(*target_color):
    return runbymainth(ColorTrack.setTargetColor, target_color)

@dispatcher.add_method
def ColorTrackingWheel(new_st = 0):
    print("Wheel",new_st)
    return runbymainth(ColorTrack.setWheel, new_st)

@dispatcher.add_method
def ColorSorting(*target_color):
    print(target_color)
    return runbymainth(ColorSort.setTargetColor, target_color)

@dispatcher.add_method
def VisualPatrol(*target_color):
    print(target_color)
    return runbymainth(VisualPat.setTargetColor, target_color)

@dispatcher.add_method
def ColorDetect(*target_color):
    """
    设置颜色检测目标颜色
    
    功能描述：
        设置颜色检测功能要检测的目标颜色。
        需要先加载颜色检测功能（LoadFunc(2)）并启动（StartFunc()）。
    
    参数说明：
        target_color (str, ...): 目标颜色，可以设置多个
            - "red": 红色
            - "green": 绿色
            - "blue": 蓝色
            - 可以同时设置多个颜色，如 ("red", "green", "blue")
    
    返回值：
        tuple: (成功标志, 结果, 方法名)
            通过主线程队列执行，返回结果
    
    使用示例：
        # 检测红色
        LoadFunc(2)
        StartFunc()
        ColorDetect("red")
        
        # 检测多种颜色
        ColorDetect("red", "green", "blue")
    
    注意事项：
        - 需要先加载并启动颜色检测功能
        - 颜色名称必须是小写英文
        - 可以同时检测多种颜色
    """
    print(target_color)
    return runbymainth(ColorDete.setTargetColor, target_color)

@dispatcher.add_method
def Avoidance(*target_color):
    print(target_color)
    return runbymainth(Avoidan.setTargetColor, target_color)


# 设置颜色阈值
# 参数：颜色lab
# 例如：[{'red': ((0, 0, 0), (255, 255, 255))}]
@dispatcher.add_method
def SetLABValue(*lab_value):
    #print(lab_value)
    return runbymainth(lab_adjust.setLABValue, lab_value)

# 保存颜色阈值
@dispatcher.add_method
def GetLABValue():
    return (True, lab_adjust.getLABValue()[1], 'GetLABValue')

# 保存颜色阈值
@dispatcher.add_method
def SaveLABValue(color=''):
    return runbymainth(lab_adjust.saveLABValue, (color, ))

@dispatcher.add_method
def HaveLABAdjust():
    return (True, True, 'HaveLABAdjust')

# ========== Gripper（夹持器）功能 ==========
# 夹持器通过PWM舵机1控制

@dispatcher.add_method
def SetGripperOpen():
    """
    打开夹持器
    
    功能描述：
        快速打开夹持器。夹持器通过PWM舵机1控制。
        打开位置对应脉冲值2000。
    
    参数说明：
        无参数
    
    返回值：
        tuple: (成功标志, 空元组, 方法名)
            - (True, (), 'SetGripperOpen'): 成功
            - (False, 'E03 - Operation failed!', 'SetGripperOpen'): 操作失败
    
    使用示例：
        # 打开夹持器
        SetGripperOpen()
    
    注意事项：
        - 夹持器使用PWM舵机1控制
        - 打开时间固定为500ms
        - 脉冲值：2000（完全打开）
    """
    ret = (True, (), 'SetGripperOpen')
    try:
        Board.setPWMServoPulse(1, 2000, 500)
    except Exception as e:
        print('error:', e)
        ret = (False, __RPC_E03, 'SetGripperOpen')
    return ret

@dispatcher.add_method
def SetGripperClose():
    """
    关闭夹持器
    
    功能描述：
        快速关闭夹持器。夹持器通过PWM舵机1控制。
        关闭位置对应脉冲值1500。
    
    参数说明：
        无参数
    
    返回值：
        tuple: (成功标志, 空元组, 方法名)
            - (True, (), 'SetGripperClose'): 成功
            - (False, 'E03 - Operation failed!', 'SetGripperClose'): 操作失败
    
    使用示例：
        # 关闭夹持器
        SetGripperClose()
    
    注意事项：
        - 夹持器使用PWM舵机1控制
        - 关闭时间固定为500ms
        - 脉冲值：1500（完全关闭）
    """
    ret = (True, (), 'SetGripperClose')
    try:
        Board.setPWMServoPulse(1, 1500, 500)
    except Exception as e:
        print('error:', e)
        ret = (False, __RPC_E03, 'SetGripperClose')
    return ret

@dispatcher.add_method
def SetGripperPosition(position, use_time=500):
    """
    设置夹持器位置（精确控制）
    
    功能描述：
        精确控制夹持器的开合程度，通过百分比设置位置。
        夹持器通过PWM舵机1控制。
    
    参数说明：
        position (int): 位置百分比，范围 0-100
            - 0: 完全关闭（脉冲值1500）
            - 50: 半开（脉冲值1750）
            - 100: 完全打开（脉冲值2000）
        use_time (int): 运行时间，单位毫秒，默认500
    
    返回值：
        tuple: (成功标志, 空元组, 方法名)
            - (True, (), 'SetGripperPosition'): 成功
            - (False, 'E03 - Operation failed!', 'SetGripperPosition'): 操作失败
    
    使用示例：
        # 设置为半开状态
        SetGripperPosition(50)
        
        # 设置为30%打开，用时1000ms
        SetGripperPosition(30, 1000)
    
    注意事项：
        - 位置值会被自动限制在0-100范围内
        - 脉冲值映射：1500 + (position/100) * 500
        - 夹持器使用PWM舵机1控制
    """
    ret = (True, (), 'SetGripperPosition')
    try:
        if position < 0:
            position = 0
        if position > 100:
            position = 100
        # 映射到脉冲值：1500（关闭）到 2000（打开）
        pulse = int(1500 + (position / 100.0) * 500)
        Board.setPWMServoPulse(1, pulse, use_time)
    except Exception as e:
        print('error:', e)
        ret = (False, __RPC_E03, 'SetGripperPosition')
    return ret

# 夹持器：获取当前位置
@dispatcher.add_method
def GetGripperPosition():
    ret = (True, 0, 'GetGripperPosition')
    try:
        pulse = Board.getPWMServoPulse(1)
        # 将脉冲值转换为位置百分比：1500=0%, 2000=100%
        if pulse < 1500:
            position = 0
        elif pulse > 2000:
            position = 100
        else:
            position = int(((pulse - 1500) / 500.0) * 100)
        ret = (True, position, 'GetGripperPosition')
    except Exception as e:
        print('error:', e)
        ret = (False, __RPC_E03, 'GetGripperPosition')
    return ret

# ========== 其他功能 ==========

@dispatcher.add_method
def SetBuzzer(state):
    """
    控制扩展板蜂鸣器
    
    功能描述：
        控制扩展板上的蜂鸣器开关。
    
    参数说明：
        state (bool): 蜂鸣器状态
            - True: 开启蜂鸣器
            - False: 关闭蜂鸣器
    
    返回值：
        tuple: (成功标志, 空元组, 方法名)
            - (True, (), 'SetBuzzer'): 成功
            - (False, 'E03 - Operation failed!', 'SetBuzzer'): 操作失败
    
    使用示例：
        # 开启蜂鸣器
        SetBuzzer(True)
        
        # 关闭蜂鸣器
        SetBuzzer(False)
    
    注意事项：
        - 蜂鸣器会持续响，直到设置为False
        - 建议短时间使用，避免噪音
    """
    ret = (True, (), 'SetBuzzer')
    try:
        Board.setBuzzer(1 if state else 0)
    except Exception as e:
        print('error:', e)
        ret = (False, __RPC_E03, 'SetBuzzer')
    return ret

# 扩展板RGB灯：设置颜色
@dispatcher.add_method
def SetBoardRGB(index, r, g, b):
    ret = (True, (), 'SetBoardRGB')
    try:
        if index < 0 or index > 1:
            return (False, __RPC_E02, 'SetBoardRGB')
        Board.RGB.setPixelColor(index, Board.PixelColor(r, g, b))
        Board.RGB.show()
    except Exception as e:
        print('error:', e)
        ret = (False, __RPC_E03, 'SetBoardRGB')
    return ret

# 扩展板RGB灯：关闭所有
@dispatcher.add_method
def SetBoardRGBOff():
    ret = (True, (), 'SetBoardRGBOff')
    try:
        for i in range(2):
            Board.RGB.setPixelColor(i, Board.PixelColor(0, 0, 0))
        Board.RGB.show()
    except Exception as e:
        print('error:', e)
        ret = (False, __RPC_E03, 'SetBoardRGBOff')
    return ret

@Request.application
def application(request):
    dispatcher["echo"] = lambda s: s
    dispatcher["add"] = lambda a, b: a + b
#     print(request.data)
    response = JSONRPCResponseManager.handle(request.data, dispatcher)

    return Response(response.json, mimetype='application/json')

def startRPCServer():
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.ERROR)
    run_simple('', 9030, application)

if __name__ == '__main__':
    startRPCServer()
