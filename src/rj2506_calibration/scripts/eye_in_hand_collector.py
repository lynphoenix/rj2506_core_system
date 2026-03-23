#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
手眼标定一键数据采集与解算工具 (Eye-in-Hand Calibrator)
用于 RJ2506 Phase 0.2: 采集手腕相机图像与法兰位姿对应关系，并一键解算标定矩阵。

操作说明：
- 按 'c' 键：捕获当前图像和法兰 TF 位姿。
- 按 's' 键：结束采集，调用 OpenCV calibrateHandEye 解算，并将结果保存至指定目录。
- 按 'q' 键：放弃退出。

执行要求：
- 需要相机提供相机内参 (默认订阅 /wrist_camera/camera_info)
- 需要标定板为 ChArUco Board。
"""

import os
import cv2
import yaml
import time
import numpy as np
import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Image, CameraInfo
from cv_bridge import CvBridge
import tf2_ros
from geometry_msgs.msg import TransformStamped
from scipy.spatial.transform import Rotation as R

class EyeInHandCalibrator(Node):
    def __init__(self):
        super().__init__('eye_in_hand_calibrator')

        # === 1. 配置参数 ===
        self.camera_topic = self.declare_parameter('camera_topic', '/wrist_camera/image_raw').value
        self.info_topic = self.declare_parameter('info_topic', '/wrist_camera/camera_info').value
        self.base_frame = self.declare_parameter('base_frame', 'base_link').value
        self.flange_frame = self.declare_parameter('flange_frame', 'flange_link').value

        # 默认保存到系统级目录，需确保运行用户有权限，或改为用户目录
        self.output_yaml = self.declare_parameter('output_yaml', '/etc/rj2506/extrinsics/rj2506_extrinsics.yaml').value
        self.debug_dir = self.declare_parameter('debug_dir', os.path.expanduser('~/.rj2506/calibration_debug')).value

        # ChArUco 标定板物理参数 (根据实际打印尺寸修改)
        self.squares_x = self.declare_parameter('squares_x', 5).value
        self.squares_y = self.declare_parameter('squares_y', 7).value
        self.square_length = self.declare_parameter('square_length', 0.03).value # 30mm
        self.marker_length = self.declare_parameter('marker_length', 0.022).value # 22mm

        # === 2. 初始化 ChArUco 字典与板 ===
        self.dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_6X6_250)
        self.board = cv2.aruco.CharucoBoard(
            (self.squares_x, self.squares_y),
            self.square_length,
            self.marker_length,
            self.dictionary
        )
        # 较新版本的 OpenCV 需要明确构建检测器
        self.params = cv2.aruco.DetectorParameters()
        self.detector = cv2.aruco.ArucoDetector(self.dictionary, self.params)

        # 创建输出目录
        os.makedirs(os.path.dirname(self.output_yaml), exist_ok=True)
        os.makedirs(self.debug_dir, exist_ok=True)

        # === 3. 初始化 ROS 2 组件 ===
        self.bridge = CvBridge()
        self.tf_buffer = tf2_ros.Buffer()
        self.tf_listener = tf2_ros.TransformListener(self.tf_buffer, self)

        # 订阅相机内参
        self.camera_matrix = None
        self.dist_coeffs = None
        self.info_sub = self.create_subscription(CameraInfo, self.info_topic, self.info_callback, 1)

        # 订阅图像
        self.latest_image = None
        self.image_sub = self.create_subscription(Image, self.camera_topic, self.image_callback, 10)

        # === 4. 存储采集的数据 ===
        self.capture_count = 0

        # 机械臂法兰位姿 (世界坐标系下) ->  World to Gripper (Base to Flange)
        self.R_base2gripper = []
        self.t_base2gripper = []

        # 相机看到的标定板位姿 (相机坐标系下) -> Camera to Target
        self.R_cam2target = []
        self.t_cam2target = []

        self.get_logger().info("=== RJ2506 手眼标定一键解算工具已启动 ===")
        self.get_logger().info("请在弹出的窗口中操作：按 'C' 捕获，按 'S' 解算并保存，按 'Q' 退出。")

    def info_callback(self, msg):
        if self.camera_matrix is None:
            self.camera_matrix = np.array(msg.k).reshape((3, 3))
            self.dist_coeffs = np.array(msg.d)
            self.get_logger().info("✅ 成功获取相机内参矩阵。")

    def image_callback(self, msg):
        try:
            self.latest_image = self.bridge.imgmsg_to_cv2(msg, "bgr8")
        except Exception as e:
            self.get_logger().error(f"图像转换失败: {e}")

    def capture_point(self):
        if self.latest_image is None:
            self.get_logger().warning("未收到图像！")
            return False

        if self.camera_matrix is None:
            self.get_logger().warning("未收到相机内参 /camera_info，无法检测 3D 位姿！")
            return False

        img = self.latest_image.copy()
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

        # 1. 尝试检测 ChArUco 标定板
        marker_corners, marker_ids, rejected = self.detector.detectMarkers(gray)

        if marker_ids is None or len(marker_ids) == 0:
            self.get_logger().warning("画面中未检测到 ArUco 标记，请调整相机视角！")
            return False

        # 提取内部角点
        ret, charuco_corners, charuco_ids = cv2.aruco.interpolateCornersCharuco(
            marker_corners, marker_ids, gray, self.board
        )

        if not ret or charuco_ids is None or len(charuco_ids) < 4:
            self.get_logger().warning("检测到的棋盘格角点太少（<4），无法进行 3D 姿态估计！")
            return False

        # 估计标定板在相机坐标系下的 3D 位姿 (Camera to Target)
        success, rvec_cam2target, tvec_cam2target = cv2.aruco.estimatePoseCharucoBoard(
            charuco_corners, charuco_ids, self.board, self.camera_matrix, self.dist_coeffs, np.empty(1), np.empty(1)
        )

        if not success:
            self.get_logger().warning("OpenCV estimatePoseCharucoBoard 解算失败！")
            return False

        # 2. 查询同一时刻机器人的 TF (Base to Flange)
        try:
            now = rclpy.time.Time()
            trans: TransformStamped = self.tf_buffer.lookup_transform(
                self.base_frame,
                self.flange_frame,
                now,
                timeout=rclpy.duration.Duration(seconds=1.0)
            )

            # === 将 TF 转换为旋转矩阵和平移向量 ===
            tx = trans.transform.translation.x
            ty = trans.transform.translation.y
            tz = trans.transform.translation.z

            rx = trans.transform.rotation.x
            ry = trans.transform.rotation.y
            rz = trans.transform.rotation.z
            rw = trans.transform.rotation.w

            r_base2flange = R.from_quat([rx, ry, rz, rw]).as_matrix()
            t_base2flange = np.array([tx, ty, tz]).reshape(3, 1)

            # === 保存配对数据供后续手眼标定 ===
            # 将旋转向量 rvec 转换为旋转矩阵
            R_cam2target_mat, _ = cv2.Rodrigues(rvec_cam2target)

            self.R_cam2target.append(R_cam2target_mat)
            self.t_cam2target.append(tvec_cam2target)

            self.R_base2gripper.append(r_base2flange)
            self.t_base2gripper.append(t_base2flange)

            self.capture_count += 1

            # 画上坐标轴并保存 debug 图像
            cv2.drawFrameAxes(img, self.camera_matrix, self.dist_coeffs, rvec_cam2target, tvec_cam2target, 0.1)
            cv2.imwrite(os.path.join(self.debug_dir, f"calib_{self.capture_count:03d}.jpg"), img)

            self.get_logger().info(f"✅ 成功捕获第 {self.capture_count} 个对齐姿态！")
            return True

        except tf2_ros.LookupException as e:
            self.get_logger().error(f"捕获失败: 找不到 TF {self.base_frame}->{self.flange_frame}")
            return False

    def solve_and_save(self):
        if self.capture_count < 10:
            self.get_logger().error(f"点数太少 ({self.capture_count})！算法需要至少 10 个以上差异显著的姿态才能收敛，请按 'C' 继续采集！")
            return False

        self.get_logger().info(f"开始使用 {self.capture_count} 个姿态矩阵执行 cv2.calibrateHandEye (Eye-in-Hand模式)...")

        try:
            # 机械臂使用的是 Eye-in-Hand (眼在手上) 模型：
            # R_gripper2base, t_gripper2base 是已知的机械臂末端相对于基座的姿态 (就是 TF)
            # R_target2cam, t_target2cam 是已知的标定板相对于相机的姿态 (就是 aruco.estimatePose)
            # 求解目标：X = R_cam2gripper, t_cam2gripper (相机光心相对于法兰坐标系的固定外参)

            R_cam2gripper, t_cam2gripper = cv2.calibrateHandEye(
                R_gripper2base=self.R_base2gripper,
                t_gripper2base=self.t_base2gripper,
                R_target2cam=self.R_cam2target,
                t_target2cam=self.t_cam2target,
                method=cv2.CALIB_HAND_EYE_TSAI # Tsai-Lenz 算法
            )

            # 组装 4x4 齐次变换矩阵
            T_extrinsic = np.eye(4)
            T_extrinsic[:3, :3] = R_cam2gripper
            T_extrinsic[:3, 3] = t_cam2gripper.flatten()

            self.get_logger().info(f"解算成功！相机外参矩阵 (Flange -> Camera):
{T_extrinsic}")

            # 保存为 YAML 供 C++ Daemon / Python 算法读取
            data_to_save = {
                'extrinsic_matrix': {
                    'rows': 4,
                    'cols': 4,
                    'data': T_extrinsic.flatten().tolist()
                },
                'translation': t_cam2gripper.flatten().tolist(),
                'rotation_matrix': R_cam2gripper.flatten().tolist(),
                'points_used': self.capture_count,
                'calibration_method': 'Tsai-Lenz',
                'frame_id': self.flange_frame,
                'child_frame_id': 'camera_color_optical_frame'
            }

            with open(self.output_yaml, 'w') as f:
                yaml.dump(data_to_save, f, default_flow_style=False)

            self.get_logger().info(f"🎉 验证通过！外参文件已保存至 {self.output_yaml}")
            return True

        except Exception as e:
            self.get_logger().error(f"解算或保存失败: {e}")
            return False

def main(args=None):
    rclpy.init(args=args)
    node = EyeInHandCalibrator()

    cv2.namedWindow("RJ2506 Eye-in-Hand Calibration", cv2.WINDOW_NORMAL)

    try:
        while rclpy.ok():
            rclpy.spin_once(node, timeout_sec=0.01)

            if node.latest_image is not None:
                display_img = node.latest_image.copy()

                # 画出简易的检测框以便肉眼确认对焦情况
                gray = cv2.cvtColor(display_img, cv2.COLOR_BGR2GRAY)
                corners, ids, _ = node.detector.detectMarkers(gray)
                if ids is not None:
                    cv2.aruco.drawDetectedMarkers(display_img, corners, ids)

                cv2.putText(display_img, f"Captured: {node.capture_count}/30", (20, 40),
                            cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
                cv2.putText(display_img, "'C': Capture | 'S': Solve & Save | 'Q': Quit", (20, 80),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)

                cv2.imshow("RJ2506 Eye-in-Hand Calibration", display_img)

            key = cv2.waitKey(10) & 0xFF
            if key == ord('c') or key == ord('C'):
                node.capture_point()
            elif key == ord('s') or key == ord('S'):
                if node.solve_and_save():
                    break
            elif key == ord('q') or key == ord('Q'):
                node.get_logger().info("放弃采集，退出。")
                break

    except KeyboardInterrupt:
        node.get_logger().info("用户中断。")
    finally:
        cv2.destroyAllWindows()
        node.destroy_node()
        rclpy.shutdown()

if __name__ == '__main__':
    main()
