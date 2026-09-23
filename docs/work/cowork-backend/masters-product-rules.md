# 主数据第四块：商品名称与单位规则（第四十六批）

第四十六批从新平台 `b90fd75` 开工，工作树 `/Users/skin/.codex/worktrees/cowork-backend-migration/pearnly`，分支 `codex/cowork-backend-migration`。线上来源仍是 `ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188`。不接前端、不部署、不改 DMS、不写旧库、不调模型。

客户那三块交完后，主数据往下走是**商品**。旧侧商品只有两个纯逻辑文件（`names.py` 33 行、`units.py` 244 行里的字段判定），本批把它们搬净：名称呈现规则与多单位的字段/数值规则。单位表、外键与 RLS 属存储层，下一批。

## 一、本批交付

| 新落点 | 来源（sha256 固定） | 内容 |
| --- | --- | --- |
| `masters/product_names.py` | `services/products/names.py`（`c690b97f…`，33 行），逐字搬 | `product_names`、`display_product_name`、`product_name_object` |
| `masters/product_units.py` | `services/products/units.py`（`b686a65b…`，244 行）不碰数据库的部分 | `coerce_number`（旧 `_num`）、`unit_insert_values`、`unit_update_values`、`clears_other_defaults`、三张字段表 |

保留的部署版口径：

- **名称**：泰/英/中按序取，空的不算，重复（忽略大小写）只留第一次出现，展示串用 `" / "`，全空时用调用方给的占位；
- **数值列**（`factor_to_base`、`price`）走 `Decimal(str(v))`（避免浮点误差），传非数字会抛 —— 与旧侧同样抛；
- **建档**只写"值不是 None"的字段（显式 None = 这次不写）；
- **更新**写"键存在且（值不是 None 或该字段可空）"的字段 —— 可空列（`barcode`/`price`）传显式 None 表示**清空**（箱码印错了要能删掉，不能只能改成别的码）；
- 更新时没有要写的字段 → 返回 None（旧侧照旧返回当前行）；
- `is_default_sell` 为真时返回 `clear_other_defaults=True`：调用方必须先清掉该商品其它单位的默认标记（**单一默认**）。

### 有意差异

| 旧 | 新 | 理由 |
| --- | --- | --- |
| 一个函数里既决定"写什么"又执行 SQL（`_clear_default`、INSERT/UPDATE、`_sync_product_active`） | 只返回"要写什么"与"要不要先清默认" | 表与外键属存储层；判定可离线差分 |
| 单位行还带 `product_active` 同步（僵尸条码那段注释） | 不在本批 | 那是存储层的列与同步，随表一起搬 |

## 二、验证与证据

- 离线用例（隔离 bench）**15** 个：三语按序、空与重复被略、展示串与占位、名称对象保留原值；`Decimal` 精确转换（`"0.1"`/`0.1`/`3`/None）、非数字抛 `InvalidOperation`；建档只写有值的字段、数值是 Decimal、什么都不给不写；更新里显式 None 清可空列、显式 None 不写必填列、只写给了键的字段、没得写返回 None、`False` 也算写入（关掉默认标记）；真值默认标记要求先清其它默认。
- 固定源差分：`names.py` 是纯函数，**7 组名称行逐字段比对**；`units.py` 把游标换成记录器（记录旧侧每条语句与参数，按部署版列序与 `%s` 参数还原"要写的列"）：**122 个建档 + 117 个更新场景，差异 0**。计数：默认清理 47、数值列 262、清空 21、丢弃未知键 64、无可写 1。
- 同族回归：客户字段规则 + 买家决策 + 商品规则一起 **56** 个通过。
- 静态检查：架构 **0** 违规；`i18n-build --check` PASS；`check-i18n` REPORT-ONLY；`git diff --check` 干净。
- 线上来源：交付 `evidence/baseline-forty-sixth-delivery.json`（`922740a8…`，采集 18:22:31Z）与第四十五批交付快照 `compare` **PASS**；运行投影 `push-runtime-forty-sixth-delivery.json`（`3a9e688b…`）与上一批**逐项相同**。

### 仍未做开工采集

同前几批：`before` 用上一批交付快照。

## 三、下一批

商品与单位的**存储层**（商品档案 + 多单位表：基础单位、换算、条码、售价、单一默认、批量/有效期/称重与最低库存等商品列），之后是供应商与科目/税率档、设置与分类、客户导入归一。

## 四、未做

- 未写存储层、未改 DocType、未加 RPC；未接前端、未部署、未推送、未合回主线；未动共享底座；未做用户验收。
