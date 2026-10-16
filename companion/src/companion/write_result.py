"""DBF 写路的返回形状(采购/销项共用)。

从 dbf_writer 抽出,采购 / 销项两条写路共用。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class DbfWriteResult:
    ok: bool
    docnum: Optional[str] = None
    skipped: bool = False  # 同一推送任务重发、上次已写过 → 本次未重写(见 task_journal)
    error_code: Optional[str] = None
    error: str = ""
    backup_dir: Optional[str] = None
    party_code: Optional[str] = None  # 解析/新建出的供应商或客户码
    created_party: bool = False  # 本次是否新建了主档(APMAS/ARMAS)
    # V2 逐行明细 mode 结果:[{seq,name,mode,stkcod,reason}]。mode=stock_item(真入库)/
    # non_stock_item/direct_account;reason 非空=该行从 non_stock 回落了 direct_account
    # (主档建/配失败·诚实记因·不静默)。
    line_modes: Optional[list] = None
    # 诚实标注:{标注键: 人话原因}。写成功但**有一部分没写**时必填(会计期间判不出 → 应收/
    # 应付月桶未写)。会计据此知道要去 Express 补哪一笔,不填就成了静默漏账。
    notes: Optional[dict] = None
    # 失败时的结构化现场证据(readback_verify / detail_mismatch / detail_sum_mismatch)——
    # 随 ack meta.evidence 回云端,支持/会计一眼看到卡在哪个表哪一行。成功路径恒 None。
    evidence: Optional[dict] = None
    # 写完顺手核对(数字会计员第 3 期第 6 条,write_verify):{state: ok|mismatch, fields, party}。只报不回滚,
    # 随 ack meta.verify 回云端;失败路径恒 None。
    verify: Optional[dict] = None

    @property
    def written(self) -> bool:
        """本次是否真往账套写了字节 —— 调用方判「到底进没进去」只认这一个。

        ok=True 且 skipped=True(同一任务重发)= 没写。算出来而不是存下来:多一个可写字段就多
        一处与真相漂移的机会,而「报成功但没写」正是这轮要根治的谎。
        """
        return bool(self.ok and not self.skipped)
