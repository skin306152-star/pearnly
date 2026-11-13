"""LINE 快捷回复 / 按钮上「点序号选」的标签（多产品共用：ERP、Cowork、DMS 的 LINE 入口都按这一条做）。纯函数，不碰站点与网络。

LINE 标签最多 20 个字。各处原来各自 ``[:20]`` 截断：同一组里名称开头相同（同一家公司的两本账套「9.บริษัท Pearnly จำกัด TEST2」
「9.บริษัท Pearnly จำกัด」、同名分店、同前缀的仓库）截完就一样，只能靠序号猜（11-13 收款真机）。这里保证序号后面那段在名称不同时彼此不同。
"""
import os

QUICK_LABEL_MAX = 20
_BREAKS = " ·-_/\\"


def choice_labels(names, limit=QUICK_LABEL_MAX) -> list:
    """``["1. 名称", …]``，每个不超过 ``limit`` 字。先照开头截；截完相同的那几个跳过共同开头（退到词边界）、显示开始不同的那一段
    （前面加「…」）；还相同就显示结尾。名称本身相同的不强分（列表里写全名，靠序号）。"""
    names = [" ".join(str(name or "").split()) for name in names]
    prefixes = [f"{index}. " for index in range(1, len(names) + 1)]
    rooms = [max(2, limit - len(prefix)) for prefix in prefixes]
    parts = [name[:room] for name, room in zip(names, rooms)]

    def clashes():
        seen = {}
        for index, part in enumerate(parts):
            seen.setdefault(part, []).append(index)
        return [group for group in seen.values() if len(group) > 1 and len({names[i] for i in group}) > 1]

    for group in clashes():
        common = len(os.path.commonprefix([names[i] for i in group]))
        cut = max((names[group[0]].rfind(mark, 0, common + 1) for mark in _BREAKS), default=-1) + 1 if common else 0
        for i in group:
            rest = names[i][cut:].strip(" ·-_")
            parts[i] = ("…" + rest)[:rooms[i]] if cut and rest else names[i][:rooms[i]]
    for group in clashes():
        for i in group:
            parts[i] = ("…" + names[i][-(rooms[i] - 1):]) if len(names[i]) > rooms[i] else names[i]
    return [prefix + part for prefix, part in zip(prefixes, parts)]


__all__ = ["QUICK_LABEL_MAX", "choice_labels"]
