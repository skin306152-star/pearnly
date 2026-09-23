# 收银台「官方 1:1」台账（2026-09-23 开工）

> 用户口径（2026-09-23）："把官方的 POS 内所有布局位置间距组件交互业务逻辑全部按我们产品给我做到
> 100% 1:1，所有，你自己想办法做到一模一样，什么位置有些啥，长啥样，在什么位置，点什么发生什么，
> 全部给我做成一样。"
>
> 这份台账就是"不漏"的办法：**先把官方有的东西全部清点出来**（从源码 + 运行页两处取），
> 再逐行标注我们这边**有没有 / 像不像 / 点什么会发生什么**，最后按行施工、按行验收。
> 施工记录仍在 `docs/work/2026-09-23-erp-sales-records.md`（F 系列），本文件只做"清单 + 状态"。

## 0. 底线（先说清楚，不动摇）

- **不装 ERPNext、不搬它的 DocType**：租户站点只装 `pearnly_core` + 本产品 App，所以官方的
  CSS/JS **不能直接引**；1:1 只能是"**把官方的标记与样式原文抄进我们自己的页面**"，
  数据与服务端逻辑接到**我们的**接口（`ERP Sale` / 我们的开班交班 / 我们的税口径）。
- 官方那些**依赖我们没有的底座**的功能（忠诚度、优惠券、定价规则、序列号/批号、离线模式、
  POS Profile 权限位、打印格式）—— 凡是"要新增数据结构或改产品可售卖范围"的，
  在台账里标 `需决定`，不悄悄加，也不假装做了。
- 验收一律"**两边同一屏、同一步、同视口，用探针量**"（`scripts/pos-parity-probe.js` +
  `scripts/pos-parity-diff.py`），不靠肉眼对截图。

## 1. 官方清单（源码事实，ERPNext 16.34.1）

`apps/erpnext/erpnext/selling/page/point_of_sale/`：

| 模块 | 行数 | 负责什么 |
| --- | --- | --- |
| `pos_controller.js` | 910 | 外壳与总调度：开班凭证检查/创建、过期开班提示、交班监听、菜单与两个页头按钮、六块组件的切换、桥接 `frappe.ui.form.Form`、提交后汇总、关闭收银台 |
| `pos_item_selector.js` | 494 | 左侧商品区：搜索栏 + 商品组过滤 + 网格/**列表**两种模式（`hide_images`）、列头（名称/单价/库存/UOM）、库存角标与购物车数量胶囊、加载动画、清空/打开链接、条形码回车即加、每页 40 条 |
| `pos_item_details.js` | 480 | 选中行的明细面板：图/缩写、单价与折扣（划线价 + 绿色折扣角标）、字段表单（数量/单价/单位/折扣/仓库/序列号/批号）、序列号自动取号、**字段与键盘联动**、关闭按钮 |
| `pos_item_cart.js` | 1129 | 右栏：客户区（选择器 + 客户卡 + 联系方式/忠诚度字段 + 最近交易）、购物车表头与行、折扣格、合计（物料数/净总计/**税费行**/总计）、键盘、结账按钮的高亮与禁用、行高亮 |
| `pos_payment.js` | 737 | 付款屏：付款方式行（每种方式一个金额控件，可展开/收起）、**附加信息**弹窗（`invoice_fields`）、3 列键盘、总计/付款/剩余/找零、完成订单、优惠券、电话支付实时回调、快捷键 |
| `pos_past_order_list.js` | 134 | 「最近订单」列表：搜索、状态过滤、按客户/时间筛选 |
| `pos_past_order_summary.js` | 490 | 单据汇总：抬头、物料行（可退）、税额、付款、打印/邮件/退货/编辑等按钮 |
| `pos_number_pad.js` | 59 | 通用数字键盘组件（键位、列数、`data-button-value`） |
| `point_of_sale.py` | 596 | 服务端：`set_customer_info`、`get_customer_recent_transactions`、取商品与库存、POS Profile 相关 |
| `point-of-sale.scss` | 1438 | 全部版式与间距（**这是"长啥样"的唯一基准**） |

**POS Profile（官方）里前端真正读到的开关**（`settings.*`，实测计数）：
`warehouse`(4)、`invoice_fields`(4)、`frm_doctype`(4)、`hide_images`(3)、`customer_groups`(2)、
`allow_rate_change`(2)、`allow_discount_change`(2)、`action_on_new_invoice`(2)、
`set_grand_total_to_default_mop`、`print_receipt_on_order_complete`、`auto_add_item_to_cart`、
`allow_partial_payment`。

**官方快捷键（用户可见）**：`⌘/Ctrl+Enter` 完成订单、`⌘/Ctrl+D` 折扣、`⌘/Ctrl+I` 搜索物料、
`⌘/Ctrl+G` 商品组、`⌘/Ctrl+P` 打印（汇总）、`⌘/Ctrl+E` 编辑（汇总）、`Tab` 在付款方式间切换；
购物车键盘另有 `⌘+Q/⌘+D/⌘+R`（数量/折扣/单价）与 `⌘+Backspace`（删除）/`⇧⌘+Backspace`（移除）。

**官方元素清单（从各模块 `class="` 取，共 ~150 个）**：见第 3 节"机械比对"里逐屏点名的那几组，
完整列表由 `scripts/pos-parity-probe.js` 里的 `SELECTORS` 表维护（**加元素就加一行**）。

## 2. 施工顺序（一个屏一个屏过，每屏都跑探针）

| 序 | 屏 | 内容 | 状态 |
| --- | --- | --- | --- |
| 1 | 外壳 | 页头（返回、标题、`…` 菜单、两个页头按钮）、六块组件的显隐规则、`最近订单` 的切换 | 未开始 |
| 2 | 商品区 | 搜索栏（含清空、打开链接、商品组）、网格/列表两模式、列头、库存角标、数量胶囊、分页与加载动画 | 部分（缺列表模式、列头细节） |
| 3 | 明细面板 | 图/缩写、单价与折扣（划线 + 绿角标）、字段与键盘联动、关闭 | 未系统比对 |
| 4 | 购物车 | 客户卡（含"点击添加邮箱/电话"占位）、表头、行（单价/金额/单位/折扣）、行高亮 | 行 1:1；**客户卡已补**（默认散客卡 → 点开搜索 → 选中换卡 → ✕ 回散客卡，四语实测） |
| 5 | 合计 | 物料数 / 净总计 / **税费行** / 总计、折扣格 | 折扣格已 1:1；**税费行缺** |
| 6 | 键盘 | 5 列键位、目标键高亮、删除/移除语义、结账键 | 已按官方重做（F19/F22），待逐项量 |
| 7 | 付款屏 | 付款方式行与金额控件、展开/收起、附加信息、3 列键盘、四个数字、快捷键、完成订单 | 部分（缺附加信息、方式行结构） |
| 8 | 最近订单 | 列表 + 筛选 + 状态 | 有（未比对） |
| 9 | 单据汇总 | 抬头、物料行、税额、付款、打印/邮件/退货/编辑按钮 | **弱**（我们那张汇总是自己拼的，已被用户点名图五） |
| 10 | 开班/交班 | 官方是 POS Opening/Closing Entry 两个 DocType + 弹窗；我们用 `ERP Shift` | 需决定（见第 4 节） |

## 3. 机械比对（这一轮实测出来的缺口）

方法：按模块把官方的 class 清单逐个在**两边同一屏**查存在性（`querySelector`），
再加 `scripts/pos-parity-probe.js` 量样式与几何。购物车这一屏（车里有货、无选中行）结果：

**官方有、我们没有**（按重要性）：

| 元素 | 官方是什么 | 我们的现状 |
| --- | --- | --- |
| `.customer-details/.customer-display/.customer-name/.customer-name-desc/.customer-desc/.customer-image/.customer-abbr/.reset-customer-btn` | 客户区在**没选客户时也有一张默认客户卡**（圆头像 = 名字缩写、"点击添加邮箱/电话"小字、右侧 ✕ 清空） | 我们原来只有一条搜索框（图一/图二右上角的差别）→ **已补**：默认画**散客卡**（我们"空客户 = 散客"），点卡片开搜索框、选了换客户卡、✕ 回散客卡；标记与官方同一套 |
| `.taxes-container/.tax-row/.tax-label/.tax-value` | 合计区在"净总计"与"总计"之间有**税费行**（按税率逐条列） | 我们没有：我们的付款屏与汇总里既没有税费行，汇总那张还把税率显示成 `THB 0` |
| `.seperator` | 合计区的小分隔线 | 未确认（我们用了别的结构） |
| `.item-qty-total-label/.item-qty-total-value/.net-total-label/.net-total-value` | 合计每一行都是"容器 + label + value"三层 | 我们是两层（自己写的），**同一步官方页上这几个类名在我们这边不存在** |

**我们有、官方没有**（要么是我们自造、要么是状态差异）：
`.customer-field`（我们把选择器做成了一条输入框，官方是"卡片 + 点开才编辑"）、
`.edit-discount-btn`/`.taxes-container`（当时状态不同，需同屏复测）。

**已知的、预期的不同**（不是缺陷，登记为"允许差异"）：
结账按钮底色（官方近黑 = 它那套 `--btn-primary`；我们用品牌色）、正文色（两站主题不同）、
金额/UOM 文案（税率、币种、语言本来就不同）。

## 4. 需你拍板的（这些不是"抄样式"能解决的）

