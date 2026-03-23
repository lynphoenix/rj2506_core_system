#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
物理探针 (TCP) 四点法标定工具 (TCP Calibrator)
用于 RJ2506 Phase 0.2: 标定金属探针相对于法兰的 XYZ 偏移量 (T_tcp_offset)。

算法原理：
使用最小二乘法拟合一个空间球心。
假设探针尖端固定在世界坐标系的某一点 P_tip，法兰中心的坐标为 P_flange_i，旋转矩阵为 R_i。
探针相对于法兰的偏置向量为 t_offset。
则对于每一个采集的姿态 i，都有：
P_flange_i + R_i * t_offset = P_tip

我们需要找到一个最优的 t_offset 和 P_tip，使得所有 4 个姿态下上述方程的误差最小。

操作说明：
- 按 'c' 键：捕获当前法兰的 TF 位姿。
- 至少捕获 4 个点后，按 's' 键：执行最小二乘法解算 TCP offset，并保存为 YAML 配置文件。
- 按 'q' 键：放弃退出。
"""

import os
import sys
import yaml
import numpy as np
import rclpy
from rclpy.node import Node
import tf2_ros
from geometry_msgs.msg import TransformStamped
from scipy.spatial.transform import Rotation as R
import termios
import tty
import select

class TCPCalibrator(Node):
    def __init__(self):
        super().__init__('tcp_calibrator')

        # === 1. 配置参数 ===
        self.base_frame = self.declare_parameter('base_frame', 'base_link').value
        self.flange_frame = self.declare_parameter('flange_frame', 'flange_link').value
        self.output_yaml = self.declare_parameter('output_yaml', '/etc/rj2506/kinematics/tcp_offset.yaml').value

        os.makedirs(os.path.dirname(self.output_yaml), exist_ok=True)

        # === 2. 初始化 ROS 2 组件 ===
        self.tf_buffer = tf2_ros.Buffer()
        self.tf_listener = tf2_ros.TransformListener(self.tf_buffer, self)

        # === 3. 存储采集的数据 ===
        self.capture_count = 0
        self.R_flanges = []  # 保存所有姿态下的法兰旋转矩阵
        self.p_flanges = []  # 保存所有姿态下的法兰平移向量

        self.get_logger().info("
=== RJ2506 TCP 四点法标定工具已启动 ===")
        self.get_logger().info(f"TF 监听关系: {self.base_frame} -> {self.flange_frame}")
        self.get_logger().info("操作说明：")
        self.get_logger().info("  请将机械臂拖拽至固定靶点，保证探针尖端完美对准。")
        self.get_logger().info("  [C] - 捕获当前法兰位姿 (需更换姿态重复 4 次)")
        self.get_logger().info("  [S] - 开始计算 TCP Offset (至少 4 个点)")
        self.get_logger().info("  [Q] - 退出")

    def capture_point(self):
        try:
            now = rclpy.time.Time()
            trans: TransformStamped = self.tf_buffer.lookup_transform(
                self.base_frame,
                self.flange_frame,
                now,
                timeout=rclpy.duration.Duration(seconds=1.0)
            )

            tx = trans.transform.translation.x
            ty = trans.transform.translation.y
            tz = trans.transform.translation.z

            rx = trans.transform.rotation.x
            ry = trans.transform.rotation.y
            rz = trans.transform.rotation.z
            rw = trans.transform.rotation.w

            r_matrix = R.from_quat([rx, ry, rz, rw]).as_matrix()
            p_vector = np.array([tx, ty, tz]).reshape(3, 1)

            self.R_flanges.append(r_matrix)
            self.p_flanges.append(p_vector)

            self.capture_count += 1
            self.get_logger().info(f"✅ 成功捕获第 {self.capture_count} 个姿态。位置: X={tx:.4f}, Y={ty:.4f}, Z={tz:.4f}")

        except tf2_ros.LookupException as e:
            self.get_logger().error(f"捕获失败: 找不到 TF {self.base_frame}->{self.flange_frame}")

    def solve_tcp(self):
        if self.capture_count < 4:
            self.get_logger().error(f"点数不足！目前只有 {self.capture_count} 个点。四点法标定至少需要 4 个不同姿态。")
            return False

        self.get_logger().info(f"开始使用 {self.capture_count} 个姿态执行最小二乘法计算...")

        """
        数学推导：
        对于 i = 1..N:
        P_flange_i + R_i * t_offset = P_tip
        R_i * t_offset - P_tip = -P_flange_i

        构建超定方程组 A * X = B
        其中未知数 X = [t_offset_x, t_offset_y, t_offset_z, P_tip_x, P_tip_y, P_tip_z]^T  (6x1 向量)

        对于第 i 个方程：
        [ R_i  |  -I ] * X = -P_flange_i
        (3x3)    (3x3)
        """

        N = self.capture_count
        A = np.zeros((3 * N, 6))
        B = np.zeros((3 * N, 1))

        I3 = np.eye(3)

        for i in range(N):
            A[3*i : 3*i+3, 0:3] = self.R_flanges[i]
            A[3*i : 3*i+3, 3:6] = -I3
            B[3*i : 3*i+3, 0] = -self.p_flanges[i].flatten()

        # 最小二乘求解 A * X = B
        X, residuals, rank, s = np.linalg.lstsq(A, B, rcond=None)

        t_offset = X[0:3].flatten()
        p_tip = X[3:6].flatten()

        # 计算 RMSE 误差 (评估你手抖不抖，戳得准不准)
        errors = []
        for i in range(N):
            predicted_tip = self.p_flanges[i].flatten() + self.R_flanges[i] @ t_offset
            err_dist = np.linalg.norm(predicted_tip - p_tip)
            errors.append(err_dist)

        rmse = np.sqrt(np.mean(np.array(errors)**2)) * 1000.0  # 转为 mm

        self.get_logger().info(f"--- 标定结果 ---")
        self.get_logger().info(f"TCP 偏移量 (相对于法兰, 米): X={t_offset[0]:.5f}, Y={t_offset[1]:.5f}, Z={t_offset[2]:.5f}")
        self.get_logger().info(f"世界坐标系下的靶点位置 (米): X={p_tip[0]:.5f}, Y={p_tip[1]:.5f}, Z={p_tip[2]:.5f}")
        self.get_logger().info(f"拟合均方根误差 (RMSE): {rmse:.2f} mm")

        if rmse > 0.5:
            self.get_logger().error(f"🚨 警告！RMSE 误差 ({rmse:.2f} mm) 大于 0.5 mm。说明你在移动机械臂时针尖滑跑了，或者姿态差异太小。强烈建议按 'q' 退出后重新采集！")
            # 允许保存，但给出强烈警告
        else:
            self.get_logger().info("🎉 误差达标！标定精度极高。")

        # 保存为 YAML 配置文件
        data_to_save = {
            'tcp_offset': {
                'x': float(t_offset[0]),
                'y': float(t_offset[1]),
                'z': float(t_offset[2])
            },
            'rmse_error_mm': float(rmse),
            'points_used': self.capture_count,
            'frame_id': self.flange_frame,
            'child_frame_id': 'tcp_probe_link'
        }

        try:
            with open(self.output_yaml, 'w') as f:
                yaml.dump(data_to_save, f, default_flow_style=False)
            self.get_logger().info(f"TCP 参数已写入: {self.output_yaml}")
            return True
        except Exception as e:
            self.get_logger().error(f"写入 YAML 失败: {e}")
            return False

def get_key():
    """非阻塞获取终端单字符输入"""
    tty.setraw(sys.stdin.fileno())
    rlist, _, _ = select.select([sys.stdin], [], [], 0.1)
    if rlist:
        key = sys.stdin.read(1)
    else:
        key = ''
    termios.tcsetattr(sys.stdin, termios.TCSADRAIN, settings)
    return key

settings = termios.tcgetattr(sys.stdin)

def main(args=None):
    rclpy.init(args=args)
    node = TCPCalibrator()

    try:
        while rclpy.ok():
            rclpy.spin_once(node, timeout_sec=0.01)
            key = get_key()

            if key:
                key = key.lower()
                if key == 'c':
                    node.capture_point()
                elif key == 's':
                    if node.solve_tcp():
                        break
                elif key == 'q':
                    node.get_logger().info("退出程序。")
                    break

    except KeyboardInterrupt:
        node.get_logger().info("用户中断。")
    finally:
        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, settings)
        node.destroy_node()
        rclpy.shutdown()

if __name__ == '__main__':
    main()
