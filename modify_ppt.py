import re

with open('generate_ppt.py', 'r', encoding='utf-8') as f:
    content = f.read()

# 1. 调整 add_bullet 函数，减小默认字号和行距，增加 base_size 参数
old_add_bullet = """def add_bullet(tf, text, level=0, bold=False, color=COLOR_TEXT_DARK):
    p = tf.add_paragraph()
    p.text = text
    p.level = level
    # 增加基础字号，提升可读性
    p.font.size = Pt(28 - level*4)
    p.font.bold = bold
    p.font.color.rgb = color
    set_font(p)
    # 设置行距和段前距
    p.line_spacing = 1.2
    p.space_before = Pt(8)"""

new_add_bullet = """def add_bullet(tf, text, level=0, bold=False, color=COLOR_TEXT_DARK, base_size=22):
    p = tf.add_paragraph()
    p.text = text
    p.level = level
    # 动态字号，避免溢出
    p.font.size = Pt(base_size - level*3)
    p.font.bold = bold
    p.font.color.rgb = color
    set_font(p)
    # 设置紧凑行距和段前距
    p.line_spacing = 1.1
    p.space_before = Pt(5)"""

content = content.replace(old_add_bullet, new_add_bullet)

# 2. 批量将特定高密度幻灯片的字号调小 (通过正则替换 add_bullet 调用，或者我们直接改全局 base_size 为 22 已经足够？)
# 保险起见，我们在 Slide 6, 7, 8, 9, 10 这种多文字页面直接把 base_size=20 加上去。
# 但是更简单的方法是：新 add_bullet 默认 base_size=22，level*3 递减。
# Level 0: 22, Level 1: 19, Level 2: 16。之前的 28 确实太大了。

# 3. 展开 Phase 2 的内容
old_phase2 = """add_bullet(tf, "Phase 2: AI 训练与全链路联调", bold=True, color=COLOR_SECONDARY, level=0)
add_bullet(tf, "时间: 4周", level=1, color=COLOR_ACCENT, bold=True)
add_bullet(tf, "任务: Sim2Real数据采集与训练、模型部署、实车打通与联调", level=1)"""

new_phase2 = """add_bullet(tf, "Phase 2: AI 训练与全链路联调", bold=True, color=COLOR_SECONDARY, level=0)
add_bullet(tf, "时间: 4周", level=1, color=COLOR_ACCENT, bold=True)
add_bullet(tf, "核心任务列表:", level=1, bold=True)

add_bullet(tf, "[2.1] 数据采集与 Sim2Real", level=2, bold=True, color=COLOR_PRIMARY)
add_bullet(tf, "• 录制 50 条遥操作真机数据，导入 IsaacLab", level=3)
add_bullet(tf, "• 生成 10000 条带各种光照变异的合成数据进行扩充", level=3)

add_bullet(tf, "[2.2] DP 模型训练与 TensorRT 部署", level=2, bold=True, color=COLOR_PRIMARY)
add_bullet(tf, "• 在 H100 集群上完成 Diffusion Policy 训练", level=3)
add_bullet(tf, "• Orin 边缘端 TensorRT 部署，保证推理延迟 < 30ms", level=3)

add_bullet(tf, "[2.3] 大脑节点 (Brain Node) 实车串联", level=2, bold=True, color=COLOR_PRIMARY)
add_bullet(tf, "• 将 Nav2 -> ACT -> Feature Memory -> IBVS -> Touch-down 管线在真机完全打通", level=3)"""

content = content.replace(old_phase2, new_phase2)

# 另外发现 Scene 1 和 Scene 2 之前的卡片描述可能也有溢出风险
# 把场景 1/2 卡片里的字体也稍微调小
content = content.replace('p1_desc.font.size = Pt(16)', 'p1_desc.font.size = Pt(14)')
content = content.replace('p2_desc.font.size = Pt(16)', 'p2_desc.font.size = Pt(14)')
content = content.replace('p1.font.size = Pt(22)', 'p1.font.size = Pt(20)')
content = content.replace('p2.font.size = Pt(22)', 'p2.font.size = Pt(20)')

with open('generate_ppt.py', 'w', encoding='utf-8') as f:
    f.write(content)
print("generate_ppt.py has been updated successfully.")