| # | 事项 | 官方怎么做 | 要新增什么 | 我的建议 |
| --- | --- | --- | --- | --- |
| D1 | 合计区的**税费行** | 由税务模板逐条列（税名 + 金额），并进"总计" | 我们需要按商品/公司算出税额明细并回给前端（现有单据字段里税额是有的，但购物车阶段没有逐条明细） | **做**：按我们已有的 VAT 口径算，购物车就显示税费行 —— 与"发票要能对上"一致 |
| D2 | 客户区的**默认客户卡** | POS Profile 有默认客户（Walk-in），一进去就显示卡 | 我们要有"默认客户"这一位（终端/公司设置里） | **做**：用我们终端上的默认客户；没有配就显示"散客"卡 |
| D3 | 客户的**联系方式/忠诚度字段**、最近交易 | 客户卡下有 email/电话/忠诚度字段与最近交易列表 | 忠诚度我们没有 → 只做 email/电话？ | 只做 **email/电话 + 最近交易**；忠诚度不做（我们产品没有这套） |
| D4 | 付款屏「附加信息」弹窗 | 由 POS Profile 的 `invoice_fields` 决定字段 | 我们有单据备注/参考号这类字段，但没有"按配置拼弹窗"的机制 | 先做**固定的那几格**（备注等），做成和官方同一个位置与交互 |
| D5 | 优惠券 / 定价规则 / 序列号 / 批号 | 官方各自成块 | 我们没有这些底座 | **不做**（与既有产品范围一致）；要做得单开一包 |
| D6 | 开班/交班 | 官方是 POS Opening/Closing Entry 两个 DocType + 弹窗（含验钞、对账、多终端） | 我们已有 `ERP Shift`（用户此前口径是"简单的开班交班"） | **保持我们的 DocType，界面按官方那张弹窗 1:1** |
| D7 | 打印小票 / 邮件小票 / 退货 | 官方汇总页三个按钮 | 打印格式（PDF）与邮件通道 | 先做**按钮与交互 1:1**，打印内容用我们自己的格式 |

## 5. 这一轮已经做完的（细节在销售记录 F61/F62）

- 折扣格（输入框整格宽、左对齐、官方图标、应用态无框、>100% 提示）—— **1:1，量过**；
- 购物车行的金额格（金额≠单价才两行、上金额下单价）与行高 —— **1:1，量过**；
- 对照机本体（探针 + 差异脚本 + 同屏同步的用法）。

## 6. 下一轮做什么（不问你，直接按这个顺序推）

1. 第 2 屏商品区（列表模式、列头、库存角标、分页/加载）与第 7 屏付款屏（方式行结构、
   附加信息、键盘快捷键）——这两屏缺口最大且不依赖新底座；
2. 第 4/5 屏的客户卡与税费行（D1/D2 按下表建议先做，做完给你看效果，不合适再回退）；
3. 第 9 屏单据汇总（被点名的图五）——先按官方 `pos_past_order_summary` 的行逐条对齐，
   然后把"增值税税率 `THB 0`"那处明显的错值修掉；
4. 每做完一屏：两边同屏跑探针 → 差异表清零 → 记录进本台账第 2 节的"状态"列。

## 7. 改走路子：**整屏搬过来，再删功能、接我们的服务**（2026-09-23 用户口径）

用户："**搬吧，搬过来做功能删减和功能对接**"。所以不再一处一处画 —— 官方的前端整棵搬进本 App，
然后 (a) 按功能删掉我们不做的，(b) 把要用的接到我们自己的服务上。

**已搬入**（`apps/pearnly_erp_portal/pearnly_erp_portal/public/desk/pos/`，每个文件头都有来源与许可标注）：

| 文件 | 行数/大小 | 对应屏 |
| --- | --- | --- |
| `official-pos_controller.js` | 910 行 | 外壳与调度（开班检查、组件切换、form 桥、提交后汇总） |
| `official-pos_item_selector.js` | 494 | 商品区（搜索、商品组、网格/列表、角标、分页） |
| `official-pos_item_details.js` | 480 | 明细面板（图/价/折扣、字段表单、与键盘联动） |
| `official-pos_item_cart.js` | 1,129 | 右栏（客户区、行、折扣格、合计含税行、键盘、结账键） |
| `official-pos_payment.js` | 737 | 付款屏（方式行、附加信息、键盘、四个数字、完成订单） |
| `official-pos_past_order_list.js` | 134 | 最近订单列表 |
| `official-pos_past_order_summary.js` | 490 | 单据汇总（就是"图五"那块） |
| `official-pos_number_pad.js` | 59 | 数字键盘组件 |
| `official-point_of_sale.js` | 23 | 页面入口（`new erpnext.PointOfSale.Controller({...})`） |
| `official-pos.css` | 42 KB / 219 条规则 | **官方编译产物里抽出的 `.point-of-sale-app` 全部样式**（连带 @media 内的 15 条） |

> **许可**（一句话说清，不拦施工）：ERPNext 是 **GPLv3**，本 App 目前是 MIT。这些副本按 GPLv3 处理，
> 文件头都标了来源；将来若产品要闭源分发，这棵目录要么按 GPL 走，要么换成"照规格重写"。

### 7.1 对接清单（从搬入的代码里机械提取，共 10 个服务端方法 + 1 个命名空间）

**要先给的命名空间**：`window.erpnext = { PointOfSale: {} }`（8 个类都挂在它下面：
`Controller / ItemSelector / ItemDetails / ItemCart / Payment / NumberPad / PastOrderList / PastOrderSummary`）。

**框架 API**（任何 Frappe 站点都有，不用管）：`frappe.utils.{escape_html, play_sound, is_mac, icon,
to_title_case, print}`、`frappe.ui.{form, make_app_page, keys}`、`frappe.model.{set_value, sync,
with_doctype, clear_doc, make_new_doc_and_get_name, get_value, on}`、`frappe.db.{get_doc, get_value}`。

**要接到我们这边的方法**：

| 官方方法 | 用途 | 接到我们这里 |
| --- | --- | --- |
| `point_of_sale.get_items` + `get_parent_item_group` | 商品列表与商品组过滤 | 我们的 `cashier.api.api_counter_products`（含库存角标） |
| `point_of_sale.get_pos_profile_data` | 收银台设置（仓库、支付方式、开关） | 我们的终端 + 班次（`api_current_shift` / 终端配置） |
| `point_of_sale.get_past_order_list` | 最近订单 | 我们的 `cashier.api.api_list_sales` |
| `point_of_sale.get_customer_recent_transactions` | 客户最近交易 | 我们的销售记录按客户筛（新增一个只读接口） |
| `point_of_sale.set_customer_info` | 改客户联系方式 | **删减**（D3：客户资料不在收银台改） |
| `pos_invoice.get_stock_availability` | 某商品的可用库存 | 我们的库存结存（`current_balance`） |
| `loyalty_program.*` | 忠诚度 | **删减**（D3/D5） |
| `serial_no.auto_fetch_serial_number` | 序列号自动取号 | **删减**（D5） |
| `sales_and_purchase_return.{is_invoice_returnable, get_invoice_item_returned_qty}` | 退货可否与已退数量 | 我们的退货口径（`api_refund_sale` 已有） |
| `frappe.core.doctype.communication.email.make` | 邮件小票 | 框架自带 ✓（D7 只做按钮与交互） |

### 7.2 对接里最要紧的一处：`frm`（这决定"功能对接"怎么做）

搬进来的代码全程操作一个 `frappe.ui.form.Form`（`frm.doc.items / payments / customer / currency /
grand_total / rounded_total / paid_amount / outstanding_amount / additional_discount_percentage …`），
`settings.frm_doctype` 决定用哪张单据。**我们不装 erpnext，没有 POS Invoice**，所以两条路：

| 路 | 做法 | 代价 |
| --- | --- | --- |
| A（建议） | 给它一个**真实表单**：`frappe.ui.form.Form("ERP Sale")`，把我们的字段名接到官方那套名字上（一个薄适配层），提交仍走我们的 `api_create_sale` | 需要一张"官方字段名 → 我们的字段/接口"对照表；不新增 DocType |
| B | 照搬官方字段，在我们自己的 DocType 上加同名栏位 | 会污染我们的数据模型（我们的 `receipt_no`/`sold_at` 与官方的 `name`/`posting_date` 打架） |

**下一步执行顺序**（不用你再确认）：① 写适配层 `desk/pos/pearnly-pos.js`（命名空间 + settings + `frm`
薄适配 + 上表 10 个方法的映射，删减项留空实现）；② 让它挂在一条**临时路由**上跑起来（现页面不动），
用探针逐屏对；③ 一屏对完切一屏，全部对完再把旧 `cashier.js` 下线。

### 7.3 第一次启动结果（2026-09-23，临时路由已可用）

**临时路由**：`/desk/pearnly-cashier?pearnly_pos_next=1` —— 走官方原样搬过来那套；
不带这个参数仍是原来那页（一行没动，演示站随时可用）。加载器与适配层在 `desk/pos/pearnly-pos-adapter.js`。

**已经起来了**：页面上出现了官方那六块容器，顺序与官方一致 ——
`items-selector / item-details-container / customer-cart-container / payment-container / past-order-list /
past-order-summary`，并且 10 列网格（官方 `grid-template-columns: repeat(10, …)`）生效 = 官方 CSS 在跑。

**启动过程中修掉的两处（都记在文件里）**：

1. `official-pos_item_selector.js` 第一行原本是 `import onScan from "onscan.js"`（官方走打包器）。
   我们不用打包器，改成引用**先加载的全局**；`onscan.js`（MIT，almasaeed2010）已随附在 `desk/pos/onscan.js`。
