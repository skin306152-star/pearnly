"""CC 桌面版共用机器通道（ADR-025）：一个站点上的一台电脑。只经 `cc_desktop.devices` 写。

原生读写与分享一律拒绝（`pearnly_core.cc_desktop.documents`）。
"""
from pearnly_core.cc_desktop.documents import ChannelDocument


class PearnlyCCDevice(ChannelDocument):
    pass
