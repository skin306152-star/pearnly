"""Pearnly 智能体底座（ADR-019）：三个产品共用的对话会话、Managed Agents 调用与工具派发。

产品只在自己的 `hooks.py` 里声明 `pearnly_assistant_tools`；会话归属、登录与租户边界在这里统一。
"""
