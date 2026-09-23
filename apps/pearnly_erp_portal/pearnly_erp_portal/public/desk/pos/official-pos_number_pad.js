erpnext.PointOfSale.NumberPad = class {
	constructor({ wrapper, events, cols, keys, css_classes, fieldnames_map }) {
		this.wrapper = wrapper;
		this.events = events;
		this.cols = cols;
		this.keys = keys;
		this.css_classes = css_classes || [];
		this.fieldnames = fieldnames_map || {};

		this.init_component();
	}

	init_component() {
		this.prepare_dom();
		this.bind_events();
	}

	prepare_dom() {
		const { cols, keys, css_classes, fieldnames } = this;

		function get_keys() {
			return keys.reduce((a, row, i) => {
				return (
					a +
					row.reduce((a2, number, j) => {
						const class_to_append = css_classes && css_classes[i] ? css_classes[i][j] : "";
						const fieldname =
							fieldnames && fieldnames[number]
								? fieldnames[number]
								: typeof number === "string"
								? frappe.scrub(number)
								: number;

						return (
							a2 +
							`<div class="numpad-btn ${class_to_append}" data-button-value="${fieldname}">${__(
								number
							)}</div>`
						);
					}, "")
				);
			}, "");
		}

		this.wrapper.html(
			`<div class="numpad-container">
				${get_keys()}
			</div>`
		);
	}

	bind_events() {
		const me = this;
		this.wrapper.on("click", ".numpad-btn", function () {
			const $btn = $(this);
			me.events.numpad_event($btn);
		});
	}
};
/* 官方收银台「数字键盘」组件（原样搬入，2026-09-23）。
 * 来源：erpnext 16.34.1 `selling/page/point_of_sale/pos_number_pad.js`。
 * 许可：ERPNext 为 GPLv3；本文件是它的副本，随本 App 分发时按 GPLv3 处理。
 */
