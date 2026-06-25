"""V3 Skills 技能系统：基于 HFACS-CS 六层模型的多技能协同提取。

核心创新：
- 将 V1 的单一提示词拆分为 6 个独立专注的技能模块
- 每个技能只关注一个 HFACS-CS 层级，提示词更短更精准
- 协调器合并所有技能输出
- 支持消融实验：移除某个技能观察效果变化

对比 V1：
  V1: 一个大提示词提取全部字段 → 长协上下文、字段间相互干扰
  V3: N 个技能各司其职 → 短提示、高精度、可并行
"""

from construction_agent.versions.v3.pipeline import V3Pipeline

__all__ = ["V3Pipeline"]
