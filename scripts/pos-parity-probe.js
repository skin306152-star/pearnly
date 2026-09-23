/* 收银台「照官方」取样探针：在**官方收银台**与**我们的收银台**各跑一次，输出同一份结构，
 * 再由 `scripts/pos-parity-diff.py` 逐项对照。
 *
 * 为什么要有这台机器（2026-09-23 用户口径）：
 *   "为什么喜欢自己画啊……关键是画出来的还不像，怎么办？那我没发现的差异怎么办？"
 * 靠肉眼看截图，一次只能发现一两个差异；这台机器把"看"变成"量"：
 * 同一批选择器、同一批属性、同一批动作，两边各出一份数值，差异自己浮出来。
 *
 * 用法（在页面里跑，需要能开这两张页的浏览器 + DevTools/CDP）：
 *   1) 打开官方收银台（例如 http://127.0.0.1:8084/desk/point-of-sale ）与我们的
 *      （例如 http://erp-pos-a.localhost:8147/desk/pearnly-cashier ）；
 *   2) 两边把**同样的动作**做一遍（加货 → 点折扣 → 填 7 回车 → 点结账 …），
 *      每一步在一张页里执行：`JSON.stringify(window.__posProbe())`；
 *   3) 把两次输出存成文件，跑
 *        python3 scripts/pos-parity-diff.py official.json ours.json
 *
 * 说明：只比**样式与几何**，不比文字（两边的商品、金额、语言本来就不同）。
 */
(() => {
	// 属性表：这几项加起来足以覆盖"看着不像"的绝大多数原因（尺寸、间距、字重、颜色、边框）。
	const PROPS = ["display", "position", "alignItems", "justifyContent", "flexDirection", "gap",
		"paddingTop", "paddingRight", "paddingBottom", "paddingLeft",
		"marginTop", "marginRight", "marginBottom", "marginLeft",
		"borderTopStyle", "borderTopWidth", "borderTopColor", "borderRadius",
		"fontSize", "fontWeight", "lineHeight", "color", "backgroundColor",
		"boxShadow", "width", "height"];

	// 选择器表：键是稳定名字（两边一致），值是 CSS 选择器。
	// 缺元素在报告里显示 `缺失` —— 那本身就是一个差异（官方有、我们没有）。
	const SELECTORS = {
		app: ".point-of-sale-app",
		items_selector: ".items-selector",
		items_container: ".items-container",
		item_card: ".items-container .item-wrapper",
		item_card_display: ".items-container .item-wrapper .item-display",
		item_card_name: ".items-container .item-wrapper .item-detail > .item-name",
		item_card_rate: ".items-container .item-wrapper .item-detail > .item-rate",
		item_card_pill: ".items-container .item-wrapper .item-qty-pill",
		cart_container: ".cart-container",
		cart_totals: ".cart-totals-section",
		discount_box: ".cart-totals-section .add-discount-wrapper",
		discount_field: ".cart-totals-section .add-discount-wrapper > .add-discount-field",
		discount_input: ".cart-totals-section .add-discount-wrapper input",
		discount_btn: ".cart-totals-section .edit-discount-btn",
		discount_icon: ".cart-totals-section .discount-icon",
		qty_row: ".cart-totals-section .item-qty-total-container",
		net_row: ".cart-totals-section .net-total-container",
		grand_row: ".cart-totals-section .grand-total-container",
		checkout_btn: ".cart-totals-section .checkout-btn",
		cart_row: ".cart-items-section .cart-item-wrapper",
		cart_row_name: ".cart-items-section .cart-item-wrapper .item-name",
		cart_row_qty: ".cart-items-section .cart-item-wrapper .item-qty",
		numpad: ".numpad-section",
		numpad_btn: ".numpad-section .numpad-btn",
		payment_container: ".payment-container",
		payment_modes: ".payment-container .payment-modes",
		payment_mode_row: ".payment-container .mode-of-payment",
		payment_keypad: ".payment-container .number-pad",
		payment_totals: ".payment-container .totals",
		submit_order: ".payment-container .submit-order-btn",
	};

	window.__posProbe = function (extraSelectors, onlyProps) {
		const sels = Object.assign({}, SELECTORS, extraSelectors || {});
		const props = onlyProps && onlyProps.length ? onlyProps : PROPS;
		const out = {};
		for (const [name, sel] of Object.entries(sels)) {
			let el = null;
			try { el = document.querySelector(sel); } catch (e) { out[name] = { error: "选择器非法" }; continue; }
			if (!el) { out[name] = null; continue; }
			const cs = getComputedStyle(el);
			const r = el.getBoundingClientRect();
			const item = {
				box: { w: Math.round(r.width), h: Math.round(r.height) },
				visible: r.width > 0 && r.height > 0 && cs.display !== "none" && cs.visibility !== "hidden",
				text: (el.innerText || "").replace(/\s+/g, " ").trim().slice(0, 40),
			};
			for (const p of props) item[p] = cs[p];
			out[name] = item;
		}
		return out;
	};
})();
