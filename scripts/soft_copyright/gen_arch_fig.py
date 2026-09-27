#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""软著说明书插图：系统架构图（matplotlib 绘制，中文 SimHei）。"""

import os

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch

plt.rcParams["font.sans-serif"] = ["SimHei", "Microsoft YaHei"]
plt.rcParams["axes.unicode_minus"] = False

OUT = r"C:\Users\<user>\Desktop\灵境制造（上线版）\output\软著材料\fig_architecture.png"


def box(ax, x, y, w, h, text, fc, ec="#334155", fs=10, bold=False):
    p = FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.02,rounding_size=0.06", fc=fc, ec=ec, lw=1.2)
    ax.add_patch(p)
    ax.text(
        x + w / 2,
        y + h / 2,
        text,
        ha="center",
        va="center",
        fontsize=fs,
        color="#0f172a",
        fontweight="bold" if bold else "normal",
        wrap=True,
    )


def arrow(ax, x1, y1, x2, y2):
    ax.annotate("", xy=(x2, y2), xytext=(x1, y1), arrowprops=dict(arrowstyle="-|>", color="#475569", lw=1.2))


fig, ax = plt.subplots(figsize=(12, 8.5))
ax.set_xlim(0, 12)
ax.set_ylim(0, 8.5)
ax.axis("off")

# 顶层：用户层
box(ax, 0.5, 7.3, 11, 0.9, "用户层：制造工程师 / 工艺员 / 车间操作员 / 管理人员", "#e0f2fe", fs=11, bold=True)

# 交互层
box(
    ax,
    0.5,
    5.9,
    11,
    1.1,
    "交互层：Vue3 前端界面（Tauri 桌面壳）\n图纸导入 · 3D 建模 · 工艺规划 · NC 生成 · 仿真监控 · 生产管理",
    "#fef3c7",
    fs=10,
)

arrow(ax, 6, 7.3, 6, 7.0)
arrow(ax, 6, 5.9, 6, 5.55)

# 应用服务层（FastAPI）
box(
    ax,
    0.5,
    3.9,
    5.2,
    1.4,
    "业务功能层（FastAPI 后端）\n图纸解析 / 特征提取 / 3D 建模\n工艺规划 / G 代码生成 / 后处理\nCAM 验证 / 加工仿真 / 碰撞检测",
    "#dcfce7",
    fs=9.5,
)
box(
    ax,
    6.3,
    3.9,
    5.2,
    1.4,
    "智能引擎层（AI 能力）\nLNN 颤振预测 / 切削参数推荐\nRAG 知识库 / 知识图谱\n自然语言建模 / Agent 编排\n可解释性 / 不确定性量化",
    "#fae8ff",
    fs=9.5,
)

arrow(ax, 4.5, 5.9, 4.5, 5.3)
arrow(ax, 8, 5.9, 8, 5.3)

# 数据与基础设施层
box(
    ax,
    0.5,
    1.9,
    5.2,
    1.6,
    "数据层\n图纸库 / 工艺库 / 知识库（RAG）\n材料与刀具数据库 / 任务记录\n数据飞轮 / 快照 / 文档管理",
    "#f1f5f9",
    fs=9.5,
)
box(
    ax,
    6.3,
    1.9,
    5.2,
    1.6,
    "基础设施层\n任务调度 / 工作流引擎\n插件系统 / 权限认证 / 审计日志\n设备接入 / DNC 传输 / 通知服务",
    "#f1f5f9",
    fs=9.5,
)

arrow(ax, 3, 3.9, 3, 3.5)
arrow(ax, 9, 3.9, 9, 3.5)

# 底层支撑
box(
    ax,
    0.5,
    0.4,
    11,
    1.1,
    "底层支撑：Python 3.11 / FastAPI / PyTorch（LNN）· Rust 后处理组件 · SQLite/向量数据库 · Ollama 本地大模型",
    "#cffafe",
    fs=9.5,
    bold=True,
)

plt.tight_layout()
os.makedirs(os.path.dirname(OUT), exist_ok=True)
plt.savefig(OUT, dpi=200, bbox_inches="tight")
print("架构图已生成:", OUT)
