// 字段说明收进标题旁的说明图标（多产品共用：产品在 `pearnly_navigation` 声明 `field_help: "icon"` 才启用——ERP、Cowork 声明了，
// DMS 没声明照原生写在字段下方；控制面不装 pearnly_core，挂不上。用户 11-02：「这类字段下方的文案，放在标题旁边，放个注释图标，
// 鼠标放上去才显示文案」）。
// 框架 16.33 自带的 `show_description_on_click`（`form/info_card.js`）只能点开、键盘到不了、读屏读不到，建好后再改的说明整句丢掉，
// 还给标题加 nowrap（泰文长标题在手机宽撑出横向滚动），所以不开它；图标用它同一个 `info`，放进框架模板留在标题旁的
// `<span class="help">` 插槽（`set_label` 重写标题时不会冲掉），提示用 Desk 自带的 Bootstrap tooltip（同框架页头 `ui/page.js`）：
// 电脑上鼠标移上去 / 键盘聚焦就显示，触屏点一下开、再点或点别处收起；`aria-label` 是说明原文，读屏读得到。
// 只换位置，说明原文照旧经 `__(description, null, parent)` 翻译（同框架 `set_description`）。
// 例外 `description_inline: 1`：运行时写的状态与数值（加载中、读取失败、文件数、应收合计、物料「分类 · 特性」等）照原生留在下方，
// 每一处登记在 `scripts/field-help-inline.tsv`，闸门 `scripts/check-field-help.py` 只许减少。
(() => {
	if (frappe.boot?.pearnly_field_help !== "icon" || !frappe.ui?.form?.ControlInput) return;
	const Input = frappe.ui.form.ControlInput;
	const native = Input.prototype.set_description;
	const touch = Boolean(window.matchMedia?.("(pointer: coarse)").matches);
	const opened = new Set();
	const close = (except) => opened.forEach(icon => icon !== except && $(icon).tooltip("hide"));
	const plain = (html) => {
		const box = document.createElement("div");
		box.innerHTML = html;
		return (box.textContent || "").replace(/\s+/g, " ").trim();
	};
	// 放图标的地方：标题旁的 `.help`；按钮的标题区被框架整个藏起来（`button.js` 的 `toggle_label(false)`），放在按钮右边
	const slot = (control) => {
		if (control.df.fieldtype === "Button") {
			let host = $(control.input_area).children(".pearnly-field-help-slot");
			if (!host.length) host = $('<span class="pearnly-field-help-slot"></span>').appendTo(control.input_area);
			return host;
		}
		return control.$wrapper.find(".help").first();
	};
	const paint = (control, html) => {
		const host = slot(control);
		let icon = host.children(".pearnly-field-help");
		if (!html) {
			icon.tooltip("dispose").remove();
			return;
		}
		if (!icon.length) {
			icon = $(`<span class="pearnly-field-help" role="button" tabindex="0">${frappe.utils.icon("info", "xs")}</span>`)
				.appendTo(host)
				.tooltip({
					title() { return $(this).data("pearnly-help"); },
					html: true,
					placement: "top",
					container: "body",
					trigger: touch ? "manual" : "hover focus",
				})
				.on("shown.bs.tooltip", function () { opened.add(this); })
				.on("hidden.bs.tooltip", function () { opened.delete(this); });
		}
		icon.data("pearnly-help", html).attr("aria-label", plain(html));
	};

	Input.prototype.set_description = function (description) {
		// 没有标题的格（只有输入框）没地方放图标，照原生
		if (this.only_input || this.df.description_inline || this.df.show_description_on_click
				|| (!this.df.label && this.df.fieldtype !== "Button")) {
			// 登记的例外带标记，流程用例据此分得清「该留在下方」和「漏了」（`scripts/regression/ui_steps.mjs`）
			this.$wrapper?.attr("data-pearnly-description", this.df.description_inline ? "inline" : null);
			if (this._pearnly_help) {
				// 从图标换回下方（同一格先是说明、后是状态，例如定时任务的文件夹）：先收掉图标，再让原生重新写
				paint(this, "");
				this._pearnly_help = false;
				this._description = undefined;
			}
			return native.call(this, description);
		}
		if (description !== undefined) this.df.description = description;
		if (this._pearnly_help && this.df.description === this._description) return;
		if (!this._pearnly_help) this.$wrapper?.attr("data-pearnly-description", null);
		this._pearnly_help = true;
		this._description = this.df.description;
		this.set_empty_description();
		paint(this, this.df.description ? __(this.df.description, null, this.df.parent) : "");
	};

	// 点图标：勾选框的图标在 <label> 里，不能顺带勾上；触屏上开 / 收
	$(document).on("click", ".pearnly-field-help", function (event) {
		event.preventDefault();
		if (!touch) return;
		const shown = opened.has(this);
		close(this);
		$(this).tooltip(shown ? "hide" : "show");
	});
	$(document).on("keydown", ".pearnly-field-help", function (event) {
		if (event.key === "Escape") {
			if (!opened.has(this)) return;
			event.stopPropagation();  // 只收提示，不连弹窗一起关
			return $(this).tooltip("hide");
		}
		if (event.key !== "Enter" && event.key !== " ") return;
		event.preventDefault();
		close(this);
		$(this).tooltip(touch && opened.has(this) ? "hide" : "show");
	});
	// 点别处、弹窗关上、换页、滚动时收起（提示挂在 body 上，不会跟着弹窗走）
	$(document).on("click", (event) => { if (!$(event.target).closest(".pearnly-field-help").length) close(); });
	$(document).on("hide.bs.modal", () => close());
	frappe.router?.on?.("change", () => close());
	window.addEventListener("scroll", () => { if (opened.size) close(); }, { capture: true, passive: true });
})();