2. `official-point_of_sale.js`（官方那条 `/desk/point-of-sale` 路由的入口）**不加载** ——
   它只做"官方站点注册页面 + 启动控制器"，启动由我们的适配层负责（否则它会去写不存在的
   `frappe.pages["point-of-sale"]`，报 `Cannot set properties of undefined`）。

**还没通的**：商品列表是空的（一次服务端调用失败，日志里是 `frappe/handler.py get_attr(cmd)` 那类
"方法不存在"）。适配层已经覆盖了 `get_items`，所以失败的是**另一处**调用 —— 下一步先给 `frappe.call`
挂一层日志把方法名点出来（这一轮探针超时了，留到下一轮）。

### 7.4 对接进展（第二轮，2026-09-23 晚）

按"挂日志 → 抓到 → 修 → 再跑"的办法推进，逐条解决：

| # | 现象 | 根因（抓到的东西） | 处理 |
| --- | --- | --- | --- |
| 1 | 商品区"没找到物料"、报 `this.get_items(...).then(...).always is not a function` | 官方 `get_items` 返回的是 **jQuery promise**（调用方用 `.always`），我们的覆盖体给了原生 Promise | 覆盖体改成 `$.Deferred()` 并返回 jQuery promise |
| 2 | 同上，即使返回了也仍"没找到物料" | 调用方要的是 `message.items`（**对象里的数组**），我们给的是数组本身 | 解析成 `{ message: { items, filtered_items } }`（与官方服务端同形状） |
| 3 | 合计区货币显示成 `د.إ` | 官方按 `doc.currency` 出符号；我们的 `ERP Sale` 没有这个栏位 → 掉到框架默认货币 | 适配层给客户端文档钉上 `doc.currency = 站点货币`（现在显示 **THB** ✓） |
| 4 | 弹窗"未安装应用程序erpnext：无法获取命令 …get_parent_item_group" | 官方还有若干方法我们没接 | 新增**方法映射垫片**：在适配层包一层 `frappe.call`，把官方方法名统一翻到我们这边或按"删减"给安全值（`get_parent_item_group`→空、`set_customer_info`/忠诚度/序列号/退货判定→安全值、库存可用量→我们的 `api_product_stock`）。**要接新方法只加一行** |
| 5 | 弹窗"没有权限：未授权访问 单据类型" | 客户端某处去读某张 DocType 的元数据，被权限挡下（**哪一个 doctype 还没点到**：CDP 的 `addScriptToEvaluateOnNewDocument` 这台机器不支持，要在适配层里挂日志才能抓到） | **未解决 —— 下一步第一件事** |

**当前状态**：骨架、官方样式、客户端 `ERP Sale` 文档、商品接口、货币都对；**卡在第 5 条权限**上，
所以商品卡还没出。默认收银台路由仍然不受影响。

### 7.5 第三轮：权限那关用"自带元数据"绕过，商品区活了（2026-09-23 晚）

**权限那道闸的真相**：客户端读 `ERP Sale` 的元数据被挡（我们 DocType 权限只给 System Manager，
平时的页面**不读元数据**所以从没撞上）。**没有去改权限**（那会让收银角色多出标准 DocType 入口），
而是在适配层里按框架自己的机制**自带一份字段定义**：

```
locals.DocType["ERP Sale"/"ERP Sale Line"/"ERP Sale Payment"] = { name, fields: [df…], … }
frappe.meta.sync(meta)
```

`frappe.get_meta()` 读的就是 `locals.DocType`，于是 `with_doctype()` 直接短路、**那次会被挡的请求根本不发**，
权限模型一个字没动。这份字段表同时也是"**收银台支持哪些字段**"的唯一清单（要加字段就在这里加）。

**这一轮的结果**：

- 商品区**活了**：40 张商品卡、官方标记（`item-wrapper / item-qty-pill / item-display / item-img /
  item-detail`）、官方图片、库存角标、官方 10 列网格；没有权限弹窗；`with_doctype` 日志只剩 `ERP Sale`。
- 购物车那一栏的官方模板在跑（`折扣` / `Total Quantity` / `净总计` / `总计` / `结账` / `返回购物车`，
  并且 `.taxes-container` 存在 —— 官方那条**税费行**的容器在）。
- **按功能放行的第一处**已写进适配层：官方要求"先选客户才能加货"，我们这边**客户为空 = 散客**，
  用"临时借一个哨兵客户过闸、返回后还原"的办法放行（提交时哨兵会被丢掉）。

**还没通（下一轮第一件事）**：**点商品卡会跳到 `/desk`**（页面被换掉），所以还没能验证"加货 → 行 →
合计 → 键盘"。目前已知：跳转是**客户端**行为（服务端 `tabError Log` 没有新记录），发生在
`on_cart_update()` 那一段里。下一步：在适配层把 `on_cart_update` 前后的 `frappe.set_route` /
页面销毁路径打点，定位是谁把路由改掉的（大概率是官方那条 `make_new_doc_and_get_name` 或
框架的"未知路由"回退），再决定是压住它还是给一条真实表单路由。

**一个操作层面的坑（记下来，别再被骗一次）**：`frappe.require` 加载的 `/assets/...` 走浏览器缓存，
同一个标签页里**重新加载也不一定拿到新文件**（我这一轮就被旧副本骗过一次：代码明明改了，
页面上却是旧行为）。判断办法：文件顶部有 `window.__pos_adapter_v`，读它确认版本；
或者**开一个新标签页**跑。真机发布不受影响（资源带版本号）。

### 7.6 第四轮：**"点商品跳走"根因找到并修掉**（2026-09-23 晚）

**根因（用栈日志抓的，不是猜的）**：官方那个客户选择框指向 **ERPNext 的 `Customer`**（我们没有这张表）。
它一渲染就发一次 `frappe.desk.search.search_link(doctype="Customer")` → **403**；而框架的 403 处理
带 `re_route: true`，所以**关掉那个提示框时会把页面路由重置**（`frappe.set_route("")` → 回 `/desk`）。
栈里写得很清楚：`msg_dialog ... re_route ... frappe.set_route("")`。

**处理**：在适配层把客户选择框整段照官方抄一遍，只把 `options` 换成**我们的 `ERP Customer`**
（我们的客户表对这个角色可搜 —— 实测下拉里就是我们那 8 个客户）。

**同一轮顺手修掉的另外三处**：

| 现象 | 根因 | 处理 |
| --- | --- | --- |
| 点商品的流程被 417 打断、页面被冻住 | 我按官方字段名问客户资料（`email_id`），我们的 `ERP Customer` 里没有这一栏 | 改用我们的 `contact_phone` / `contact_email`（并用我们的字段名做映射） |
| 库存校验那条映射调了一个不存在的接口（`api_product_stock`） | 我先前凭空接的 | 改成用**商品区那一页自带的 `qty_on_hand`**（装货时存进 `available_map`，官方问"还有多少"就答它）；落单时服务端仍会再校验 |
| 商品卡上的可用量数字不对 | 我映射的字段名也错了（我们叫 `qty_on_hand`，不是 `available_qty`） | 改成按 `qty_on_hand` 映射成官方的 `actual_qty` |

**验证到哪一步**：客户下拉出的是**我们的客户**；页面**不再跳走**；点商品不再报 417/403；
货币那处又冒出来一次 `د.إ`，已改成**死钉站点货币**（`frappe.boot.sysdefaults.currency`）。

**还剩**：加货那一步在浏览器里还是没走完（最后一测里点完商品车还是 0 行 —— 当时刚用 Esc 关掉客户下拉，
点可能落在浮层上；下一轮先用**干净的手顺**再走一遍：选客户 → 点商品 → 读行/合计/键盘，
若仍不进货就看 `__pos_call_log` 与 `on_cart_update` 的返回）。

### 7.7 第五轮：**加货这一整条通了**（2026-09-23 深夜）

**真凶（两个，都是"我以为接上了、其实没有"）**：

1. **`doc.items` 与 `doc.lines` 不是同一个数组**。我原来只是"赋一次值"把 `items` 指向 `lines`，
   而框架刷新单据时会把子表换掉 —— 结果行落在 `lines`、官方读 `items` 读到 0，
   表现就是**点商品不加货**（`on_cart_update` 返回 ok、界面纹丝不动）。
   → 改成**属性别名**（`Object.defineProperty(doc, "items", {get: () => this.lines || (this.lines = []), set})`），
   永远是同一个数组。实测：`add_child` 之后 `items: 1 / lines: 1 / sameArray: true`。
2. **净额/总计/物料数是服务端算的**（官方在 POS Invoice 上 `calculate_taxes_and_totals`）。
   我们这张客户端 `ERP Sale` 没那一步，所以行出来了、数字还是 0。
   → 在适配层按**我们的口径**先算（数量、净额 = Σ 数量×单价、整单折扣、总计），再交给官方那套渲染。

**现在能跑通的（演示站 `?pearnly_pos_next=1`，真点鼠标、不是脚本模拟）**：

| 步 | 结果 |
| --- | --- |
| 进页面 | 官方骨架 + 官方 10 列网格 + 40 张商品卡（官方标记、图片、库存角标） |
| 点商品卡 | **真的加进货**：购物车出官方表头（物料 / 数量 / 金额）+ 行「2 瓶 · THB 100.00」 |
| 合计 | 官方的 `Total Quantity 2 / 净总计 THB 200.00 / 总计 THB 200.00`，**折扣格**在，**结账**是主色 |
| 0 价商品 | 官方那句「Price is not set for the item.」照原样拦下（与我们的零价闸一致） |
| 客户 | 下拉里是**我们的客户**（CUS-00213…）；点商品不会再跳走 |

