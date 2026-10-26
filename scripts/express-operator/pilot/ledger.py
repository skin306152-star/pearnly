"""花费账本：每次 API 调用记一行（模型、用途、usage、美元），跨多次运行累计。

- 硬上限（默认 90 美元）：发请求前按「已花 + 这次最坏情况」判断，超了就抛 BudgetStop，不发；
- 每满 10 美元记一个「待报告」节点：当前这张单录完后执行器停下（退出码 10），向主会话报实际花费，确认后带 --ack 继续；
- 账本不放仓库（默认 ~/pearnly-devtools/xp-pilot/ledger.jsonl），不含密钥、不含截图。
"""
from __future__ import annotations

import json
import time
from decimal import Decimal
from pathlib import Path
from typing import Dict, Optional

from . import pricing

DEFAULT_PATH = Path.home() / "pearnly-devtools" / "xp-pilot" / "ledger.jsonl"
HARD_CAP = Decimal("90")
MILESTONE = Decimal("10")


class BudgetStop(Exception):
    """到硬上限，不再发请求。"""


class Ledger:
    def __init__(self, path: Path = DEFAULT_PATH, hard_cap: Decimal = HARD_CAP, milestone: Decimal = MILESTONE):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.hard_cap = Decimal(hard_cap)
        self.milestone = Decimal(milestone)
        self.ack_path = self.path.with_suffix(".ack.json")
        self._total = Decimal(0)
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    self._total += Decimal(json.loads(line)["usd"])

    @property
    def total(self) -> Decimal:
        return self._total

    def acked(self) -> Decimal:
        if self.ack_path.exists():
            return Decimal(json.loads(self.ack_path.read_text(encoding="utf-8"))["acked_usd"])
        return Decimal(0)

    def ack(self) -> None:
        """主会话已收到报告：把「已报告」推到当前累计所在的整 10 美元。"""
        level = (self._total // self.milestone) * self.milestone
        self.ack_path.write_text(json.dumps({"acked_usd": str(level), "at": time.time()}), encoding="utf-8")

    def pending_milestone(self) -> Optional[Decimal]:
        """累计越过了还没报告的整 10 美元节点时，返回该节点。"""
        level = (self._total // self.milestone) * self.milestone
        return level if level > self.acked() and level > 0 else None

    def guard(self, model: str, prompt_estimate: int, max_tokens: int) -> None:
        worst = pricing.worst_case(model, prompt_estimate, max_tokens)
        if self._total + worst > self.hard_cap:
            raise BudgetStop(f"已花 {self._total} 美元，这次最坏 {worst} 美元，会超过上限 {self.hard_cap} 美元")

    def record(self, *, model: str, usage, purpose: str, doc_id: str, mode: str, seconds: float,
               extra: Optional[Dict] = None) -> Decimal:
        norm = pricing.normalize(usage)
        usd = pricing.cost(model, norm)
        row = {"at": round(time.time(), 3), "model": model, "rate": pricing.rate_key(model, norm), "purpose": purpose,
               "doc_id": doc_id, "mode": mode, "seconds": round(seconds, 2), "usage": norm, "usd": str(usd)}
        if extra:
            row.update(extra)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        self._total += usd
        return usd
