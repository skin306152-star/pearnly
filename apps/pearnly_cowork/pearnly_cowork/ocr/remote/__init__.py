"""Cowork 发票识别在 Cloud Run 上执行（ADR-0015）。

``contract`` 是服务器与 Cloud Run 共用的纯约定；``runner`` / ``app`` 只在 Cloud Run 镜像里跑（不依赖 Frappe）；
``forward`` 是服务器这边的转发（Frappe 识别任务里调用）。识别读法（模型、提示词、调用顺序、结果整理）与本机执行
是同一份引擎代码，只改执行位置。
"""
