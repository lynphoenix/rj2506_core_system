# 模块 3 核心算法：高空 3D 特征提取与记忆 (Feature Memory)

[⬅️ 返回全局总纲](./01-RJ2506-Global-Architecture-And-Task-Breakdown.md)

**文档状态**: DRAFT (待实施验证)
**适用阶段**: Phase 1 (组件独立验证)
**目标读者**: 视觉算法工程师、ACT 模型部署人员

## 1. 模块定位与核心痛点

本模块是 RJ2506 系统从“宏观端到端 (ACT/DP)” 向 “微观盲插闭环 (Virtual IBVS)” 切换的**关键接力棒**。

*   **上游输入**：ACT 策略模型将机械臂引导至距离插槽/目标上方 $\approx 15cm$ 处（允许 $\pm 3cm$ 的大尺度宏观误差），然后交出控制权。
*   **本模块任务**：在手腕相机尚未被完全遮挡的这“最后一眼”中，从复杂光照的图像里提取出 $N \ge 4$ 个极其稳定的像素特征点，并结合 OAK-D 双目的深度图，计算出这些点在物理世界中绝对不动的 3D 坐标 $P_{world}^*$。
*   **下游输出**：将这 $N$ 个绝对 3D 坐标注入内存（The 3D Memory），彻底关闭相机流，唤醒 [模块 4: 虚拟 IBVS 算法](./04-Module4-Virtual-IBVS-Math.md) 进行 1000Hz 的高频电机位姿重投影盲插。

**核心痛点（The Nightmare of Specular Reflection）**：
车间金属料片和卡槽存在严重的**镜面高光反光 (Specular Reflection)**。
1.  反光会导致表面失去纹理，传统的 SIFT/ORB 算子提取出的特征点会随着视角的微小晃动而发生剧烈的“像素滑动”。
2.  反光会导致 OAK-D 的红外散斑打滑，深度图 (Depth Map) 往往在反光中心呈现大面积的零值空洞 (Black Holes)。
如果提取的这 4 个初始点在 3D 空间里是“漂移”的，下游的 IBVS 盲插会直接把夹爪怼碎。

---

## 2. 算法选型与双轨架构 (Dual-Track Architecture)

为了在 15cm 处保证 100% 的提取成功率，我们采用**“传统几何主导，深度学习兜底”**的双轨并发策略。

### 2.1 主力轨道：纯几何轮廓与外角点提取 (Track 1: Geometric Corners)
*优先使用，耗时 < 5ms，CPU 执行，完全免疫表面纹理高光滑动*

**设计思路**：我们不找料片表面斑驳的反光点，我们直接提取料片或卡槽那“雷打不动”的 4 条刚性物理边界线，求交点作为特征点。
**执行流程**：
1.  **高斯模糊与 Canny 边缘**：对当前 $15cm$ 处拍下的 RGB 图像进行极强的去噪提取。
2.  **形态学闭运算 (Morphological Closing)**：连通断裂的金属边缘。
3.  **轮廓多边形拟合 (`cv2.approxPolyDP`)**：寻找视野中心面积最大的四边形轮廓。
4.  **求取 4 个虚拟角点 $s_{target}^*$**：这 4 个角点即使在照片上模糊不清，也是由 4 条长线段强行相交算出来的，像素级稳定。
5.  **空洞插值深度读取**：在深度图对应这 4 个角点周围的 $5 	imes 5$ 邻域内，剔除 $Z=0$ 的空洞值和离群噪点，取有效深度的**中位数 (Median)** 作为该角点的真实深度 $Z^*$。

### 2.2 备用轨道：轻量级特征匹配网络 (Track 2: Deep Learning Features)
*作为 Track 1 提取失败时的兜底方案，耗时 30-50ms，在交接瞬间独占 Orin GPU 推理*

正如架构评审会议所确定的，虽然深度学习耗时较长，但在 $15cm$ 高空“悬停接力”的这一瞬间，上游的 ACT/DP 模型已经释放了计算资源，下游的 1000Hz 纯运动学 IBVS 循环还未开启。此时 **Orin 的 GPU 是完全空闲的**，我们完全可以塞入一个轻量级网络。

**设计思路**：当几何轮廓因背景杂乱无法提取（提取出的多边形不是 4 个角）时，触发深度学习匹配。
**推荐网络**：**SuperPoint** (提取) + **LightGlue / SuperGlue** (匹配)。
*   相比于传统的 LoFTR（密集匹配太慢），SuperPoint 提取出的稀疏点更能抵抗光照变化，且其输出的置信度 (Confidence Map) 可以过滤掉反光产生的伪角点。
**执行流程**：
1.  **加载金标准定妆照**：读取之前实施人员在完美对准后向上抬升 15cm 拍下的绝对金标准图 `golden_template.jpg`。
2.  **GPU 极速推理匹配**：将当前帧与金标准送入 TensorRT 优化过的 SuperPoint+LightGlue 网络。
3.  **RANSAC 几何校验**：利用 `cv2.findHomography` 计算单应性矩阵，剔除误匹配，筛选出得分最高的 $N$ 个点 (通常 $N=8$)。
4.  **深度校验**：同样在深度图上读取这 8 个点的邻域中值深度，如果某个点落在深度黑洞区 ($Z=0$)，则丢弃该点，只要最终保留 $\ge 4$ 个点即可进入下一环节。

