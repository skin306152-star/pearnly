"""CC 桌面版共用机器通道（ADR-025）：请电脑读一样东西交回来（按需请求）。只经 `cc_desktop.requests` 写。

原生读写与分享一律拒绝（`pearnly_core.cc_desktop.documents`）。
"""
from pearnly_core.cc_desktop.documents import ChannelDocument


class PearnlyCCRequest(ChannelDocument):
    pass
