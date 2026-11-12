"""CC 对话的驱动接口（真模型场景用）。场景只对这个接口写；实现明天对着真环境接。

    talk = ctx.targets.conversation(product="cowork", mode="direct", company=…, ledger=…)   # 待接
    talk.attach([票据文件…]); talk.say("录一下"); talk.click("plan", "write"); talk.stop(); talk.wait_idle()
    record = talk.record()     # cc_lines.Record：对话、工具调用、卡片、喊停；写入由场景用账本前后对比补上
    talk.cost()                # 这段对话到现在花了多少美元（场景每一步后 ctx.spend）

实现计划（不重造）：收编 `scripts/e2e/stress.py` 的 `run_one`（真 Keycloak 登录或站点会话、每 10 秒截图、读会话事件里的工具调用与用量折算、
替用户点卡片、费用到顶点停止）与 11-12 A 线「本对话账套」真模型脚本的做法（ssh → 测试机实验栈、命中真账套即停）。
**状态：只有接口，没有实现**——用它的场景现在一律记「没测到」（Unmet），不会假装跑过。
"""
from __future__ import annotations

import sys

Unmet = sys.modules["acc_errors"].Unmet if "acc_errors" in sys.modules else RuntimeError


class Conversation:
    def __init__(self, product: str, mode: str, company: str = "", ledger: str = ""):
        self.product, self.mode, self.company, self.ledger = product, mode, company, ledger
        raise Unmet("CC 对话驱动还没接（scripts/acceptance/cc_driver.py）：等真环境可用时实现")

    def attach(self, files: list) -> None: ...
    def say(self, text: str) -> None: ...
    def click(self, card: str, choice: str) -> None: ...
    def stop(self) -> None: ...
    def wait_idle(self, seconds: int = 180) -> None: ...
    def record(self): ...
    def cost(self) -> float: ...