---

## 3. 物理世界坐标锚定 (3D World Anchoring)

无论是 Track 1 还是 Track 2，一旦我们在当前画面上锁定了 $N$ 个极其稳定的像素点 $s_{target}^* (u, v)$，并拿到了它们可靠的深度 $Z^*$，必须**立刻在这一瞬间**将其转换为机器人基座坐标系下的绝对死坐标。

**矩阵推演链条**：

1.  **二维像素升维到相机 3D 坐标**：
    利用相机内参 $K$ 的逆透视：
    $$ X_c = rac{(u - c_x) \cdot Z^*}{f_x} $$
    $$ Y_c = rac{(v - c_y) \cdot Z^*}{f_y} $$
    $$ P_{cam}^* = [X_c, Y_c, Z^*, 1]^T $$

2.  **捕获当前法兰物理姿态**：
    向 C++ Daemon 索要这一帧图像曝光瞬间（利用[模块 5 时间戳插值](./03-Module5-Cpp-Daemon-Time-Sync.md)）的法兰真实物理位姿 $T_{flange\_hover}$。

3.  **相机 3D 到世界 3D (The Anchoring)**：
    利用我们在 Phase 0 标定好的相机外参 $T_{extrinsic}$ (`camera_to_flange`)：
    $$ P_{world}^* = T_{flange\_hover} \cdot T_{extrinsic} \cdot P_{cam}^* $$

至此，我们得到了一个数组 `memory_3d_points = [P_world_1, P_world_2, ..., P_world_N]`。这几个点代表了插槽在物理世界中**绝对的、不可撼动的空间位置**。哪怕接下来相机被砸烂，只要这几个数字存入内存，后续的虚拟 IBVS 就能把机械臂强行引过去。

---

## 4. 与 ACT 和 IBVS 的系统级握手协议 (The Handshake Protocol)

这个模块在代码层面上是一个 ROS 2 的 `LifecycleNode` (生命周期节点) 或者一个基于 Action 状态机的短时阻塞服务。

**状态机转换时序图**：

1.  **[ACT 执行中]**：本特征提取节点处于 `INACTIVE` 状态，不消耗任何 CPU/GPU 资源。
2.  **[到达 15cm 悬停点]**：ACT 模型向本节点发送 Action 目标 `ExtractFeatures(template_id="slot_A")`。ACT 模型释放 GPU 显存。
3.  **[特征提取中]**：
    *   本节点激活，订阅 OAK-D 的 RGB 和 Depth 话题。
    *   执行 Track 1 (几何边缘提取)。
    *   *If Track 1 Fails* $
ightarrow$ 动态加载 TensorRT SuperPoint 引擎，执行 Track 2。
    *   *If Track 2 Fails* $
ightarrow$ 返回 `Action_Abort`，报错“特征全丢，要求 ACT 稍微移动换个视角重试”。
4.  **[坐标锚定完成]**：算出了 `memory_3d_points` 和对应的目标像素 $s_{golden\_pixels}$。
5.  **[移交控制权]**：本节点将包含这 $N$ 个 3D 坐标的结构体，通过 ROS 2 Service Call `StartVirtualIBVS` 发送给 [模块 4 控制器](./04-Module4-Virtual-IBVS-Math.md)。
6.  **[本模块休眠]**：清空 GPU 显存，注销图像订阅话题。系统进入 1000Hz 纯运动学无视觉盲插阶段。

---

## 5. 验收测试标准 (Phase 1.3)

算法工程师在开发完本节点后，必须在真实的带反光金属的工厂环境下进行压力测试：

1.  **高光滑动抵抗测试**：操作员手持强光手电筒，在目标卡槽上方晃动。节点在提取 Track 1 或 Track 2 的 4 个 $P_{world}^*$ 时，这四个三维坐标在内存中的计算抖动方差必须 $< 1.5mm$。
2.  **深度空洞修复测试**：在 OAK-D 原生深度图大面积缺失的黑色反光区，验证 $5 	imes 5$ 中值滤波机制是否能稳定推算出周围边缘的真实深度。
3.  **GPU 显存接力测试**：在 Orin 上使用 `tegrastats` 监控。证明 ACT 进程释放显存与 SuperPoint 加载推理的时序没有重叠导致 OOM（Out Of Memory），且单次特征提取总耗时严格控制在 $150ms$ 以内（在悬停点卡顿 0.15 秒对于工业堆垛完全可接受）。
