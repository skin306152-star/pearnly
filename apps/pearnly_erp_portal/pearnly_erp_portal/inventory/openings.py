"""期初库存。

期初**落成流水**（`movement_type=opening`），不单独留一份报表基数。为什么：旧线上
期初只进收发存报表、不进在库结存，于是"在库 30、收发存期末 40"两个数并存——同一个
事实两个答案。现在把期初写成流水，在库、收发存、成本全部同源，只有一个数。

`ERP Stock Opening` 保留为不可变的初始化凭据。完全相同的重放不加库存，已有凭据
或库存流水的仓不能重新设置期初；后续变更走入库或盘点调整。
"""

from pearnly_erp_portal.inventory import opening_batch


def upsert_opening(*, product, warehouse, as_of_date, qty, unit_cost=None, note=None) -> dict:
    """单个仓放一笔期初（测试与实验室准备数据用；页面走 `opening_batch` 的预览 / 提交）。

    对外接口 `api_upsert_opening` / `api_list_openings` 没有页面调，11-05 删了。
    """
    return opening_batch.upsert_one(product=product, warehouse=warehouse, as_of_date=as_of_date,
                                    qty=qty, unit_cost=unit_cost, note=note)
