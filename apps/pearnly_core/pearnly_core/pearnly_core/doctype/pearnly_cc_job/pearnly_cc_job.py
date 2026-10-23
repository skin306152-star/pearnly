"""CC 桌面版共用机器通道（ADR-025）：一件要电脑写进 Express 的单据。只经 `cc_desktop.jobs` 写。

原生读写与分享一律拒绝（`pearnly_core.cc_desktop.documents`）。
"""
from pearnly_core.cc_desktop.documents import ChannelDocument


class PearnlyCCJob(ChannelDocument):
    pass