**已知的两个小尾巴（下一轮顺手）**：① 客户那一格一进页面就是"空输入框 + 下拉张开"（官方是显示
**默认客户卡**）→ 按台账 D2，进来应当画**散客卡**，别让下拉盖住购物车；② 税费行容器在但还没有明细（D1）。

## 8. 顺带修的全局项：**货币一律出符号（฿），不出代码（THB）**（2026-09-23）

用户口径："全站货币改为泰铢的符号，不要字母。"

**根因**：框架取符号走 `format_currency → get_currency_symbol → frappe.model.get_value(":Currency", <代码>, "symbol")`，
取不到就**退回货币代码本身**（所以到处是 "THB 62.00"）。客户端缓存 `locals[":Currency"]` 在我们站点一直是空的。

**改法**（都在公共层 `apps/pearnly_core/pearnly_core/public/desk/product.js`，一处改、全站生效）：

1. **把符号喂进框架自己的缓存**：`locals[":Currency"][THB] = { symbol: "฿" }`，并顺手用
   `frappe.db.get_value("Currency", code, "symbol")` 读一次真实配置覆盖（客户改过就用客户的）；
   **这一句在 2026-09-24 被撤了** —— 前端读 `Currency` 表会弹 403「无权限操作货币」，
   符号改成从**服务端启动数据**下发。见下面第 9 节。
2. **给 `window.format_currency` 加一层兜底**：万一首屏那一瞬还没喂到，框架返回 "…THB 62.00" 时
   也换成符号。两层都在同一文件，商品 / 库存 / 采购 / 销售 / 收银（含搬过来的官方收银台）
   以及所有原生表格一起生效。

**验证（演示站实测）**：

| 页面 | 结果 |
| --- | --- |
| 物料列表 | 售价/参考成本列全是 `฿ 100`、`฿ 80`…；页面里 **没有任何 `THB <数字>`** |
| 收银台（对照路由） | 合计 `净总计 ฿ 200.00 / 总计 ฿ 200.00`，同样没有代码形态 |
| 直接问框架 | `format_currency(62, "THB", 2)` → **`฿ 62.00`** |

## 9. 上一批留下的三个真问题（2026-09-24，均已修 + 演示站实测）

### 9.1 「点商品加进货、购物车一行都不画」——真凶在库存回参的形状

现象一直是"单据里已经有行了（`doc.items` 有 1 行），购物车 DOM 还是 `No items in cart`"，
而且**控制台什么都没有**。原因是官方把错误吞了：`on_cart_update()` 里那段
`try { … } catch (error) { console.log(error); }`，`console.log` 在我们的标签页里读不到。

把 `console.log` 临时接管之后，原话是：

```
TypeError: Cannot read properties of undefined (reading 'toString')
  at PearnlyController.check_stock_availability (official-pos_controller.js:807)
  at async PearnlyController.on_cart_update (official-pos_controller.js:708)
```

官方 `check_stock_availability()` 读的是**三元数组**：

| 位 | 官方语义 | 官方拿它做什么 |
| --- | --- | --- |
| `resp[0]` | 可用量 | 显示 `Available Qty`；`available_qty < qty_needed` 时拦单 |
| `resp[1]` | 是否库存商品 | 为假则"不是库存商品"直接放行 |
| `resp[2]` | 是否允许负库存 | 为**真**则 `if (is_negative_stock_allowed) return;` 提前返回 |

我们早先只回了一个**数字** → `resp[2]` 是 `undefined` → `available_qty.toString()` 抛错 →
被官方那个空 `catch` 吞掉 → 后面那句 `this.update_cart_html(item_row)` 根本执行不到。

**改法**：映射回 `[可用量, 0, 1]`。
第三位给**真**不是为了让闸门松掉，而是它本来就是我们既有口径 ——
`inventory/stock.py` 的 `apply_movement(allow_negative=True)` 注释写着"允许超卖，告警在上层"，
收银台不因库存拦单；官方那位读到真就提前返回，跟我们的行为对上。
第二位给假（我们的商品不是"免盘点"的非库存商品），可用量只用于界面显示。

**实测**：点第一张卡 → 购物车出 `1 瓶 · ฿ 100.00`，合计 `Total Quantity 1 / 净总计 ฿ 100.00 / 总计 ฿ 100.00`，
控制台零报错、无弹窗。

### 9.2 商品卡上的钱是 `د.إ`（迪拉姆），不是 `฿`

**根因**（两个坑叠在一起）：

1. **站点根本没有全局默认货币**：框架取符号那条路是
   `format_currency(v, currency)` → `get_currency_symbol(currency)`，**不传币种时**读
   `frappe.boot.sysdefaults.currency`，而这个键来自 `DefaultValue` 表
   （`frappe.defaults.get_defaults()`）。我们自己供的站点初始化时**没写这一行**
   （`erpnext` 的安装流程会写，我们不装 `erpnext`）—— 对照机 `business.localhost` 上有
   `currency = THB`（parent `__default`），我们演示站上没有。
2. 链子断在后面的样子：`get_currency_symbol(undefined)` →
   `frappe.model.get_value(":Currency", undefined, "symbol")` **命中缓存里的第一条货币** →
   本机抓到的是 `AED`，于是页面上的金额全变成 `د.إ 100.00`。

**改法**：

| 层 | 落点 | 做什么 |
| --- | --- | --- |
| 站点数据（根治） | `pearnly_core/patches/v0_0_4_site_default_currency.py` + `patches.txt` | 用原生补丁补上全局默认货币；取值顺序 System Settings 单值 → 采购设置的本位币 → THB；已经有了就一个字不动 |
| 启动数据 | `pearnly_core/desk/currency.py`（新 `boot_session`） | 下发 `boot.pearnly_currency = {code, symbol}`，并给缺 `currency` 的 boot 兜一层 |
| 前端 | `pearnly_core/public/desk/product.js` | 预热符号改**只认服务端那一份**；boot 里没币种时钉成 THB（防未迁移的老站点继续花） |
| 收银台适配层 | `pearnly-pos-adapter.js` | 商品卡带上 `currency`（官方价签读 `item.currency`） |

### 9.3 顺手揪出的第三个：403 弹窗「无权限操作货币」

把符号预热真正跑起来之后（以前代码提前返回，这次请求根本没发过），
`product.js` 里那句 `frappe.db.get_value("Currency", code, "symbol")` 会**弹一个 403 弹窗**：

```
信息｜无权限操作货币
```

`Currency` 这张表的权限在本站是收紧的，前端读它必然撞墙。**已删掉这次前端读取**，
符号一律走 9.2 的服务端启动数据（服务端 `frappe.db.get_value("Currency", "THB", "symbol")`
回读是 `฿`，不受用户权限限制）。

### 9.4 这一批的验证（演示站真点鼠标，不是脚本模拟）

| 项 | 结果 |
| --- | --- |
| 点商品卡 | 购物车 `1 瓶 · ฿ 100.00`；合计三行正确；**0 报错、0 弹窗** |
| 商品卡 | `-14 水 ฿ 100 / 瓶`；库存角标这次真的带颜色带数字（`red -14`） |
| `frappe.boot.pearnly_currency` | `{code: "THB", symbol: "฿"}` |
| `format_currency(62, undefined, 2)` | `฿ 62.00`（改前是 `د.إ 62.00`） |
| 物料列表 | 售价/参考成本全是 `฿`；全页 **无 `THB <数字>`、无 `د.إ`、无弹窗** |
| `scripts/check-architecture.py` | `PASS (0 violations)` |

## 10. 订单折扣那一格：填了百分比合计不动（2026-09-24，已修 + 实测）

**官方这一格长什么样**（照搬后与官方逐字一致）：没折扣时是虚线框 + 折扣图标 + `Add Discount`；
填了就是 `附加项 12% 已应用折扣`（官方原文 `Additional 12% discount applied`，
标记 `<div class="edit-discount-btn">`）。**是内联一格，不是弹窗** —— 你 2026-09-23 说的那条，
搬过来之后就自动对了。

**但它填完不重算**：实测填 12% 之后 `doc.additional_discount_percentage = 12`，
而 `discount_amount` 还是 0、`grand_total` 还是 100。

**根因**：官方这次改动**自己不调重算** —— 它只 `frappe.model.set_value(…)` 把百分比写进单据，
然后等 **`refresh-fields` 事件**（`official-pos_item_cart.js` 的 `attach_refresh_field_event`，
里面才是"重画购物车 + 重算合计"）。而那个事件由**表单 Layout 的刷新**发出来
（`frappe/form/layout.js:373`）。我们这张客户端 `ERP Sale` 没有 Layout，事件永远不会响。

**改法**（适配层，两处）：

1. `PearnlyItemCart.hide_discount_control(discount)` 覆盖：走完官方那段之后按我们自己的口径重算一次合计；
2. `update_totals_section(frm)` 允许**不带参数**调用（官方就是这么调的），
   并且行金额照 ERPNext 口径算：折% 优先，没有百分比才用折额
   （`line_amount(row)`）—— 不这样，键盘上那个"折扣"改的是**行**折扣，合计读不出来。

> 踩坑记录：第一版只加了 ①，没意识到官方调用时不传 `frm`，我们那段重算被 `if (frm && …)` 整个跳过，
> 表现和没改一样（`grand 100`）。补上 ② 之后 `grand 88`。

**实测（演示站真点鼠标）**：填 12% → 界面 `净总计 ฿ 100.00 / 总计 ฿ 88.00`，
单据 `discount_amount 12 / grand_total 88`；0 报错、0 弹窗（除了下面那条）。

