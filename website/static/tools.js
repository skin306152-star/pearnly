/* pearnly.com 免费工具：全部在浏览器里算，不发请求。纯函数同时导出给 node 测试（tests/test-website-tools.cjs）。
 * 金额一律按“分”（สตางค์）用 BigInt 算，第三位小数四舍五入，避免浮点误差。 */
(function (root) {
  'use strict';

  var DIGITS = ['ศูนย์', 'หนึ่ง', 'สอง', 'สาม', 'สี่', 'ห้า', 'หก', 'เจ็ด', 'แปด', 'เก้า'];
  var PLACES = ['', 'สิบ', 'ร้อย', 'พัน', 'หมื่น', 'แสน'];
  var MAX_DIGITS = 15;

  // 读一串整数（泰文），每 6 位一个“ล้าน”；个位的 1 前面有更高位时读 เอ็ด（同 Excel BAHTTEXT）。
  function readInteger(str) {
    str = String(str).replace(/^0+/, '');
    var out = '';
    for (var i = 0; i < str.length; i++) {
      var d = +str[i];
      var pos = str.length - 1 - i;
      var place = pos % 6;
      if (d !== 0) {
        if (place === 0 && d === 1 && i > 0) out += 'เอ็ด';
        else if (place === 1 && d === 1) out += 'สิบ';
        else if (place === 1 && d === 2) out += 'ยี่สิบ';
        else out += DIGITS[d] + PLACES[place];
      }
      if (place === 0 && pos > 0) out += 'ล้าน';
    }
    return out;
  }

  // "1,234.565" → { negative, integer: '1234', satang: 57 }；格式不对返回 null。
  function parseAmount(input) {
    var s = String(input == null ? '' : input).replace(/[,\s฿]/g, '');
    var m = /^(-?)(\d*)(?:\.(\d*))?$/.exec(s);
    if (!m || (m[2] === '' && !m[3])) return null;
    var integer = (m[2] || '0').replace(/^0+(?=\d)/, '');
    if (integer.length > MAX_DIGITS) return null;
    var frac = (m[3] || '') + '000';
    var satang = +frac.slice(0, 2) + (+frac[2] >= 5 ? 1 : 0);
    if (satang === 100) {
      integer = (BigInt(integer) + 1n).toString();
      satang = 0;
    }
    return { negative: m[1] === '-', integer: integer, satang: satang };
  }

  function bahtText(input) {
    var a = parseAmount(input);
    if (!a) return null;
    var baht = readInteger(a.integer);
    if (!baht && !a.satang) return 'ศูนย์บาทถ้วน';
    var text = (baht ? baht + 'บาท' : '') + (a.satang ? readInteger(String(a.satang)) + 'สตางค์' : 'ถ้วน');
    return (a.negative ? 'ลบ' : '') + text;
  }

  function toSatang(input) {
    var a = parseAmount(input);
    if (!a || a.negative) return null;
    return BigInt(a.integer) * 100n + BigInt(a.satang);
  }

  // 正数 a*b/c 四舍五入到整数分。
  function mulDivRound(a, b, c) {
    return (a * b * 2n + c) / (2n * c);
  }

  // 税率用万分位（7% = 700），避免小数。
  function rateBp(input) {
    var a = parseAmount(input);
    if (!a || a.negative || a.integer.length > 3) return null;
    var bp = BigInt(a.integer) * 100n + BigInt(a.satang);
    return bp <= 10000n ? bp : null;
  }

  function vat(amount, rate, inclusive) {
    var x = toSatang(amount), bp = rateBp(rate);
    if (x === null || bp === null) return null;
    var base = inclusive ? mulDivRound(x, 10000n, 10000n + bp) : x;
    var tax = inclusive ? x - base : mulDivRound(base, bp, 10000n);
    return { base: base, vat: tax, total: base + tax };
  }

  // 预扣税只按未含 VAT 的金额算；pay = 应付对方的净额。
  function withholding(amount, inclusive, vatRate, whtRate) {
    var v = vat(amount, vatRate, inclusive), bp = rateBp(whtRate);
    if (!v || bp === null) return null;
    var wht = mulDivRound(v.base, bp, 10000n);
    return { base: v.base, vat: v.vat, total: v.total, wht: wht, pay: v.total - wht };
  }

  function formatSatang(x) {
    var neg = x < 0n;
    if (neg) x = -x;
    var baht = (x / 100n).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ',');
    var satang = (x % 100n).toString().padStart(2, '0');
    return (neg ? '-' : '') + baht + '.' + satang;
  }

  // 13 位税号 / 身份证号的 MOD-11 校验位（同 pearnly_core.tax_documents.amounts.valid_tax_id）。
  function taxId(input) {
    var raw = String(input == null ? '' : input).replace(/[\s-]/g, '');
    if (!/^\d{13}$/.test(raw)) return { digits: raw, complete: false, valid: false, formatted: '' };
    var total = 0;
    for (var i = 0; i < 12; i++) total += +raw[i] * (13 - i);
    var valid = (11 - total % 11) % 10 === +raw[12];
    var formatted = [raw.slice(0, 1), raw.slice(1, 5), raw.slice(5, 10), raw.slice(10, 12), raw.slice(12)].join('-');
    return { digits: raw, complete: true, valid: valid, formatted: formatted };
  }

  var api = {
    readInteger: readInteger, parseAmount: parseAmount, bahtText: bahtText, toSatang: toSatang,
    vat: vat, withholding: withholding, formatSatang: formatSatang, taxId: taxId,
  };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (typeof document === 'undefined') return;

  // ---- 页面绑定：每个工具是一个 form[data-tool]，结果写进 [data-out] ----
  function $(form, sel) { return form.querySelector(sel); }
  function val(form, name) {
    var el = form.elements[name];
    if (!el) return '';
    if (el.type === 'checkbox') return el.checked;
    return el.value;
  }
  function show(form, key, text) {
    var el = $(form, '[data-out="' + key + '"]');
    if (el) el.textContent = text;
  }
  function state(form, name) {
    var nodes = form.querySelectorAll('[data-state]');
    for (var i = 0; i < nodes.length; i++) nodes[i].hidden = nodes[i].getAttribute('data-state') !== name;
  }

  var RENDER = {
    'baht-text': function (form) {
      var text = val(form, 'amount').trim() ? bahtText(val(form, 'amount')) : '';
      show(form, 'text', text || '');
      state(form, text === null ? 'invalid' : text ? 'ok' : 'empty');
    },
    vat: function (form) {
      var r = val(form, 'amount').trim() ? vat(val(form, 'amount'), val(form, 'rate'), val(form, 'mode') === 'inclusive') : undefined;
      if (r === undefined) return state(form, 'empty');
      if (!r) return state(form, 'invalid');
      show(form, 'base', formatSatang(r.base));
      show(form, 'vat', formatSatang(r.vat));
      show(form, 'total', formatSatang(r.total));
      state(form, 'ok');
    },
    'withholding-tax': function (form) {
      var custom = $(form, '[data-custom-rate]');
      var preset = val(form, 'preset');
      if (custom) custom.hidden = preset !== 'custom';
      var rate = preset === 'custom' ? val(form, 'rate') : preset;
      var vatRate = val(form, 'vat') ? '7' : '0';
      var r = val(form, 'amount').trim() ? withholding(val(form, 'amount'), val(form, 'mode') === 'inclusive', vatRate, rate) : undefined;
      if (r === undefined) return state(form, 'empty');
      if (!r) return state(form, 'invalid');
      show(form, 'base', formatSatang(r.base));
      show(form, 'vat', formatSatang(r.vat));
      show(form, 'wht', formatSatang(r.wht));
      show(form, 'pay', formatSatang(r.pay));
      state(form, 'ok');
    },
    'tax-id': function (form) {
      var r = taxId(val(form, 'id'));
      if (!r.digits) return state(form, 'empty');
      if (!r.complete) return state(form, 'incomplete');
      show(form, 'formatted', r.formatted);
      state(form, r.valid ? 'valid' : 'invalid');
    },
  };

  function bind(form) {
    var render = RENDER[form.getAttribute('data-tool')];
    if (!render) return;
    var run = function () { render(form); };
    form.addEventListener('input', run);
    form.addEventListener('change', run);
    form.addEventListener('submit', function (e) { e.preventDefault(); run(); });
    var copy = $(form, '[data-copy]');
    if (copy && navigator.clipboard) {
      copy.addEventListener('click', function () {
        var out = $(form, '[data-out="' + copy.getAttribute('data-copy') + '"]');
        if (out && out.textContent) navigator.clipboard.writeText(out.textContent);
      });
    }
    run();
  }

  var forms = document.querySelectorAll('form[data-tool]');
  for (var i = 0; i < forms.length; i++) bind(forms[i]);
})(this);
