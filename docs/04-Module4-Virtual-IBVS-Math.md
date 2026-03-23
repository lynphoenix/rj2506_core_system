# 模块 4 核心算法：抗遮挡虚拟 IBVS 闭环控制推导与实现

[⬅️ 返回全局总纲](./01-RJ2506-Global-Architecture-And-Task-Breakdown.md)

**文档状态**: DRAFT (待推演验证)
**适用阶段**: Phase 1 (组件独立验证)
**目标读者**: 视觉伺服算法工程师、机器人运动学开发人员

## 1. 为什么需要“虚拟 IBVS” (Virtual IBVS)？

传统的 IBVS (Image-Based Visual Servoing) 是基于“当前看到的图像误差”来计算机械臂的速度指令。
*   **痛点**：在 RJ2506 的装配/堆垛场景中，当夹爪距离插槽 $< 5cm$ 时，夹持的物料或机械臂本体会**彻底遮挡手腕相机的视线**。传统的 IBVS 在这里会立刻发散崩溃，因为特征点丢失了。
*   **破局**：我们引入**“虚拟相机重投影 (Virtual Camera Re-projection)”**技术。当物理相机被遮挡时，直接**关闭图像输入流**。利用在 15cm 高空记忆的“绝对 3D 目标坐标”，结合机器人的高频正运动学 (Forward Kinematics)，在内存中“脑补”出一张虚拟照片，继续驱动 IBVS 控制律直到触底。

## 2. 数学链路推演 (从 3D 世界到 2D 雅可比)

我们需要解答两个最核心的数学问题：**虚拟像素怎么算出来的？** 和 **怎么把像素误差变成机械臂六个关节的速度？**

### 2.1 步骤一：高空 3D 目标记忆 (The 3D Memory)

在 $Z = 15cm$ 悬停点时，视线尚未被遮挡。
1.  手腕相机提取到装配卡槽的 $N$ 个像素特征点 $s_{img}^*$ (通常 $N \ge 4$)。
2.  利用深度相机（或双目测距、激光点）获取这些特征点在**当时相机坐标系**下的深度 $Z^*$。
3.  将其转换为相对机器人底座 (`base_link`) 的**绝对世界坐标** $P_{world}^*$：
    $$ P_{world}^* = T_{flange\_hover} \cdot T_{extrinsic} \cdot P_{camera\_hover}^* $$
    *这里的 $P_{world}^*$ 将被固化在内存中，它代表了卡槽在物理世界中绝对不动的真实位置。*

### 2.2 步骤二：盲插下降时的“脑补”重投影 (Virtual Re-projection)

当机械臂往下压，相机彻底瞎了。此时系统以 1000Hz 跑以下循环：
1.  **读底盘**：读取 CANopen 发来的最新电机角度，通过正运动学算出现在法兰的真实位姿 $T_{flange\_now}$。
2.  **算虚拟相机**：算出现在相机光心在世界里的位姿：$T_{cam\_now} = T_{flange\_now} \cdot T_{extrinsic}$
3.  **坐标系逆转**：把高空记忆的那个死死固定在桌子上的卡槽坐标 $P_{world}^*$，反向投射到“现在的相机”坐标系里：
    $$ P_{cam\_now}^* (X, Y, Z) = (T_{cam\_now})^{-1} \cdot P_{world}^* $$
4.  **针孔相机透视 (Pinhole)**：把这个 3D 点投影成**虚拟的 2D 像素点** $s_{virtual}$：
    $$ u = f_x rac{X}{Z} + c_x, \quad v = f_y rac{Y}{Z} + c_y $$
    *此时我们得到了一个“脑补出的当前画面” $s_{virtual}$。我们要让这个 $s_{virtual}$ 去追赶当年在 15cm 处拍的那张定妆照 $s_{target}^*$ (金标准)。*

### 2.3 步骤三：三维图像雅可比矩阵推导 (3D Image Jacobian)

这是最核心的控制律。我们要算出：当前虚拟像素在变动时，法兰该怎么动？
**公式**：$\dot{s} = J_{image}(s, Z) \cdot V_{camera}$

传统的 2D 雅可比只管平移，遇到俯仰 (Pitch) 倾斜直接发散。我们**必须带入深度 $Z$**（刚在 2.2 步第 3 小步里算出来的那个 $Z$），构建完整的 6x6 图像雅可比矩阵 $L_s$ (Interaction Matrix)：