## 11. 「结账 → 付款屏 → 完成订单」整条打通（2026-09-24，已做 + 实测）

用户口径："**不存草稿，但是过账应该发生在最后一环节吧**。" —— 按这条做的。

### 11.1 先修断点：点「结账」弹的是我们自己的必填校验

官方 `save_and_checkout()`（`official-pos_controller.js:895`）会先 `this.frm.save(…)`
**存一张草稿单**，存成功才切付款屏；它存的是我们这张 `ERP Sale`，而客户端文档上带的是官方字段名
（行上是 `rate`、整单没有 `sold_at`），于是被框架的必填校验挡下，弹的才是那句
"ERP Sale 请填写必填字段：销售时间"。

**改法**：覆盖 `save_and_checkout()`，**跳过保存、直接切付款屏**。
单据由 `api_create_sale` 在「完成订单」时**一次落定**（写单 + 出库存 + 提交）——
我们产品里本来也没有"草稿单"这个状态。

### 11.2 付款屏本身：方式行、右下角三格、三列键盘

官方付款屏要三样东西，都是我们这边缺的：

| 官方的输入 | 谁给 | 我们怎么接 |
| --- | --- | --- |
| 方式行 = `doc.payments` 子表 | 服务端 `set_pos_data` 从 POS Profile 带出 | `seed_payments()`：按我们自己的方式表给四行（现金 / 刷卡 / 扫码（PromptPay）/ 转账），**与 `cashier/contracts.py` 的 `PAYMENT_METHODS` 同一集合** |
| 右下角 = `doc.paid_amount / grand_total / change_amount` | 服务端算好 | `recalc_payment_amounts()`：实收 = Σ 各方式金额；找零只可能从现金出（与 `contracts.settle()` 同一口径，服务端还会再核一遍） |
| `frm.cscript.calculate_outstanding_amount()` | POS Invoice 的 cscript | 给客户端单据挂一个**同名实现**（`get_new_frm()` 里），官方那句调用一个字没改 |

**方式名为什么不能直接照官方那样当 CSS 类名**：官方 `sanitize_mode_of_payment()` 会把名字里的
非字母数字**剔掉**再小写。我们的方式名是**本地化标签**（泰语为主，`p.texts` 那张表），
照那样剔会全变成空串 —— 几种方式撞成同一个类名，`this[`${mode}_control`]` 也互相覆盖。
所以子类里覆盖 `sanitize_mode_of_payment()`，按方式表还原成 ASCII key（`cash/card/promptpay/transfer`）；
界面上的字仍是本地化标签，官方那句 `${escape_html(p.mode_of_payment)}` 一个字没动。

### 11.3 提交：官方那句 `this.events.submit_invoice()` 一个字没改

官方把"提交"写在 **controller 传给 payment 的 events** 里（`init_payments()` 中那句
`submit_invoice: () => this.frm.savesubmit()`），那是 POS Invoice 的路。
我们覆盖 `init_payments()`，`super` 之后把 `this.payment.events.submit_invoice` 换成自己的
`submit_our_sale()` —— 换掉的是**我们这一侧的事件实现**，官方 payment 里那句调用原样。

`submit_our_sale()` 把客户端文档翻成 `api_create_sale` 的入参：行给
`product / qty / unit / unit_price / discount`（折扣按**金额**，与界面那套算法同源）、
整单给 `discount_total`（界面那格是百分比，我们已算成金额）、付款给 `{method, amount}`。

### 11.4 顺手修的两处 `undefined`（单据汇总屏）

官方的单据汇总在提交后立刻渲染（`toggle_submitted_invoice_summary(true)`），抓到两处：

| 症状 | 根因 | 改法 |
| --- | --- | --- |
| 行上 `(undefined% off)` | 官方那句 `item_data.rate !== item_data.price_list_rate` 是**严格比较**，我们行上的 `rate` 是商品卡 `data-rate` 来的 **DOM 字符串**（`"100" !== 100`）→ 被判成"这行有折扣" | `on_cart_update()` 收尾把 `rate / price_list_rate / discount_percentage / discount_amount` 一律转成数字 |
| 状态徽章是一个 `undefined` | 徽章读 `doc.status`，官方 POS Invoice 是 `Paid/Consolidated…`，我们客户端单据没这一位 | `set_pos_profile_data()` 里钉 `status = "Paid"`（"当场付清"，也是销售记录里收款状态那一列的同一个词） |

另外补了一个映射：汇总屏问客户邮箱走 `frappe.db.get_value("Customer", …)`，我们没有那张表 →
适配层翻成 `ERP Customer` / `contact_email`，并按官方那个字段名回；散客直接答空
（不能让 `message` 是 `undefined`，官方那句读法会当场炸）。

### 11.5 实测（演示站真点鼠标，两笔都真落单）

| 步 | 结果 |
| --- | --- |
| 点商品 → 结账 | **不再弹任何校验**；付款屏出现四种方式，`data-mode` 分别是 `cash/card/promptpay/transfer` |
| 付款屏右下角 | `总计 ฿ 88.00 / 付款金额 ฿ 88.00 / 剩余金额 ฿ 0.00`（现金那格按官方 `set_grand_total_to_default_mop` 预填应收） |
| 点「完成订单」 | 绿条 `POS invoice SI2569-000053 created successfully`；汇总屏 `已付款`、`物料 1 瓶 ฿ 100.00`、`折扣金额 (12 %) ฿ 12.00`、`总计 ฿ 88.00`、`付款 现金 ฿ 88.00` + 打印收据 / Email Receipt / 新订单；**0 报错 0 弹窗** |
| 过账只在最后一环节 | 全程 0 写入；点完成订单那一下：`ERP Sale` 30→31→32、库存流水 204→205→206、`PRD-00597` 在 `WH-00101` 结存 100→99→98 |
| 服务端回读 | `SALE-2026-00655`：`subtotal 100 / discount_total 12 / grand_total 88 / paid_total 88 / status completed`；付款行 `cash 88`；行 `PRD-00597 ×1 @100` |

**未做的（下一个包）**：付款屏那行 `Update Additional Information` 还是英文（附加信息弹窗，
台账 D4）；`+ Add Payment Method`（官方自己注掉了）；打印收据 / Email Receipt / 新订单 三颗按钮
只有"新订单"那条路是官方原生的，打印与邮件还没接（台账 D7）。

## 12. 声音 / 语言 / 图片 / 图标：逐项盘点（2026-09-24，已补齐 + 实测）

用户口径："对应的声音那些语言图片图标的显示，补充完整。"

### 12.1 声音：本来就是完整的（本轮零改动，但这次有证据）

页面回读：`<audio id="sound-…">` 共 **8 个** —— frappe 自带七个（submit / error / cancel /
delete / click / alert / email）+ 我们自己钩子里那一条 `numpad-touch`（`pearnly_core/hooks.py`
的 `sounds`，文件在我们 App 里，30ms / 音量 0.8，与上游同一份）；
`frappe.boot.user.mute_sounds = 0`；官方 POS 会用到的五种音（error / submit / cancel / email /
numpad-touch）全部 `readyState 4`、能播。**官方代码里 `play_sound(...)` 的名字与我们的节点一一对上，
没有一条是取不到节点的。**

### 12.2 语言：官方那 89 条文案，我们术语表里**一条都没有**

**为什么一直没发现**：`scripts/check-i18n.py` 的 `SKIP_DIRS` 里有 `public` ——
所以 `public/desk/` 下**全部** Desk 脚本（不只搬来的官方文件，我们自己的也是）从来没被这台机器扫过。
英文就是这样漏出来的（收款屏的 `Update Additional Information`、购物车的 `Item Cart` /
`No items in cart` / `Total Quantity`、汇总屏的 `Sold by` / `Email Receipt`…）。

**改法**：把官方 POS 用到的 **89 条** `__()` 文案全部写进术语表
（`pearnly_erp_portal/i18n/messages.tsv`，中英泰日四语、泰语为主），重新生成翻译包、清缓存。
顺手把适配层自己那两条**中文 msgid** 改成英文（规矩是"msgid 一律英文"：中文 msgid 等于
"这门语言永远是中文"）：`The cashier assets did not load (official-*.js).` /
`Open a shift on the cashier page first.`。

其中约二十条属于**已删减功能**的文案（忠诚度、序列号/批号、开班交班单、退货、优惠券）——
它们在我们这两条链路上渲染不到，照样补上：术语表是"词"的表，不是"功能"的表，
补上只防英文漏出，不代表我们支持那些功能。

**实测（页面回读 `__()` 与实际 DOM）**：`Item Cart → 购物车`、`No items in cart → 购物车是空的`、
`Total Quantity → 总数量`、`Sold by → 收银员`、`Email Receipt → 邮件发送收据`、
`Update Additional Information → 更新附加信息`、`Change Amount → 找零`、`Totals → 合计`、
`Search by customer name, phone, email. → 按客户名称、电话、邮箱搜索`、
`Price is not set for the item. → 该物料没有设置售价`、`Remark → 备注`、`Refresh → 刷新`。
收银台里"仍缺词条"自检：**0 条**。

### 12.3 图片与名字：购物车行、明细面板本来是**空的**

**根因**：官方行上的 `item_name / image / uom` 是**服务端** `set_item_details` 补的
（官方 POS Invoice 那边由服务端带回），我们这张客户端单据没有那一步。实测明细面板那格是
`<div class="item-abbr"></div>`（**空 div**）、购物车行只有数量与金额、没有物料名。

