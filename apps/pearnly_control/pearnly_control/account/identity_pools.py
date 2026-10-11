"""开户账号按产品落在各自的账号池（Keycloak realm）。

用户 2026-09-26 定（方案 A）：Cowork 用自己的账号池 `pearnly-cowork`，与 ERP / DMS（业务账号池 `pearnly-business`）分开，
账号不共用。所以一张发号单开了 ERP 又开了 Cowork，就在两个池里各建一个账号（同一用户名、同一初始密码），
各记各的身份：业务池记在 `identity_subject`（沿用原字段），Cowork 池记在 `cowork_identity_subject`。

目标上哪组字段是哪个池见 `pearnly_core.keycloak_admin.POOL_PREFIX`；这里只管“发号单上的身份记在哪”。
（模块本身不在导入时依赖 Core：离线的开户邮件等测试只装控制面。）
"""

# 账号池（目标上的字段前缀）→ 发号单上记身份的字段。
SUBJECT_FIELDS = {"": "identity_subject", "cowork_": "cowork_identity_subject"}
# 每个池拿一个产品作代表，给 `client_for_target(product=...)` 用。
REPRESENTATIVE = {"": "erp", "cowork_": "cowork"}


def pool_of(product) -> str:
    from pearnly_core.keycloak_admin import POOL_PREFIX

    return POOL_PREFIX.get(product or "", "")


def _value(issue, field):
    # 发号单文档、get_value 的结果、测试替身都能按属性取；取不到（老库还没这一列）当没建。
    return getattr(issue, field, None) or None


def subject_field(product) -> str:
    return SUBJECT_FIELDS[pool_of(product)]


def subject_of(issue, product):
    """这个产品的站点绑哪个身份：它所在账号池里为本单建的那个。"""
    return _value(issue, subject_field(product))


def site_pool(issue, product) -> str:
    """这个产品的站点实际按哪个池登录，回那个池的代表产品（给 `subject_of`、签发方地址用）：本单在产品自己的池里建过账号
    就是它；没建过、只在另一个池里建过的（拆池前开的户，Cowork 账号还在业务池、站点也按业务池登录），用那个池。
    2026-10-11：testcowork 被按产品推算成 Cowork 池登记进入口，CC 入口把人送去没有这个账号的池。"""
    own = pool_of(product)
    if _value(issue, SUBJECT_FIELDS[own]):
        return REPRESENTATIVE[own]
    held = [pool for pool, field in SUBJECT_FIELDS.items() if _value(issue, field)]
    return REPRESENTATIVE[held[0]] if len(held) == 1 else REPRESENTATIVE[own]


def pools_for(products) -> list:
    """所选产品要在哪几个池里建号，按 业务、Cowork 的固定顺序，每个池给一个代表产品。"""
    wanted = {pool_of(product) for product in products if product}
    return [REPRESENTATIVE[pool] for pool in ("", "cowork_") if pool in wanted]


def subjects_of(issue) -> list:
    """本单在各池建的身份：[(代表产品, subject), ...]，没建的池不列。"""
    return [(REPRESENTATIVE[pool], _value(issue, field)) for pool, field in SUBJECT_FIELDS.items() if _value(issue, field)]


def issued(issue) -> bool:
    return issue.status == "issued" and bool(subjects_of(issue))