$$
L_s =
egin{bmatrix}
-rac{\lambda}{Z} & 0 & rac{u}{Z} & rac{u v}{\lambda} & -rac{\lambda^2 + u^2}{\lambda} & v \\
0 & -rac{\lambda}{Z} & rac{v}{Z} & rac{\lambda^2 + v^2}{\lambda} & -rac{u v}{\lambda} & -u
\end{bmatrix}
$$

*   $(u, v)$ 是当前虚拟特征点相对于图像中心的坐标（减去主点 $c_x, c_y$）。
*   $Z$ 是当前推算出的深度。
*   $\lambda$ 是相机的焦距参数（通常为了简化设为 1，或者用真实焦距归一化坐标）。

因为我们有 4 个特征点（$N=4$），将 4 个点的 $2 	imes 6$ 矩阵垂直拼起来，得到一个 $8 	imes 6$ 的大矩阵 $J_{img}$。

### 2.4 步骤四：计算机械臂的速度指令

1.  **像素误差**：$e = s_{virtual} - s_{target}^*$  （脑补的像素位置 减去 目标金标准像素位置）
2.  **求伪逆 (Pseudo-Inverse)**：计算雅可比矩阵的广义逆 $J_{img}^+$。
3.  **计算相机速度**：$V_{cam} = - \lambda_{gain} \cdot J_{img}^+ \cdot e$  （$\lambda_{gain}$ 是比例增益，相当于 PID 里的 P 控制）。
4.  **速度映射到法兰 (Adjoint Transform)**：通过 $T_{extrinsic}$ 的伴随矩阵 (Adjoint Matrix)，把“要求相机跑的速度” $V_{cam}$，无损地转换成“要求法兰跑的速度” $V_{flange}$。
    $$ V_{flange} = egin{bmatrix} R_{extrinsic} & 0 \ [t_{extrinsic}]_	imes R_{extrinsic} & R_{extrinsic} \end{bmatrix} V_{cam} $$
5.  **下发硬件**：把 $V_{flange}$（六个维度的空间速度 $v_x, v_y, v_z, \omega_x, \omega_y, \omega_z$）通过运动学逆解（Inverse Kinematics）或者雅可比伪逆算成 6 个电机的角速度，扔给 C++ Daemon 下发。

---

## 3. Python 核心控制律代码 (ROS 2 Node 片段)

我们将这套纯数学的矩阵推演，浓缩成了下面这段能在 ROS 2 里极速跑（耗时 < 1ms）的核心算法节点。这段代码可以直接嵌入到你的 IBVS Action Server 里。

