"""启动信息里带上站点总闸：没开的站点「新建录入」弹窗照旧三张卡片，规则页入口不出现（不改现有界面）。"""
from pearnly_cowork.folder_intake import settings


def extend_bootinfo(bootinfo):
    bootinfo.pearnly_cowork_folder_intake = 1 if settings.enabled() else 0
