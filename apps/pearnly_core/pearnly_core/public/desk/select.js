/* Product selects use Frappe's searchable popup, not the browser's built-in select menu. */
(() => {
	if (!frappe.boot.pearnly_product) return;
	// A read-only native Autocomplete never builds its input, so it has no option list (awesomplete);
	// the framework's validate still reads that list and a set_value on it throws, stopping the page
	// (2026-10-26: the location field of a submitted document). Read-only has no options to check against:
	// keep the value. Fields that can be read-only still use a read-only Data field.
	class ProductAutocomplete extends frappe.ui.form.ControlAutocomplete {
		validate(value) {
			return this.awesomplete ? super.validate(value) : value;
		}
	}
	frappe.ui.form.ControlAutocomplete = ProductAutocomplete;
	class ProductSelect extends ProductAutocomplete {
		// A native Select lists every option; native Autocomplete stops at 99 unless typed into.
		// Keep the Select behaviour: the popup is as long as the option list (an explicit max_items still wins).
		option_limit() {
			const options = this.df.options;
			const count = Array.isArray(options) ? options.length : String(options || "").split("\n").length;
			return this.df.max_items || Math.max(99, count);
		}
		set_formatted_input(value) {
			// Dialogs can replace options after lookup. Refresh the native autocomplete
			// data before formatting, otherwise IDs appear and choices stay stale.
			const options = JSON.stringify(this.df.options);
			if (this.pearnly_options !== options) {
				this.set_options();
				this.pearnly_options = options;
				if (this.awesomplete) this.awesomplete.maxItems = this.option_limit();
			}
			super.set_formatted_input(value);
		}
		get_awesomplete_settings() {
			const settings = super.get_awesomplete_settings();
			settings.maxItems = this.option_limit();
			const filter = settings.filter;
			const control = this;
			settings.filter = function(item, input) {
				return filter.call(this, item, input === control.format_for_input(control.value) ? "" : input);
			};
			// Labels may come from DMS master data, so render them as text. An option's
			// description stays the official second line (small), also as text.
			settings.item = function(item) {
				const li = document.createElement("li");
				li.setAttribute("aria-selected", "false");
				const a = document.createElement("a");
				a.textContent = item.label || item.value;
				const description = this.get_item?.(item.value)?.description;
				if (description) {
					const small = document.createElement("span");
					small.className = "small text-muted";
					small.textContent = description;
					a.append(document.createElement("br"), small);
				}
				li.appendChild(a);
				return li;
			};
			return settings;
		}
		make_input() {
			super.make_input();
			this.$input.attr("autocomplete", "off");
			this.$input.on("focus click", () => this.awesomplete.evaluate());
		}
	}
	frappe.ui.form.ControlSelect = ProductSelect;
})();