```python
import numpy as np
from scipy.spatial.transform import Rotation as R

class VirtualIBVSController:
    def __init__(self, K, T_extrinsic):
        self.K = K                      # 3x3 相机内参矩阵
        self.T_extrinsic = T_extrinsic  # 4x4 相机外参 (Eye-in-Hand: 法兰 -> 相机)
        self.P_world_targets = []       # [N, 4] 记忆的绝对物理 3D 坐标 (齐次)
        self.s_golden_pixels = []       # [N, 2] 任务六拍出来的金标准定妆照的 2D 像素坐标
        self.lambda_gain = 0.5          # IBVS 比例增益 (控制下降的平滑度)

    def compute_interaction_matrix(self, u, v, Z):
        """
        构造单个点的 2x6 三维图像雅可比矩阵 (Interaction Matrix L_s)
        彻底解决 Pitch/Roll 倾斜发散问题，利用推算出的深度 Z 提供物理约束。
        """
        L = np.zeros((2, 6))
        # 将像素坐标 (u,v) 转换为归一化图像平面坐标 (x,y)
        fx, fy = self.K[0,0], self.K[1,1]
        cx, cy = self.K[0,2], self.K[1,2]
        x = (u - cx) / fx
        y = (v - cy) / fy

        # 填入那段长长的推导公式
        L[0,0] = -1.0 / Z
        L[0,1] = 0.0
        L[0,2] = x / Z
        L[0,3] = x * y
        L[0,4] = -(1.0 + x**2)
        L[0,5] = y

        L[1,0] = 0.0
        L[1,1] = -1.0 / Z
        L[1,2] = y / Z
        L[1,3] = 1.0 + y**2
        L[1,4] = -x * y
        L[1,5] = -x

        return L
        
    def adjoint_transform(self, V_cam):
        """
        计算伴随矩阵，将相机的期望速度映射到法兰坐标系下
        V_cam: [vx, vy, vz, wx, wy, wz]
        """
        R_ext = self.T_extrinsic[:3, :3]
        t_ext = self.T_extrinsic[:3, 3]
        
        # 叉乘的反对称矩阵
        t_skew = np.array([
            [0, -t_ext[2], t_ext[1]],
            [t_ext[2], 0, -t_ext[0]],
            [-t_ext[1], t_ext[0], 0]
        ])
        
        # 构造 6x6 伴随矩阵
        Adj = np.zeros((6, 6))
        Adj[:3, :3] = R_ext
        Adj[3:, 3:] = R_ext
        Adj[:3, 3:] = t_skew @ R_ext
        
        # 转换速度
        V_flange = Adj @ V_cam
        return V_flange

    def calculate_velocity_command(self, T_flange_now):
        """
        核心控制循环：以 1000Hz 频率被调用。
        输入：当前法兰的物理正运动学位姿
        输出：法兰 6DoF 期望速度指令 [vx, vy, vz, wx, wy, wz]
        """
        if not self.P_world_targets or not self.s_golden_pixels:
            return np.zeros(6)

        # 1. 脑补当前相机在物理世界中的位姿
        T_cam_now = T_flange_now @ self.T_extrinsic
        T_cam_inv = np.linalg.inv(T_cam_now)

        J_img_full = []
        error_full = []

        # 2. 遍历所有高空记忆的特征点 (N个点)
        for i in range(len(self.P_world_targets)):
            # 脑补重投影：把死死固定在桌子上的物理坐标 P_world，拉回"现在的相机"视角里
            P_cam = T_cam_inv @ self.P_world_targets[i]
            Xc, Yc, Zc = P_cam[0], P_cam[1], P_cam[2]
            
            # 防御性编程：如果推算的深度小于 0，说明机械臂撞过头了，相机跑到了物体下面
            if Zc <= 0.001:
                print("危险！推算深度穿模，强制刹车！")
                return np.zeros(6)

            # 针孔透视变成"脑补"的虚拟像素 (u_virtual, v_virtual)
            u_v = (self.K[0,0] * Xc / Zc) + self.K[0,2]
            v_v = (self.K[1,1] * Yc / Zc) + self.K[1,2]

            # 计算图像雅可比矩阵
            L_s = self.compute_interaction_matrix(u_v, v_v, Zc)
            # 因为我们的控制律是用像素误差算，所以要把归一化的雅可比乘以焦距放缩回来
            L_s[0, :] *= self.K[0,0]
            L_s[1, :] *= self.K[1,1]
            
            J_img_full.append(L_s)

            # 计算像素误差 (虚拟的现在像素 - 金标准定妆照像素)
            u_err = u_v - self.s_golden_pixels[i][0]
            v_err = v_v - self.s_golden_pixels[i][1]
            error_full.extend([u_err, v_err])

        # 3. 拼装大矩阵 (8x6) 并求解伪逆 (Pseudo-Inverse, 6x8)
        J_img_full = np.vstack(J_img_full)
        error_full = np.array(error_full).reshape(-1, 1)

        J_pinv = np.linalg.pinv(J_img_full)

        # 4. 计算要求相机的空间速度 V_camera = -lambda * J^+ * e
        V_camera = -self.lambda_gain * (J_pinv @ error_full)

        # 5. 坐标系扭转：把要求相机跑的速度 V_cam，通过伴随矩阵折算成法兰应该怎么动
        V_flange = self.adjoint_transform(V_camera.flatten())

        return V_flange
```

---

## 4. 结论与下一步

这套方案**彻底抛弃了物理相机的束缚**。只要 $T_{extrinsic}$ (任务三算出来的外参) 足够精准，且正运动学读取的 $T_{flange\_now}$ 足够快，它就能在画面被物体完全挡死的情况下，依靠纯数学信仰（雅可比投影）将夹爪强行掰正到金标准的位置。由于雅可比中带入了实时推算的真实深度 $Z$，它天生免疫俯仰和滚转（Pitch/Roll）带来的视角畸变，不会出现传统 2D IBVS 在靠近时突然乱扭的问题。

这就是 RJ2506 能在黑暗中实现毫米级装配的最后一道杀手锏。在进入 C++ Daemon 开发之前，算法组应当在 Python 仿真环境中注入人为噪声，测试这套代码中伪逆矩阵的条件数 (Condition Number) 稳定性。
