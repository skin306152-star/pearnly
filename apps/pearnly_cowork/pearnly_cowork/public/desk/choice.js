/* 录入那几组选择共用的一套卡片（只有这一份实现，页面不再各画一套）。
 *
 * 一组卡片 = `p.cowork_choice(parent, {items, value, enabled, on_change})`：
 *   · `items`：`{value, label, status?, disabled?, action?}` —— `status` 是一颗官方
 *     状态药丸（`{text, tone}`，tone 用官方那几档 gray/blue/green/orange/red/purple），
 *     `action` 是卡片自带的第二颗按钮（例如 MR.ERP、Express 的「连接」）；
 *   · `value` 当前选中；`enabled=false` 整组不可点（例如还没选目标系统时）；
 *   · `disabled` 的单张卡片也不可点（例如这条通道还没连上）。
 *
 * 配色口径（用户 2026-09-25）：**主题色只出现在侧栏和主按钮**，所以选中态用中性色
 * （深色描边 + 加粗），不拿主题色去描卡片；状态药丸用的是官方自己的语义色。
 */
(() => {
	const p = frappe.pearnly;
	p.cowork_choice = (parent, {items = [], value = null, enabled = true, on_change}) => {
		let current = value, live = enabled;
		const wrapper = $("<div class='pearnly-choice'>").appendTo(parent);

		const paint = () => {
			wrapper.empty().toggleClass("is-disabled", !live);
			for (const item of items) {
				const blocked = !live || Boolean(item.disabled);
				const card = $("<div class='pearnly-choice-card'>")
					.attr("data-value", item.value)
					.toggleClass("is-picked", current === item.value)
					.toggleClass("is-blocked", blocked)
					.appendTo(wrapper);
				const head = $("<div class='pearnly-choice-head'>").appendTo(card);
				$("<span class='pearnly-choice-label'>").text(item.label).appendTo(head);
				if (item.status?.text) {
					$("<span class='indicator-pill'>").addClass(item.status.tone || "gray")
						.text(item.status.text).appendTo(head);
				}
				if (item.hint) $("<div class='pearnly-choice-hint text-muted'>").text(item.hint).appendTo(card);
				if (item.action) {
					$("<div class='pearnly-choice-actions'>").append(
						$("<button type='button' class='btn btn-default btn-sm'>").text(item.action.label)
							.on("click", event => {event.stopPropagation(); item.action.on_click();}),
					).appendTo(card);
				}
				// 点卡片=选中；卡片的按钮已经 stopPropagation，不会误选。
				card.on("click", () => {
					if (blocked || current === item.value) return;
					current = item.value;
					paint();
					on_change?.(current);
				});
			}
		};
		paint();
		return {
			get value() {return current;},
			set value(next) {current = next; paint();},
			set_items(next) {items = next; paint();},
			set_enabled(next) {live = next; paint();},
			destroy() {wrapper.remove();},
		};
	};
})();
