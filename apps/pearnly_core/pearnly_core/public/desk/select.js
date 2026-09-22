/* Product selects use Frappe's searchable popup, not the browser's built-in select menu. */
(() => {
	if (!frappe.boot.pearnly_product) return;
	class ProductSelect extends frappe.ui.form.ControlAutocomplete {
		get_awesomplete_settings() {
			const settings = super.get_awesomplete_settings();
			const filter = settings.filter;
			const control = this;
			settings.filter = function(item, input) {
				return filter.call(this, item, input === control.format_for_input(control.value) ? "" : input);
			};
			// Labels may come from DMS master data, so render them as text.
			settings.item = item => {
				const li = document.createElement("li");
				li.setAttribute("aria-selected", "false");
				const a = document.createElement("a");
				a.textContent = item.label || item.value;
				li.appendChild(a);
				return li;
			};
			return settings;
		}
		validate(value) {
			// Read-only controls have no input widget; defaults still pass through validation.
			return this.awesomplete ? super.validate(value) : value;
		}
		make_input() {
			super.make_input();
			this.$input.attr("autocomplete", "off");
			this.$input.on("focus click", () => this.awesomplete.evaluate());
		}
	}
	frappe.ui.form.ControlSelect = ProductSelect;
})();