**两处细节**（都是实测才看见的）：

1. 官方点击商品时传给 `on_cart_update` 的 `item` **只有** `item_code / uom / rate /
   stock_uom / batch_no / serial_no`（见官方 `item_selector` 那次 `item_selected`）——
   **没有名字与图片**。所以适配层另存一份 `item_meta`（从商品卡那份数据来）。
2. 官方在 `super.on_cart_update()` 里**已经画过一次**购物车，那一笔画的是还没补值的行；
   补完必须**再画一次**，否则界面停在那张"没名字的行"上。

**实测**：购物车行 `水 | 1 瓶 | ฿ 100.00`（带商品图 `/private/files/IMG_4550.JPG`）；
明细面板 `水` + 图片 `1024x768` + `฿ 100.00`。没有图片的商品仍走官方的缩写格（39/40 张卡如此，
与官方同一套判断）。

### 12.4 图标：没有缺口

官方 POS 只用 `frappe.utils.icon("close", …)` 两处，都是框架自带图标（sprite 里 symbol 在、
可见的框架图标量到 16×16）。POS 里那 7 个 `.es-icon` 落在**隐藏面板**里（量到 0×0），属正常。

### 12.5 顺带删掉两个"没承接的入口"

官方页头菜单挂着两项：**打开表单视图**（要打开这张客户端单据的服务端表单，我们没有那种表单）与
**关闭收银台**（要开一张 `POS Closing Entry`；我们的交班是自己的 `ERP Shift` 页面）。
两项各自还绑着快捷键（⌘+F / ⇧+⌘+C），所以覆盖 `prepare_menu()` 整段清空。实测页头只剩
我们自己的 `最近订单 / 新单`。

### 12.6 这一轮发现、留给下一包的两件事

1. **最近订单面板是空的**：官方 `past_order_list` 调的是
   `erpnext.selling.page.point_of_sale.point_of_sale.get_past_order_list`，我们适配层的映射表里
   **没有这一条** → 列表 0 行（面板本身、文案、筛选都在，`单据状态/搜索` 已译）。
   要接我们的销售记录接口（`api_list_sales`）并做一层字段映射。
   （面板里那两个 `undefined` 是 frappe 控件的 tooltip 节点，`opacity: 0`，悬停才会显形，
   不算显示问题 —— 但值得顺手把这两格的 `description` 补上。）
2. **`check-i18n.py` 的盲区**：它跳过 `public/`。本轮之前 `public/desk/` 下有 **135 条**
   `__()` 缺词条（收银台 89 条 + 我们自己的页面 46 条）。收银台这 89 条已补；
   其余 46 条（商品 / 采购 / 销售 / 盘点 / 团队等页面）**这一轮没动**，是下一批要清的账；
   要不要把这台机器扩到 `public/desk/`（并要求零缺口），需要你拍一下。

## 13. 扫码接我们现成的 + 右上角那三个点按"我们之前"摆（2026-09-24，已做 + 实测）

用户口径："**扫码可以用我们现成的呗，还有右上角那三个点。。。的内容，我们之前是什么，
如果是主按钮就不要放在。。。里面**，你每次做完不要关预览，我要看。"

### 13.1 我们之前那页的页面动作是什么（照着它摆）

`desk/cashier.js` 里那套（`paint_actions()`）就是我们的口径：

| 位置 | 我们之前的做法 |
| --- | --- |
| 那三个点（菜单） | **交班** |
| 主按钮 | 有班次 **新单**／没班次 **开班** |
| 次按钮 | **最近订单 / 隐藏最近订单** |
| 指示器 | 有班次：`班次 14 · 开班 2026-09-23 22:33 · 应有现金 ฿ 988`（绿）／没班次：`尚未开班`（橙） |

官方摆的是：菜单「打开表单视图 / 关闭收银台」（两项在我们这边都没有落点，一项要打开这张
客户端单据的服务端表单、一项要开 `POS Closing Entry`），主按钮「New Invoice」、次按钮
「Recent Orders」、指示器是**收银台名**。所以：菜单换成我们的「交班」，
**开班搬到主按钮上**（它是没班次时唯一该点的东西），指示器改回班次状态。

### 13.2 开班 / 交班弹窗：抽成一份共用（136 行 → 6 行）

那两个弹窗原先写在 `cashier.js` 那个大闭包里，而那个闭包在**对照路由分支里提前 return**，
函数根本没定义 —— 搬过来的官方收银台要「交班」就得有第二份。所以抽成
`desk/pos/pearnly-pos-shift.js`：

```
frappe.pearnly.pos_shift.open({ t, on_done })    // 开班
frappe.pearnly.pos_shift.close({ t, shift, on_done })  // 交班
```

`cashier.js` 里那一整段（币种字段、对账表、两个弹窗，136 行）删掉，只留两行转调；
两条路调**同一份**。实测两边都能开出交班弹窗（标题「交班」+ 笔数/净总计/退款笔数/找零合计
+ 对账表 `现金 / 银行卡 / PromptPay / 转账` 四行）。

### 13.3 扫码：用框架自己那颗相机按钮 + 扫到就进车

官方那个搜索框只认 `onscan.js` 那种键盘楔式扫码枪。我们旧页面的做法是给控件 `options: "Barcode"` ——
那是**框架自己的开关**（`ControlData.setup_barcode_field()` 会挂上 `icon-scan-barcode` 那颗
`.link-btn`，点开官方相机对话框）。适配层不改官方建控件的代码，只在它建好之后按框架那条路补上，
并接管"扫到之后干什么"：**唯一命中直接进购物车**（我们之前的行为），一条都没有就照实说
（沿用术语表里那一句 `No product for this code: {0}`）。

实测：点相机 → 输入条码 `8850011100105` → 商品卡收到一张、**直接进车**（`水 | 1 瓶 | ฿ 100.00`），
搜索框自动清空、商品区恢复 40 张。

### 13.4 顺手改对的三个字段路径（原来靠服务端默认值兜着）

`api_current_shift` 回的是**当班汇总**（`{shift: {...}, balances, sales_count, …}`），
而适配层一直按平铺读：

| 以前 | 现在 | 影响 |
| --- | --- | --- |
| `shift.terminal` | `shift.shift.terminal` | `pos_profile` 以前是 `undefined`，API 调用靠"取默认收银台"兜着 |
| `shift.opened_at` | `shift.shift.opened_at` | 开班时间以前是空 |
| `shift.company` / `terminals.terminals` | 公司取终端那家 / 接口回的是**数组** | `settings.warehouse`、`company` 以前是空串 |

### 13.5 「新单」不再问"要不要保存"

我们给的档位 `action_on_new_invoice: "Always Ask"` 会让官方弹"有未保存的改动，要保存这张单据吗？"，
点"是"就走 `frm.save()` —— 那条路在我们这边必然撞必填校验（客户端文档带的是官方字段名），
而我们根本没有"草稿单"这种状态。改成官方那条"直接开新单、不存"的档位（我们旧页面「新单」就是这个行为）。

### 13.6 实测

| 项 | 结果 |
| --- | --- |
| 对照路由菜单 | 那三个点里只有 **交班**；主按钮 **新单**；次按钮 **最近订单** |
| 对照路由指示器 | `班次 14 · 开班 2026-09-23 22:33 · 应有现金 ฿ 988` |
| 没班次分支 | 指示器 `尚未开班`、主按钮变 **开班**（只置空状态模拟，没写数据） |
| 扫码 | 唯一命中直接进车；搜索框自动清空 |
| 旧页面（默认路由） | 照旧可用：菜单 **交班**、指示器班次状态、商品区 40 张、交班弹窗正常 |
| 两条路的交班弹窗 | 同一份，字段与对账表一致 |

> 用户口径："**每次做完不要关预览**" —— 收尾时把对照路由与旧页面两张标签留着
> （`markDeliverable`），不再自动关。

## 14. 声音：接漏了三处（2026-09-24，已修 + 逐动作实测）

用户口径："你声音接漏了吧"。**对了** —— 上一轮我只核了"节点在不在、声音名对不对"，
没核"每个动作到底有没有响"。这一轮在页面上装探针（包一层 `frappe.utils.play_sound` 记下来），
把每个动作都点一遍，漏的三处全露出来了。

### 14.1 漏在哪（根因都不是"没写 play_sound"，而是**官方那两声来自我们删掉的那两步**）

| 动作 | 官方那一声从哪来 | 我们为什么没有 |
| --- | --- | --- |
| 点「结账」 | 官方 `.checkout-btn` → `save_and_checkout()` → `frm.save()` → frappe `form.js` 存成功后播 **`click`**（音量 0.05） | 我们按你的口径**不存草稿**，那一步没了 → 声音也没了 |
| 点「完成订单」 | 官方 `frm.savesubmit()` → frappe `form.js` 提交成功后播 **`submit`** | 我们换成 `api_create_sale`，那一步没了 → 声音也没了 |
| 相机扫码命中 / 落空 | 官方在 **"回车"那条路**上播 `submit` / `error`（`item_selector` 的 `frappe.ui.keys.on("enter")`）；相机是 `set_value()` 回填，**没有回车** | 相机这条路我们自己接的，没补这两声 |

**改法**：都在适配层"补同一颗音、同一时刻"——结账在切屏前、完成订单在成功之后绿条之前、
扫码在官方自动进车那一步（`add_filtered_item_to_cart()` 之后）与落空那一步。

### 14.2 顺带修掉一个我自己引进的毛病：扫码**进两瓶**

