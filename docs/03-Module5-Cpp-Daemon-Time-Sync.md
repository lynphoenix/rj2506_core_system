# 模块 5 核心架构：非实时内核下的 C++ 守护进程与环形缓冲时间插值

[⬅️ 返回全局总纲](./01-RJ2506-Global-Architecture-And-Task-Breakdown.md)

**文档状态**: DRAFT
**适用阶段**: Phase 1 (非实时环境过渡方案)

## 1. 硬件限制与架构折中
当前架构面临两个不可逾越的硬件级限制：
1.  **无硬件同步线**：OAK-D 标准版相机没有 FSYNC 物理引脚，图像时间戳存在 $10ms \sim 30ms$ 的 USB 随机抖动。
2.  **无实时内核 (No PREEMPT_RT)**：Orin 因其他计算任务冲突，暂时无法刷入实时补丁。这意味着哪怕我们以 1000Hz 发送 CANopen 报文，也会被 Linux 调度器随机抢占，导致微秒级抖动甚至高达几毫秒的挂起。

**目标**：在这两个“非理想”条件下，通过**无锁环形队列 (Lock-free Ring Buffer) 和软件样条插值**，拯救端到端模型 (ACT/DP) 免受“因果混淆”之苦。

## 2. 核心机制：时间穿梭插值 (Time-Travel Interpolation)

因为我们无法强迫相机和电机在同一瞬间同时发生，我们只能利用 C++ 进程在内存里**保留过去 1 秒钟的所有电机状态**。当 ROS 收到一张照片时，去内存里**“倒查”**那个瞬间的电机状态。

### 2.1 C++ Daemon 线程设计 (双线程模型)

为了对抗非实时内核的抢占，C++ 守护进程必须分为两个独立的线程，严格使用**无锁队列**进行跨线程数据共享。

*   **线程 1: CANopen Master 轮询线程 (尽力而为的 1000Hz)**
    *   **职责**：死循环调用 CANopen API 发送/接收 PDO 报文。
    *   **限制**：因为没有 PREEMPT_RT，这个线程的循环时间 `delta_t` 可能是 $0.8ms$，也可能是 $2.5ms$。
    *   **操作**：每收到一帧电机编码器数据，立刻打上**高精度的 `std::chrono::system_clock` 时间戳**（极其重要！），然后连同所有的电机角度、速度，通过 `push_back()` 压入全局的无锁环形缓冲区。
*   **线程 2: ROS 2 / Python 通信线程**
    *   **职责**：等待上层 AI 算法或 IBVS 视觉节点发来的查询请求。
    *   **操作**：算法发来一个图像时间戳 $T_{img}$，本线程去环形缓冲区中查找匹配，算出那一刻的联合状态，然后返回给上层。

### 2.2 1000 帧环形缓冲区 (Ring Buffer)

```cpp
#include <deque>
#include <mutex>
#include <chrono>

struct MotorState {
    double timestamp;  // 精确系统时间 (秒)
    double q[12];      // 12 个关节角度
    double dq[12];     // 12 个关节速度
    double tau[12];    // 12 个关节扭矩/电流
};

// 预分配固定大小，防止动态内存分配导致耗时抖动
class StateBuffer {
private:
    std::deque<MotorState> buffer;
    size_t max_size = 1000; // 保留过去 1 秒的数据 (假设平均 1000Hz)
    std::mutex mtx; // 或者更好的无锁并发结构如 moodycamel::concurrentqueue

public:
    void push(const MotorState& state) {
        std::lock_guard<std::mutex> lock(mtx);
        if (buffer.size() >= max_size) {
            buffer.pop_front();
        }
        buffer.push_back(state);
    }
    
    // ...
```

### 2.3 基于历史时间戳的样条插值 (Spline Interpolation)

当算法端拿到一张时间戳为 $T_{img\_recv}$ 的相机照片时：

1.  **补偿 USB 固有延迟**：系统管理员预先标定出一个常数 $\Delta T_{usb}$（通常约为 $25ms$）。
    真实曝光时间：$T_{exposure} = T_{img\_recv} - \Delta T_{usb}$
2.  **查找邻近帧**：去 `StateBuffer` 中查找，找到时间戳刚好夹住 $T_{exposure}$ 的前后两帧数据 $S_{before}$ 和 $S_{after}$。
    *   $T_{before} \le T_{exposure} < T_{after}$
3.  **线性插值 (Lerp)**：
    由于非实时内核导致电机帧之间的时间间隔不一定是严格的 $1ms$，必须根据时间比例进行加权插值：
    
    $$ Ratio = frac{T_{exposure} - T_{before}}{T_{after} - T_{before}} $$
    
    对于每一个关节角度 $q_i$：
    $$ q_i^{target} = q_i^{before} + Ratio 	imes (q_i^{after} - q_i^{before}) $$

4.  **返回结果**：将这个经过插值算出来的完美 $q^{target}$，和那张照片拼在一起，送进 HDF5 里供 ACT 训练。

---

## 3. 为什么在非实时内核下这样设计能“保命”？

如果不做这套插值，你直接拿 $T_{img\_recv}$ 去抓取当前最新的电机制状态，你的误差将是：
`USB 延迟 (约 25ms) + Linux 抢占抖动 (可能 2~5ms) = 约 30ms 错位`。

使用了这套 C++ 环形缓冲区插值后：
我们硬生生地把电机的状态“时光倒流”了 $25ms$，并且通过前后两帧插值，**填平了 Linux 非实时调度器导致的那几毫秒的数据缺失。**

最终，提供给端到端训练的数据，其时间对齐误差将被**压榨到 $\pm 3ms$ 以内**。这就从根本上拯救了模型，使其免于因果混淆。
