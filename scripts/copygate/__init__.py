# -*- coding: utf-8 -*-
"""界面文案闸门（scripts/check-copy.py）的实现。

  repo.py     读两棵树：git 某个提交（基线 / --head）或磁盘上的目录（工作树 / --tree 暂存快照）
  sources.py  各文案来源的抽取器：统一产出词条（Entry）与代码里的引用（CodeRef）
  diff.py     基线解析，新增 / 改动 / 删除 / 移动 / 译文改动分类
  rules.py    角色推断与各条判据（词表在 scripts/i18n/copy-words.tsv）
  report.py   Markdown 清单与全量审计统计

只用标准库，不联网。
"""