官方 `filter_items()` 里**本来就有"唯一命中直接进车"**：
`auto_add_item && 搜索框有值 && this.items.length == 1 → add_filtered_item_to_cart()`，
而我们的设置在 `auto_add_item_to_cart: 1`。我上一轮自己又点了一次卡 ——
于是**同一次扫码进两瓶**（实测：一次输入让车里从 1 瓶变 2 瓶）。
现在加货一律走官方那条路，我们只补官方没有的：**相机按钮**与**声音**。

### 14.3 逐动作实测（探针记录，不是"看着像"）

| 动作 | 响什么 | 说明 |
| --- | --- | --- |
| 点商品卡 | **静音** | 官方就是静音（旧注释里"加购不响"这次复现了） |
| 手输搜索、唯一命中自动进车 | **静音** | 官方这条自动加货路本来就是静的；要响的是"回车"那条（我们没动） |
| 相机扫码命中 | **`submit`** | 补的（我们旧页面也是这一声） |
| 相机扫码落空 | **`error`** + 提示「没找到这个码对应的物料：0000000000」 | 补的（沿用术语表 `trade.scanNotFound`） |
| 购物车键盘没先选目标 | **`error`** | 官方行为 |
| 结账 | **`click`** | 补的（0.05 音量，与官方同一声） |
| 付款小键盘 | **`numpad-touch`** | 官方行为 |
| 完成订单·没收钱 | **`error`** + 提示「没有收款不能提交」 | 官方行为 |
| 完成订单·成功 | **`submit`** | 补的（绿条 `SI2569-000059` 之前那一声） |
| 客户资料更新成功 | **`submit`** | 官方行为 |

**声音节点的证据**（上一轮已核，这轮复述）：8 个 `<audio id="sound-…">` 齐（frappe 七个 +
我们钩子里那条 `numpad-touch`），`mute_sounds = 0`，用到的名字全部 `readyState 4`。

## 15. 2026-09-24 第二批：收据按钮实测、开班到仓、最近订单接上、删物料组、翻译补全

用户这一轮的口径（原话要点）：按钮留我们之前的；开班弹窗那颗箭头会把人带走；
收银台到底有没有用、没用就删；**提示语全局翻译完整**；**物料组我们是没有的，删掉**；
**最近订单我们是支持的呀**。

### 15.1 「收据」那三颗按钮：端到端实测（不是只画出来）

点「最近订单」→ 点一行 → 汇总屏底下是 **打印收据 / 退货退款 / 作废 / 新订单**
（没有官方的「邮件发送收据」—— 我们没做那个功能）。逐个动作实测：

| 动作 | 结果 |
| --- | --- |
| 打印收据 | `p.pos_receipt.print` 收到 **1 次**调用，开 `/printview?doctype=ERP Sale&name=SALE-2026-00681`（内部单号） |
| 退货退款 | 真出了一张退款单 `SALE-2026-00683 / SI2569-000063`，`return_against=SALE-2026-00681`，金额 ฿100 |
| 作废 | 对那张退款单作废 → 提示「已作废 1 张」，汇总屏立刻变「已作废」，按钮只剩「新订单」 |
| 新订单 | 清车回商品区 |

**踩到三处**（都已修）：

1. **两个号**：列表与汇总屏要显示**小票号**（`SI2569-000060`），取数要**内部单号**（`SALE-2026-00673`）。
   官方那个渲染函数两处都用 `invoice.name` —— 不改官方代码，改成"渲染时给小票号、
   追加到列表前把 `data-invoice-name` 换回内部单号"（`p.pos_receipt.print` 与
   `api_get_sale` 都按内部单号走）。
2. **点一次弹两个打印页**：官方把 `.print-btn` 的动作用**委托**绑在外层容器上，
   我们自己那颗绑在按钮区 → 两个都跑。构造里 `off("click", ".print-btn")` 摘掉官方那一条
   （实测容器上只剩官方别的委托，没有 `.print-btn`）。
3. **已作废的单底下还画着「作废」**：官方状态词（`Paid`/`Return`/`Voided`，给色点用）与
   我们自己的状态（`completed`/`void`，给按钮判据用）混着判了。分成两个字段
   （`status` 给渲染、`pearnly_status` 给按钮），与旧页面同一句 `doc.status === "completed"`。

### 15.2 开班弹窗：箭头没了，并且当场说清"哪家公司、哪个仓"

收银台那一格是**原生 `Select`**（不是 Link，也就没有那颗跳走的箭头），选项写成
`Counter 1 · Purchase check 4bb0f9fc`；下面两格只读显示 **公司**（抬头，不是 `CO-00552`）与
**仓库**（名字，不是 `WH-00174`），各带一句说明。实测：`.modal.show .link-btn .btn-open` = **0**。

顺带修了一个真 bug：`tblERP POS Terminal.warehouse` **全站为 NULL**（终端没配仓），
所以"仓库"那格以前是空的。现在服务端 `list_terminals` 多回两个**只读**字段
`effective_warehouse` / `effective_warehouse_name`（自己配了用自己，没配就是这家公司的默认仓，
与卖货那条路同一口径；只读、不建）。站上两台终端都落在 `WH-00174`。

### 15.3 POS 卖货"单据一个仓、行另一个仓"（**本轮最重要的一个真 bug**）

实测一笔：单据 `SALE-2026-00681` 的 `warehouse = WH-00174`，而它的行 `warehouse = WH-00101`
（= 该商品自己的 `default_warehouse`），库存是从**行**那个仓出的。
也就是：商品区显示的可用量按"收银台的仓"算、开班弹窗也这么写，**实际扣的却是商品的默认仓** ——
"看得到的库存"和"扣掉的库存"不是一个地方。

根因：`cashier/sales.py` 里 `positions.prepare(line, warehouse or terminal_doc.warehouse)` 的
兜底顺序是"行 → 终端 → **商品默认仓**"，而本租户终端 `warehouse` 是 NULL → 商品默认仓胜出；
同一函数下面那句 `warehouses.required(...)` 才解析出公司默认仓（= 终端有效仓）。

改法：**先把这台收银台的有效仓定死，再铺行** ——
`warehouse_id = warehouses.required(warehouse or terminal_doc.warehouse)`，
`lines = [positions.prepare(line, warehouse_id) …]`。行、单据、界面三处从此同一个仓。
实测新单 `SALE-2026-00685 / SI2569-000064`：`doc_wh = line_wh = WH-00174` ✓。
（改动前那两张单的历史行不动，用户口径是本地痕迹不用恢复。）

### 15.4 「最近订单」接上我们自己的销售记录

以前点开是 `未安装应用程序 erpnext / 无法获取命令 …get_past_order_list`。现在：

- 列表 = **`api_list_sales`**（收银记录那页同一个接口），一屏 20 条、按 `sold_at desc`；
- 状态那一格换成我们这三档：**全部 / 已完成 / 已作废**，默认「全部」
  （官方默认筛"草稿"，而我们**不存草稿**，照它那个默认一打开就是空的）；
- 搜索框口径与占位文案一致：单号、内部单号、**客户抬头**（后者本轮补进 `list_sales` 的 `or_filters`）；
- 点一行 → 走 `api_get_sale`，把我们的单据**摆成官方那套字段形状**喂给官方的汇总渲染
  （`summary_doc_of`：行、税、净总计、整单折扣、收付款、状态），官方那两个文件的渲染代码一个字没改；
- 顺手修掉官方那段的遮罩陷阱：`freeze()` 只在成功回调里 `unfreeze()`，请求一失败整页就永远发灰
  （用户那张标签以前就是这么卡住的）—— 现在失败也收。

### 15.5 删掉「物料组」

我们产品没有物料组。官方那一格建在 `make_search_bar()` 里 → 适配层在自己的
`make_search_bar()` 里（super 之后）把 `.item-group-field` 整个容器移除：筛选行只剩
「所有物料」标题 + 搜索框。实测 `.item-group-field` = 0。
（控件对象照官方那套建，官方在 `attach_clear_btn()` / `attach_shortcuts()` 里对它的引用落在
已移除节点上是空操作，不会抛错。`Ctrl+G` 那条快捷键还注册着但指向已移除的控件，属无害残留。）

### 15.6 提示语全局翻译补全（**以生成的翻译包为准**做的检查）

只看"术语表里有没有这个词"不够 —— **msgid 只出现在"带 key 的词条"里、且同英文的几条译文
不一致时，生成脚本按规矩不补"英文原文"那一行，运行时那句就直出英文**。所以这轮改成拿
`translations/<lang>.csv`（运行时真正生效的那份）当基准逐个核对。

- 收银域缺的 14 条服务端提示全补上，其中就有用户看到的那句
  **`Open a shift before selling`**（术语表里只有旧页面那句 `Open a shift on the cashier page first.`，
  而那已经是死词条，一并换掉）；
- 另外补了 `erp_portal` 27 条、`pearnly_core` 5 条、`pearnly_dms` 2 条、`pearnly_control` 14 条；
- **修掉两条"假翻译"**：`Void`（术语表里有两条带 key 的：库存"撤销"与销售"作废"，译文不一致
  → 不生成英文行）与 `No product matches that code`（手机扫码与盘点两条同样冲突）。
  前者改成走我们自己的词条键（与旧页面同一份），后者把盘点那句英文改成独占写法再加一行；
- 复查：脚本扫全部 `_()`/`__()` 字面量，剩下的"缺"只有 DocType 名、角色名（`System Manager`）、
  品牌名与 Frappe 自己已带的词（`Discard`/`Logout`/`Display`/`Website` 实测都已翻出来）。

