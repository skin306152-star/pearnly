/* pearnly.com 免费工具：全部在浏览器里算，不发请求。纯函数同时导出给 node 测试（tests/test-website-tools.cjs）。
 * 金额一律按“分”（สตางค์）用 BigInt 算，第三位小数四舍五入，避免浮点误差。
 * 文件类工具（图片转 PDF、合并 / 拆分 PDF）的页面逻辑在 files.js，表格类工具（PDF 转 Excel、Excel 转 PDF）在 sheets.js；
 * 这里只放它们要用的纯函数（页码范围、表格还原、单元格识别、数字格式），方便 node 测试。 */
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

  // ---- PromptPay 收款码：按泰国央行公布的 EMVCo 格式拼出二维码内容，最后是 CRC-16/CCITT（初值 FFFF） ----
  function tlv(id, value) { return id + String(value.length).padStart(2, '0') + value; }
  function crc16(text) {
    var crc = 0xFFFF;
    for (var i = 0; i < text.length; i++) {
      crc ^= text.charCodeAt(i) << 8;
      for (var b = 0; b < 8; b++) crc = crc & 0x8000 ? ((crc << 1) ^ 0x1021) & 0xFFFF : (crc << 1) & 0xFFFF;
    }
    return crc.toString(16).toUpperCase().padStart(4, '0');
  }
  // 收款账号：手机号 10 位（转成 0066 开头的 13 位）、身份证号 / 税号 13 位、电子钱包 15 位。金额可空（空 = 付款人自己填）。
  function promptpay(target, amount) {
    var id = String(target == null ? '' : target).replace(/[\s-]/g, '');
    var kind;
    if (/^0\d{9}$/.test(id)) { kind = '01'; id = '0066' + id.slice(1); }
    else if (/^\d{13}$/.test(id)) kind = '02';
    else if (/^\d{15}$/.test(id)) kind = '03';
    else return { error: 'target' };
    var money = '';
    if (String(amount == null ? '' : amount).trim()) {
      var x = toSatang(amount);
      if (x === null || x <= 0n) return { error: 'amount' };
      money = formatSatang(x).replace(/,/g, '');
    }
    var body = tlv('00', '01') + tlv('01', money ? '12' : '11')
      + tlv('29', tlv('00', 'A000000677010111') + tlv(kind, id))
      + tlv('53', '764') + (money ? tlv('54', money) : '') + tlv('58', 'TH') + '6304';
    return { payload: body + crc16(body), kind: kind };
  }

  // ---- 佛历 / 公历 ----
  var MONTHS = ['มกราคม', 'กุมภาพันธ์', 'มีนาคม', 'เมษายน', 'พฤษภาคม', 'มิถุนายน', 'กรกฎาคม', 'สิงหาคม', 'กันยายน', 'ตุลาคม', 'พฤศจิกายน', 'ธันวาคม'];
  var MONTHS_SHORT = ['ม.ค.', 'ก.พ.', 'มี.ค.', 'เม.ย.', 'พ.ค.', 'มิ.ย.', 'ก.ค.', 'ส.ค.', 'ก.ย.', 'ต.ค.', 'พ.ย.', 'ธ.ค.'];
  // 一个年份：2400 以上当作佛历，否则当作公历；相差 543 年。
  function year(input) {
    var s = String(input == null ? '' : input).trim();
    if (!/^\d{1,4}$/.test(s)) return null;
    var y = +s;
    if (y < 1) return null;
    return y >= 2400 ? { be: y, ce: y - 543 } : { be: y + 543, ce: y };
  }
  // 公历日期 "2026-10-09"（日期选择框的值）→ 泰文写法。
  function thaiDate(iso) {
    var m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(iso || ''));
    if (!m) return null;
    var y = +m[1], mo = +m[2], d = +m[3];
    var dt = new Date(Date.UTC(y, mo - 1, d));
    if (dt.getUTCFullYear() !== y || dt.getUTCMonth() !== mo - 1 || dt.getUTCDate() !== d) return null;
    var be = y + 543;
    return {
      long: d + ' ' + MONTHS[mo - 1] + ' ' + be,
      short: d + ' ' + MONTHS_SHORT[mo - 1] + ' ' + String(be).slice(-2),
      numeric: String(d).padStart(2, '0') + '/' + String(mo).padStart(2, '0') + '/' + be,
    };
  }

  // ---- 拆分 PDF 的页码范围："1-3, 5, 8-" → 从 0 开始的页序号（按输入顺序，去重）；超出总页数或写错返回 null ----
  function pageRange(input, total) {
    var s = String(input == null ? '' : input).replace(/\s/g, '');
    if (!s || !(total > 0)) return null;
    var out = [], seen = {};
    var parts = s.split(/[,，]/);
    for (var i = 0; i < parts.length; i++) {
      var m = /^(\d+)?(?:(-)(\d+)?)?$/.exec(parts[i]);
      if (!m || (!m[1] && !m[3]) || (!m[1] && !m[2])) return null;
      var a = m[1] ? +m[1] : 1, b = m[2] ? (m[3] ? +m[3] : total) : a;
      if (a < 1 || b < a || b > total) return null;
      for (var p = a; p <= b; p++) if (!seen[p]) { seen[p] = 1; out.push(p - 1); }
    }
    return out;
  }

  // ---- 税号查公司：解析税务局（กรมสรรพากร）公开接口的 SOAP 返回（经官网 nginx 的 /api/rd/vat、/api/rd/tin 转发） ----
  // 规则和 ERP 的 apps/pearnly_erp_portal/pearnly_erp_portal/masters/rd_contracts.py 一致：字段是 <vXxx><anyType>值</anyType></vXxx>，
  // 按别名取值，"-" 当没有；地址按门牌、Moo、巷、路、区、县、府、邮编拼起来。
  var ADDRESS_PARTS = [['HouseNumber'], ['MooNumber', 'Moo'], ['Yaek'], ['VillageName'], ['BuildingName'], ['RoomNumber'], ['FloorNumber'],
    ['SoiName', 'Soi'], ['StreetName', 'Street'], ['Thambol', 'ThambolName', 'ThumbolName', 'TambolName'], ['Amphur', 'AmphurName'],
    ['Province', 'ProvinceName'], ['PostCode']];
  function unescapeXml(t) {
    return t.replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&quot;/g, '"').replace(/&apos;/g, "'").replace(/&#(\d+);/g, function (m, n) { return String.fromCharCode(+n); }).replace(/&amp;/g, '&');
  }
  function rdFields(xml) {
    var out = {}, re = /<v([A-Z][A-Za-z]*)>\s*<anyType[^>]*>([\s\S]*?)<\/anyType>/g, m;
    while ((m = re.exec(String(xml || '')))) {
      var v = unescapeXml(m[2]).trim();
      if (v && !(m[1] in out)) out[m[1]] = v;
    }
    return out;
  }
  function rdPick(f) {
    for (var i = 1; i < arguments.length; i++) { var v = (f[arguments[i]] || '').trim(); if (v && v !== '-') return v; }
    return '';
  }
  // VAT 登记查询的返回 → 公司资料；查不到（没有 NID）返回 null。regBE 是税务局给的佛历日期 "2546-02-14"。
  function rdCompany(xml) {
    var f = rdFields(xml), id = rdPick(f, 'NID', 'ID');
    if (!id) return null;
    var branch = parseInt(rdPick(f, 'BranchNumber') || '0', 10) || 0;
    var name = [rdPick(f, 'BranchTitleName', 'BranchTitle', 'TitleName', 'Title'), rdPick(f, 'BranchName', 'Name')].filter(Boolean).join(' ');
    var address = ADDRESS_PARTS.map(function (names) { return rdPick.apply(null, [f].concat(names)); }).filter(Boolean).join(' ');
    return { taxId: id, branch: branch, name: name, address: address, regBE: rdPick(f, 'BusinessFirstDate') };
  }
  // 税号查询（TIN）的返回 → 税号是否存在。
  function rdExists(xml) { return /^(yes|true|1|y)$/i.test(rdPick(rdFields(xml), 'IsExist')); }

  // ---- PDF 转 Excel：把 pdf.js 读出的文字块（每块有 x0、x1、基线 y、字高 h）还原成表格 ----
  // 1) 同一基线（相差不到半个字高）的块归成一行，行内挨得很近的块拼成一个词组；
  // 2) 列 = 横向上大多数“有两块以上的行”都覆盖到的区间，几乎没有行覆盖的地方就是列间缝隙；
  // 3) 每个词组放进和它重叠最多的那一列；4) 每页都重复的页眉页脚只留第一次，“หน้า 1/3”这类页码去掉。
  function median(list) { var s = list.slice().sort(function (a, b) { return a - b; }); return s.length ? s[s.length >> 1] : 0; }
  // PDF 里读出的泰文常见两种走样：声调等符号的字形没有编码，读出来是控制字符（删掉，宁可少一个声调也不留怪字符）；
  // “ำ”被拆成 ํ + า（合回来，中间夹着声调的也一样）。
  // 旧式泰文字体用私用区 U+F700–U+F71A 放“排低、左移”的声调和元音（Microsoft / Apple 通用的那套），换回标准泰文字符
  var THAI_PUA = [0x0E10, 0x0E34, 0x0E35, 0x0E36, 0x0E37, 0x0E48, 0x0E49, 0x0E4A, 0x0E4B, 0x0E4C, 0x0E48, 0x0E49, 0x0E4A, 0x0E4B, 0x0E4C,
                  0x0E0D, 0x0E31, 0x0E4D, 0x0E47, 0x0E48, 0x0E49, 0x0E4A, 0x0E4B, 0x0E4C, 0x0E38, 0x0E39, 0x0E3A];
  function cleanThai(str) {
    return String(str || '').replace(/[\uF700-\uF71A]/g, function (ch) { return String.fromCharCode(THAI_PUA[ch.charCodeAt(0) - 0xF700]); })
      .replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F\uFFFD]/g, '').replace(/\u0E4D([\u0E48-\u0E4B]?)\u0E32/g, '$1\u0E33');
  }
  // 有的 PDF（系统打印的报表）一整行写成一串字、列之间用好几个空格隔开：按两个以上空格切开，位置按字数比例估。
  // 一段以日期开头、后面跟着别的字（日期和单号挤在一起）：把日期单独切出来，好成为一列。
  var LEAD_DATE = /^(\d{1,2}[\/\-.]\d{1,2}[\/\-.](?:\d{4}|\d{2}))(?!\d)\s*([^\s\d].*|\S.*\s.*)$/;   // 日期后面紧跟泰文（没空格）也算
  function splitItem(it) {
    var str = it.str, w = (it.x1 - it.x0) / Math.max(1, str.length), out = [], re = /\S+(?:\s\S+)*/g, m;
    var spaced = /\S\s{2,}\S/.test(str);
    while ((m = re.exec(str))) {
      if (!spaced && out.length) break;
      out.push({ str: m[0], x0: it.x0 + (m.index + 0.5) * w, x1: it.x0 + (m.index + m[0].length - 0.5) * w, y: it.y, h: it.h, cut: out.length > 0 });
    }
    if (!spaced) out = [{ str: str, x0: it.x0, x1: it.x1, y: it.y, h: it.h, cut: false }];
    return out.reduce(function (acc, part) {
      var d = LEAD_DATE.exec(part.str.trim()), pw = (part.x1 - part.x0) / Math.max(1, part.str.length);
      if (!d) { acc.push(part); return acc; }
      var cut = part.str.indexOf(d[2]);
      // 位置按字数估，各收进半个字宽，免得估大了把两列之间的空隙填上
      acc.push({ str: d[1], x0: part.x0, x1: part.x0 + (d[1].length - 0.5) * pw, y: part.y, h: part.h, cut: part.cut });
      acc.push({ str: d[2], x0: part.x0 + (cut + 0.5) * pw, x1: part.x1, y: part.y, h: part.h, cut: true });
      return acc;
    }, []);
  }
  var RULE_ONLY = /^[\s\-_=.*~]+$/;   // 只有分隔线的行（-----、_____、=====）
  function pageLines(items) {
    items = [].concat.apply([], items.map(function (it) { return splitItem({ str: cleanThai(it.str), x0: it.x0, x1: it.x1, y: it.y, h: it.h }); }))
      .filter(function (it) { return !RULE_ONLY.test(it.str); });
    var list = items.filter(function (it) { return it.str && it.str.trim(); }).sort(function (a, b) { return b.y - a.y || a.x0 - b.x0; });
    var H = median(list.map(function (it) { return it.h; })) || 10, lines = [];
    list.forEach(function (it) {
      var line = null;
      for (var i = lines.length - 1; i >= 0 && i >= lines.length - 3; i--) if (Math.abs(lines[i].y - it.y) <= H * 0.5) { line = lines[i]; break; }
      if (!line) { line = { y: it.y, items: [] }; lines.push(line); }
      line.items.push(it);
    });
    return lines.map(function (line) {
      var parts = line.items.sort(function (a, b) { return a.x0 - b.x0; }), chunks = [];
      parts.forEach(function (it) {
        var last = chunks[chunks.length - 1], gap = last ? it.x0 - last.x1 : Infinity;
        // cut：上面按空格或日期切开的段，不再拼回去
        if (last && gap < H * 0.3 && !it.cut) { last.text += (gap > H * 0.12 && !/\s$/.test(last.text) ? ' ' : '') + it.str; last.x1 = Math.max(last.x1, it.x1); }
        else chunks.push({ x0: it.x0, x1: it.x1, text: it.str });
      });
      chunks.forEach(function (c) { c.text = c.text.replace(/\s+/g, ' ').trim(); });
      // 日期和后面的单号本来是两段、只隔一个空格，上面拼词组时会拼到一起：拼完再把开头的日期切出来
      chunks = chunks.reduce(function (acc, c) {
        var d = LEAD_DATE.exec(c.text);
        if (!d) { acc.push(c); return acc; }
        var cw = (c.x1 - c.x0) / Math.max(1, c.text.length), cut = c.text.indexOf(d[2]);
        acc.push({ x0: c.x0, x1: c.x0 + (d[1].length - 0.5) * cw, text: d[1] });
        acc.push({ x0: c.x0 + (cut + 0.5) * cw, x1: c.x1, text: d[2] });
        return acc;
      }, []);
      return { y: line.y, H: H, chunks: chunks.filter(function (c) { return c.text; }) };
    }).filter(function (l) { return l.chunks.length; });
  }
  function columnsOf(lines) {
    var rows = lines.filter(function (l) { return l.chunks.length >= 2; });
    if (!rows.length) return [];
    var lo = Infinity, hi = -Infinity;
    rows.forEach(function (l) { l.chunks.forEach(function (c) { lo = Math.min(lo, c.x0); hi = Math.max(hi, c.x1); }); });
    lo = Math.floor(lo); hi = Math.ceil(hi);
    var cover = new Array(hi - lo + 1).fill(0), need = Math.max(1, rows.length * 0.05);
    rows.forEach(function (l) { l.chunks.forEach(function (c) { for (var x = Math.floor(c.x0); x <= Math.ceil(c.x1); x++) cover[x - lo]++; }); });
    var cols = [], start = -1;
    for (var i = 0; i <= cover.length; i++) {
      var on = i < cover.length && cover[i] > need;
      if (on && start < 0) start = i;
      if (!on && start >= 0) { cols.push([start + lo, i - 1 + lo]); start = -1; }
    }
    return cols;
  }
  var PAGE_NO = /^(page|หน้า(ที่)?|頁|第)\s*\d+(\s*(\/|of|จาก)\s*\d+)?\s*(页|頁)?$/i;
  function nearest(c, cols) {
    var best = 0, bestOv = -Infinity;
    cols.forEach(function (col, k) {
      var ov = Math.min(c.x1, col[1]) - Math.max(c.x0, col[0]);
      if (ov > bestOv) { bestOv = ov; best = k; }
    });
    return best;
  }
  // 每页自己算列（同一份 PDF 里几页版式可能不同）；行太少的页沿用全部页一起算的列。
  // 再把各页的列按位置对齐成一套总列：和已有总列重叠过半就算同一列，否则新开一列。
  function tableFromPages(pages) {
    var all = pages.map(pageLines), fallback = columnsOf([].concat.apply([], all));
    if (!fallback.length) fallback = [[-Infinity, Infinity]];
    var global = [];
    var pageCols = all.map(function (lines) {
      var cols = lines.filter(function (l) { return l.chunks.length >= 2; }).length >= 4 ? columnsOf(lines) : fallback;
      if (!cols.length) cols = fallback;
      return cols.map(function (col) {
        for (var g = 0; g < global.length; g++) {
          var ov = Math.min(col[1], global[g][1]) - Math.max(col[0], global[g][0]);
          if (ov > 0.5 * Math.min(col[1] - col[0], global[g][1] - global[g][0])) return { col: col, g: g };
        }
        global.push(col.slice());
        return { col: col, g: global.length - 1 };
      });
    });
    var order = global.map(function (c, g) { return g; }).sort(function (a, b) { return global[a][0] - global[b][0]; });
    var slot = []; order.forEach(function (g, k) { slot[g] = k; });
    var rows = [], seen = {};
    all.forEach(function (lines, p) {
      var cols = pageCols[p];
      lines.forEach(function (l) {
        var cells = global.map(function () { return ''; });
        l.chunks.forEach(function (c) {
          var k = slot[cols[nearest(c, cols.map(function (x) { return x.col; }))].g];
          cells[k] = cells[k] ? cells[k] + ' ' + c.text : c.text;
        });
        var key = cells.join('\u0001');
        rows.push({ page: p, cells: cells, key: key });
        (seen[key] = seen[key] || {})[p] = 1;
      });
    });
    var n = pages.length, kept = [], done = {};
    rows.forEach(function (r) {
      var text = r.cells.filter(Boolean).join(' ');
      if (PAGE_NO.test(text)) return;
      var onPages = Object.keys(seen[r.key]).length;
      if (n >= 2 && onPages >= 2 && onPages >= n / 2) { if (done[r.key]) return; done[r.key] = 1; }
      kept.push(r.cells);
    });
    // 某一列有三成以上的格子是“日期 + 别的字”（日期和单号挤在同一列，没日期的续行又把列间缝隙填上了）：拆成日期列和其余文字列
    for (var k = (kept[0] || []).length - 1; k >= 0; k--) {
      var filled = kept.filter(function (r) { return r[k]; }), lead = filled.filter(function (r) { return LEAD_DATE.test(r[k]); });
      if (lead.length < 3 || lead.length < filled.length * 0.3) continue;
      kept = kept.map(function (r) {
        var d = r[k] && LEAD_DATE.exec(r[k]), out = r.slice();
        out.splice(k, 1, d ? d[1] : '', d ? d[2].trim() : r[k]);
        return out;
      });
    }
    var used = (kept[0] || []).map(function (c, k) { return kept.some(function (r) { return r[k]; }); });
    return kept.map(function (r) { return r.filter(function (c, k) { return used[k]; }); });
  }
  // 读出来的字是不是乱码：泰文 PDF 字体编码不规范时，会变成私用区字符、替换符，或一串 Latin-1 符号（TIS-620 当成 Latin-1）
  function garbled(text) {
    var s = String(text || '').replace(/\s/g, '');
    if (!s.length) return false;
    var bad = (s.match(/[\uE000-\uF8FF\uFFFD\u0000-\u0008]/g) || []).length + (s.match(/[\u00A1-\u00FF]/g) || []).length;
    return bad / s.length > 0.2;
  }
  var TH_MONTHS = ['ม.ค.', 'ก.พ.', 'มี.ค.', 'เม.ย.', 'พ.ค.', 'มิ.ย.', 'ก.ค.', 'ส.ค.', 'ก.ย.', 'ต.ค.', 'พ.ย.', 'ธ.ค.'];
  var EN_MONTHS = ['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec'];
  function fullYear(y) {
    y = +y;
    if (y < 100) y = y < 50 ? 2000 + y : 2500 + y;   // 两位年：49 及以下当公历 20xx，50 及以上当佛历 25xx（会计资料这几十年里不会混）
    return y > 2400 ? y - 543 : y;
  }
  function ymd(y, m, d) {
    y = fullYear(y);
    var dt = new Date(Date.UTC(y, m - 1, d));
    return dt.getUTCFullYear() === y && dt.getUTCMonth() === m - 1 && dt.getUTCDate() === d && y > 1900 && y < 2200 ? { y: y, m: m, d: d } : null;
  }
  // 一格文字 → 数字 / 日期 / 文字。账号、税号这类以 0 开头或超过 15 位的数字保持文字（不能被 Excel 吃掉前导 0）。
  function sheetCell(text) {
    var s = String(text == null ? '' : text).trim();
    if (!s) return null;
    var t = s.replace(/^฿\s*/, '').replace(/\s*(บาท|THB)$/i, '');
    var m = /^([-+])?\(?([\d,]*\d(?:\.\d+)?)\)?(-)?$/.exec(t.replace(/\s/g, ''));
    if (m && (!/,/.test(m[2]) || /^\d{1,3}(,\d{3})+(\.\d+)?$/.test(m[2]))) {
      var digits = m[2].replace(/[,.]/g, '');
      var paren = /^\(/.test(t) && /\)$/.test(t);
      if (digits.length <= 15 && !(/^0\d/.test(m[2]) && !/[.,]/.test(m[2]))) {
        var v = +m[2].replace(/,/g, '');
        if (m[1] === '-' || m[3] || paren) v = -v;
        var dec = (m[2].split('.')[1] || '').length;
        return { t: 'n', v: v, fmt: dec ? '#,##0.' + '0'.repeat(dec) : /,/.test(m[2]) ? '#,##0' : null };
      }
    }
    var d = /^(\d{1,2})[\/\-.](\d{1,2})[\/\-.](\d{2}|\d{4})$/.exec(s);
    if (d) { var a = ymd(d[3], +d[2], +d[1]); if (a) return { t: 'd', v: a }; }
    d = /^(\d{1,2})[\s\-]*([ก-๙.]+|[A-Za-z]{3,9})\.?[\s\-,]*(\d{2}|\d{4})$/.exec(s);
    if (d) {
      var mo = TH_MONTHS.indexOf(d[2]) + 1 || EN_MONTHS.indexOf(d[2].slice(0, 3).toLowerCase()) + 1 || MONTHS.indexOf(d[2]) + 1;
      var b = mo && ymd(d[3], mo, +d[1]);
      if (b) return { t: 'd', v: b };
    }
    return { t: 's', v: s };
  }

  // ---- Excel 转 PDF：按单元格的数字格式把值写成文字（常用的会计格式：千分位、小数位、括号负数、百分比、货币符号、日期） ----
  function pad2(n) { return (n < 10 ? '0' : '') + n; }
  function formatDate(dt, fmt) {
    var y = dt.getUTCFullYear(), m = dt.getUTCMonth() + 1, d = dt.getUTCDate();
    var hh = dt.getUTCHours(), mi = dt.getUTCMinutes(), ss = dt.getUTCSeconds();
    var f = String(fmt || '').replace(/\[[^\]]*\]/g, '').replace(/"[^"]*"/g, '').split(';')[0];
    // Excel 内置的“短日期”（14、22 号）按电脑地区显示，不是字面的月在前；泰国这边是 日/月/年
    if (f === 'mm-dd-yy') f = 'dd/mm/yyyy';
    if (f === 'm/d/yy h:mm') f = 'dd/mm/yyyy h:mm';
    // 时间部分先换掉（h 后面的 mm 是分钟，不是月份）
    f = f.replace(/h{1,2}(:mm)?(:ss)?(\s*AM\/PM)?/gi, function (tok) {
      var ampm = /AM\/PM/i.test(tok), H = ampm ? (hh % 12 || 12) : hh;
      return '\u0001' + (/^hh/i.test(tok) ? pad2(H) : H) + (/:mm/i.test(tok) ? ':' + pad2(mi) : '') + (/:ss/i.test(tok) ? ':' + pad2(ss) : '')
        + (ampm ? (hh < 12 ? ' AM' : ' PM') : '') + '\u0002';
    });
    if (!/[dy]/i.test(f.replace(/\u0001[^\u0002]*\u0002/g, ''))) {
      return /\u0001/.test(f) ? f.replace(/[\u0001\u0002]/g, '').trim() : pad2(d) + '/' + pad2(m) + '/' + y;
    }
    var monthsEn = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
    return f.replace(/yyyy|yy|mmmm|mmm|mm|m|dd|d|hh|h|ss|AM\/PM/gi, function (tok) {
      var k = tok.toLowerCase();
      if (k === 'yyyy') return String(y);
      if (k === 'yy') return String(y).slice(-2);
      if (k === 'mmmm' || k === 'mmm') return monthsEn[m - 1];
      if (k === 'mm') return pad2(m);
      if (k === 'm') return String(m);
      if (k === 'dd') return pad2(d);
      if (k === 'd') return String(d);
      return '';
    }).replace(/[\u0001\u0002]/g, '').replace(/[\s:]+$/, '');
  }
  function formatNumber(v, fmt) {
    fmt = String(fmt || 'General');
    if (/^general$/i.test(fmt) || !fmt) {
      if (Number.isInteger(v)) return String(v);
      return String(+v.toPrecision(10));
    }
    var parts = fmt.split(';'), sec = parts[0], neg = v < 0;
    if (neg && parts[1] !== undefined) sec = parts[1];
    else if (v === 0 && parts[2] !== undefined) sec = parts[2];
    // 引号里的文字和 [$฿-41E] 这类货币符号先换成私用区占位字符（不能用数字，免得被当成数字位），最后再换回来
    var lits = [], hold = function (t) { lits.push(t); return String.fromCharCode(0xE000 + lits.length - 1); };
    sec = sec.replace(/"([^"]*)"/g, function (m0, t) { return hold(t); })
      .replace(/\[\$([^\]-]*)(-[^\]]*)?\]/g, function (m0, sym) { return hold(sym); })
      .replace(/\[[^\]]*\]/g, '').replace(/_./g, ' ').replace(/\*./g, '').replace(/\\(.)/g, '$1');
    var back = function (t) { return t.replace(/[-]/g, function (ch) { return lits[ch.charCodeAt(0) - 0xE000]; }); };
    var pct = /%/.test(sec), body = /[0#?][0#?,.]*/.exec(sec);
    if (!body) return back(sec).trim();
    var spec = body[0], dec = (spec.split('.')[1] || '').replace(/[^0#?]/g, '').length, group = /,/.test(spec.split('.')[0]);
    var x = Math.abs(v) * (pct ? 100 : 1), txt = x.toFixed(dec);
    if (group) txt = txt.replace(/^(\d+)/, function (i) { return i.replace(/\B(?=(\d{3})+(?!\d))/g, ','); });
    if (!/0/.test(spec) && +txt === 0) txt = '';   // 只有 # 和 ? 的格式，值为 0 时什么数字都不显示（会计格式的零显示成 "-"）
    var out = back(sec.slice(0, body.index) + txt + sec.slice(body.index + spec.length));
    if (neg && parts[1] === undefined) out = '-' + out;
    return out.replace(/\s+$/, '').replace(/^\s+/, '');
  }

  var api = {
    readInteger: readInteger, parseAmount: parseAmount, bahtText: bahtText, toSatang: toSatang,
    vat: vat, withholding: withholding, formatSatang: formatSatang, taxId: taxId,
    crc16: crc16, promptpay: promptpay, year: year, thaiDate: thaiDate, pageRange: pageRange,
    rdFields: rdFields, rdCompany: rdCompany, rdExists: rdExists,
    cleanThai: cleanThai, pageLines: pageLines, tableFromPages: tableFromPages, garbled: garbled, sheetCell: sheetCell, formatNumber: formatNumber, formatDate: formatDate,
  };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (typeof document === 'undefined') return;
  root.PearnlyTools = api;   // files.js 用（页码范围）

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
    // 先用校验位挡掉打错的号码，校验通过再去税务局查；同一号码只查一次，旧请求的结果晚到就丢掉
    'tax-id': function (form) {
      var r = taxId(val(form, 'id'));
      if (!r.digits) return state(form, 'empty');
      if (!r.complete) return state(form, 'incomplete');
      if (!r.valid) return state(form, 'invalid');
      if (form._tin === r.digits) return;
      form._tin = r.digits;
      state(form, 'looking');
      lookupCompany(r.digits).then(function (res) {
        if (form._tin !== r.digits) return;
        if (res.company) {
          var c = res.company, head = form.getAttribute('data-head'), branch = form.getAttribute('data-branch');
          show(form, 'tin', r.formatted);
          show(form, 'name', c.name);
          show(form, 'branch', c.branch ? branch.replace('{0}', String(c.branch).padStart(5, '0')) : head);
          show(form, 'address', c.address);
          var m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(c.regBE), ce = m ? (+m[1] - 543) + '-' + m[2] + '-' + m[3] : '';
          var d = ce && thaiDate(ce);
          show(form, 'since', !d ? c.regBE : document.documentElement.lang === 'th' ? d.long : ce);
          return state(form, 'found');
        }
        state(form, res.status);
      });
    },
    // 二维码画在 canvas 上（qrcode-generator 只在这一页加载），下载成 PNG；下面写上收款账号和金额，方便打印贴在收银台
    'promptpay-qr': function (form) {
      var target = val(form, 'target').trim(), amount = val(form, 'amount');
      if (!target) return state(form, 'empty');
      var r = promptpay(target, amount);
      if (r.error) return state(form, r.error === 'amount' ? 'bad-amount' : 'bad-target');
      var canvas = $(form, 'canvas'), qr = root.qrcode && root.qrcode(0, 'M');
      if (!qr || !canvas) return state(form, 'empty');
      qr.addData(r.payload); qr.make();
      var n = qr.getModuleCount(), cell = Math.max(4, Math.floor(560 / (n + 8))), size = (n + 8) * cell;
      var ctx = canvas.getContext('2d');
      canvas.width = size; canvas.height = size;
      ctx.fillStyle = '#fff'; ctx.fillRect(0, 0, size, size); ctx.fillStyle = '#000';
      for (var y = 0; y < n; y++) for (var x = 0; x < n; x++) if (qr.isDark(y, x)) ctx.fillRect((x + 4) * cell, (y + 4) * cell, cell, cell);
      var t = toSatang(amount);
      show(form, 'who', target.replace(/[\s-]/g, ''));
      show(form, 'how-much', t === null || !String(amount).trim() ? '' : formatSatang(t) + ' ฿');
      state(form, 'ok');
    },
    'buddhist-year': function (form) {
      var y = year(val(form, 'year')), d = thaiDate(val(form, 'date'));
      var yo = $(form, '[data-year]'), dout = $(form, '[data-date]');
      if (yo) { yo.hidden = !y; if (y) { show(form, 'be', String(y.be)); show(form, 'ce', String(y.ce)); } }
      var bad = $(form, '[data-state="bad-year"]');
      if (bad) bad.hidden = !!y || !String(val(form, 'year')).trim();
      if (dout) { dout.hidden = !d; if (d) { show(form, 'long', d.long); show(form, 'short', d.short); show(form, 'numeric', d.numeric); } }
    },
  };

  var LOOKUPS = {};
  function getText(path) {
    return fetch(path, { credentials: 'omit' }).then(function (res) {
      if (res.status === 429) throw new Error('busy');
      if (!res.ok) throw new Error('error');
      return res.text();
    });
  }
  // → { company } 或 { status: 'not-vat' | 'not-found' | 'busy' | 'error' }
  function lookupCompany(tin) {
    if (LOOKUPS[tin]) return LOOKUPS[tin];
    var p = getText('/api/rd/vat?tin=' + tin).then(function (xml) {
      var company = rdCompany(xml);
      if (company) return { company: company };
      return getText('/api/rd/tin?tin=' + tin).then(function (t) { return { status: rdExists(t) ? 'not-vat' : 'not-found' }; });
    }).catch(function (e) { delete LOOKUPS[tin]; return { status: e && e.message === 'busy' ? 'busy' : 'error' }; });
    LOOKUPS[tin] = p;
    return p;
  }

  function bind(form) {
    var render = RENDER[form.getAttribute('data-tool')];
    if (!render) return;
    var run = function () { render(form); };
    form.addEventListener('input', run);
    form.addEventListener('change', run);
    form.addEventListener('submit', function (e) { e.preventDefault(); run(); });
    form.querySelectorAll('[data-copy]').forEach(function (copy) {
      if (!navigator.clipboard) return;
      copy.addEventListener('click', function () {
        var out = $(form, '[data-out="' + copy.getAttribute('data-copy') + '"]');
        if (out && out.textContent) navigator.clipboard.writeText(out.textContent);
      });
    });
    var save = $(form, '[data-save-png]');
    if (save) {
      save.addEventListener('click', function () {
        var canvas = $(form, 'canvas');
        if (!canvas || !canvas.toBlob) return;
        canvas.toBlob(function (blob) {
          var a = document.createElement('a');
          a.href = URL.createObjectURL(blob); a.download = save.getAttribute('data-save-png');
          document.body.appendChild(a); a.click(); a.remove();
          setTimeout(function () { URL.revokeObjectURL(a.href); }, 1000);
        }, 'image/png');
      });
    }
    run();
  }

  var forms = document.querySelectorAll('form[data-tool]');
  for (var i = 0; i < forms.length; i++) bind(forms[i]);
})(this);