### 15.7 顺手修掉"改了代码页面还是旧的"

适配层原来是页面里 `frappe.require("/assets/.../pearnly-pos-adapter.js")` 加载的 ——
路径不带版本，浏览器一直吃缓存（排查时靠 `window.__pos_adapter_v` 认版本）。
想给它带 `?v=` 又撞上框架自己的坑（`assets.js` 的 `extn()` 从路径里取扩展名，
带 `?` 就解析错，实测 `TypeError: s is not a function`）。

最后按仓库里已有的做法：**把适配层挂进 `app_include_js`**（`versioned_asset` 按文件内容算 hash），
页面里改成直接用那个全局加载器；hooks 那份兜底留给"刚改完还没重启 web"的情况。
代价是改适配层要 `clear-cache` + 重启 web（与 `desk/*.js` 那批一致）。

## 16. 2026-09-24 第三批：删掉"默认仓"、空态用官方图标与文案、建公司时一并建收银台

用户这一轮的三个动作 + 两个问题（原话要点）：**不要做默认仓，依赖的全部删掉，删掉后加一个
状态字段在备注后面**；**pos 空态未开班时加官方文案和 svg 图标展示**；问"收银台的来源是哪里、
要不要加收银台的配置、建公司的时候建还是怎么做"。

### 16.1 "默认仓"这一位彻底删掉（含它下面挂着的每一处）

**规则换成了什么**：仓库就是这份平铺清单，**哪个单据、哪台收银台用哪个仓，都写在自己身上**。
以前那种"没写就落默认仓"的兜底换成一个**事实判断**：这家公司**只有一个启用的仓**就用它
（唯一解，不是替谁拿主意），有多个仓就报错「请选择仓库」。

删掉/改掉的东西（逐处）：

| 落点 | 处理 |
| --- | --- |
| `ERP Warehouse.is_default` 字段 | 从 DocType JSON 删掉；`v1_6_…` patch 把库里的列真正 DROP（模型同步只加/改列，不删列） |
| `warehouses.ensure_default()` | 删掉；`required()` 改成"唯一仓 / 报错"；新增 `sole()`（事实判断，不存配置位） |
| `warehouses.set_default()` / `_demote_others()` | 删掉 |
| `masters/api.api_set_default_warehouse` | 删掉 |
| 仓库页「默认仓库」列 + 行内「设为默认」 | 删掉；列表与新建/编辑弹窗的**状态都排到备注后面**（用户口径） |
| `list_warehouses` 排序 | `is_default desc` → 按名字 |
| 出入库/盘点/采购入库/销售单出库/收银 | 全部改成"单据上明确的仓 → 唯一仓 → 报错"，一处不落（见各文件注释） |
| 词条 | `company.col.defaultWarehouse` / `company.setDefaultWarehouse` / `company.notice.defaultWarehouse` 三条删掉（不再引用） |

**存量数据怎么过来的**：`v1_5_terminal_warehouse` 放在 `[pre_model_sync]`（跑在模型同步**之前**），
趁 `is_default` 还在的时候把**当时实际生效的那个仓抄到收银台上**（优先 `is_default=1`，
没有就取这家公司最早的仓）。实测：站上两台终端都补成了 `WH-00174`（= 以前那个默认仓），
所以老站点不停摆、行为不变，只是值从"隐式"变成"显式"。

**实测四条**（`frappe` 直连跑）：`sole()` → None（这家公司有 19 个仓）；`required()` →
`ValidationError: Pick a warehouse`；`required("WH-00174")` → `WH-00174`；
`terminals.place_of(空仓终端)` → `This terminal has no warehouse…`。
以及新卖的一单 `SI2569-000065`：**单据仓 = 行仓 = `WH-00174`**（行上还带了商品名）。

### 16.2 没开班时的空态：官方那枚图标 + 官方那两句文案

以前那块地方是**一片白**。现在（`check_opening_entry()` 里没班次那条路）画一个空态：

- **图标**：官方 POS 自己那枚（erpnext 16.34.1 `public/images/pos.svg`，与 `official-*.js`
  同一份 GPLv3 素材），**内联在适配层**（照原样的路径，`fill="currentColor"` 跟着文字色走）；
- **文案**：官方文件里原文那两句 —— `Create POS Opening Entry`（新建开班记录）与
  `Please add Mode of payments and opening balance details.`（请添加支付方式与开班余额明细），
  都在术语表里、四语齐；
- 排版在 `desk/cashier.css` 的 `.pearnly-pos-empty`（居中、图标 96px、灰 55%）。

实测：真·未开班加载（不是造出来的）→ 图标 + 两行字 + 右上角「开班」主按钮；
点开班 → 班次 18 开起来、商品区 40 件、物料组 0。

### 16.3 收银台的来源：现在是"建公司时一并建一台，并且**把仓写死在它身上**"

来源有两处，本轮把第一处补上了：

1. **建公司时**（`company.create_company`）：建这家公司的第一个仓 → 建一台收银台
   （`terminals.ensure_default(company, warehouse=那个仓)`）→ 卖货的仓**显式**指向它。
   这样新公司一建出来就是能开班、能卖货的。
2. **第一次开班时的懒建**（`terminals.ensure_default`）：老站点/边界情况兜底，同一套逻辑。

**还没做的**（等用户拍板）：一个能改"收银台名字 / 卖货的仓 / 启用停用"的**配置入口**。
本轮只把后端能力（`api_create_terminal` / `api_update_terminal`）留着，页面没加 ——
要加的话放哪儿（收银组下单独一页 / 并进公司页）由用户定，见交接说明。

## 17. 2026-09-24 第四批：收款方式"关掉一种"三条路一起收口 + 顺带修掉一颗点了没反应的按钮

用户口径："继续做未完成的。" 上一批我明确列为遗留的第一条就是**退款弹窗没跟公司的收款方式走**；
这一轮把它连同"旧页面整套收款方式"一起收口，并且在实测里**撞出一颗真按钮坏了**。

### 17.1 「这家公司收哪几种钱」这件事，两条路现在都只有一份来源

来源一直是服务端那一份：`api_list_terminals` 回给每台收银台的 `payment_methods`
（= `masters/company.payment_methods_of(公司)`，建公司时勾的那几种）。上一批只把**搬过来的官方
收银台**接上了，**旧页面（默认那条 `/desk/pearnly-cashier` 不带参数的路）还是四种写死**——
关掉"转账"，旧路线的付款面板照样给转账。这一轮补齐：

| 落点 | 改法 |
| --- | --- |
| 旧页面付款面板 + 落单 + 退款 | `const METHODS` 拆成 `ALL_METHODS`（四种全集）与 `let METHODS`（**这家公司开了的**那几种）；`refresh()` 拿到班次后按收银台收窄一次，面板/校验/退款读的都是它 |
| 退款弹窗（共用模块） | `pos_receipt.refund({ …, methods })` 用调用方给的那几种；没给才回落四种 |
| 退款弹窗的"付款方式"那一格 | 原来把 `cash`/`card` 这种**键面**直接摆给收银员看，现在按付款面板同一份词条显示（`trade.methodCash` …），提交仍是键 |
| 开班/交班对账表 | 上一批已按公司过滤（`pos_shift.js`）；交班时仍照**班次上已经对过的行**给，不凭空加减 |
| 服务端 | 上一批的 `create_sale` 兜底闸保留（`This payment method is not enabled for this company`） |

### 17.2 实测（两只钟都走了一遍：关掉 → 只剩三种；还原 → 又是四种）

把 `CO-00552` 的"转账"取消勾选之后，在演示站真点鼠标：

| 面 | 结果 |
| --- | --- |
| 付款面板（旧路线） | `["cash","card","promptpay"]`，**转账消失**；默认展开的仍是第一种（现金，官方 `set_grand_total_to_default_mop` 同一手） |
| 退款弹窗的付款方式 | 读控件本体：`ProductSelect.get_value() = cash`、候选 = `现金 / 刷卡 / 扫码（PromptPay）` 三项（本地化名称 + 键面提交，实测落库的 `method` 就是 `cash`） |
| `api_list_terminals` | 两台收银台都回 `["cash","card","promptpay"]`（开班弹窗读的就是它） |
| 服务端兜底 | 直连调 `sales.create_sale`：`transfer` → `ValidationError: This payment method is not enabled for this company`；`cash` → 正常出单 |
| 还原成四种后 | 付款面板回到 `["cash","card","promptpay","transfer"]`（证明是跟着数据走，不是写死） |

### 17.3 顺带修掉的一颗真按钮：旧路线的「退货退款」点了没反应

验证退款这一格时发现：旧页面把按钮画成 `return-btn`，而事件绑的是
`.past-order-summary .refund-btn` —— **类名对不上，那颗按钮是死的**（搬过来的官方收银台
两边都用 `refund-btn`，所以只有默认那条路坏了）。改成同一个类名，`node --check` 通过。
这说明"收据三颗按钮"那批当时只在对照路由上验过，默认路由漏了。

### 17.4 这一轮留下的本地痕迹（用户口径：本地测试数据不用恢复）

为了把"有班次才能看付款面板"这条路走通，在演示站开班并真出了一单：**班次 19**
（`SHIFT-2026-00695`，`TERM-00485`）、**`SI2569-000066`**（点出来的，现金 ฿100）、
**`SI2569-000067`**（服务端兜底那条测试落下来的，现金 ฿100，`create_sale` 自己 commit，
所以脚本里的 `rollback()` 撤不掉它——记下来，别再当成"测试没留下东西"）。公司收款方式
已还原成四种。
