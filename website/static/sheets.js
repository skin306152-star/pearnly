/* pearnly.com 表格类免费工具：PDF 转 Excel、Excel 转 PDF。文件只在浏览器里读和生成，不上传。
 * 两个第三方库都在选了文件之后才加载（pdf.js 约 1.8 MB、ExcelJS 约 1 MB），打开页面不下载。
 * 表格还原、单元格识别、数字格式这些纯规则在 tools.js（PearnlyTools，有 node 测试）。
 * Excel 转 PDF 用浏览器自带的“打印 / 存为 PDF”：泰文的元音和声调要浏览器排字才不会叠错，PDF 里的字也能选中、搜索。 */
(function () {
  'use strict';
  var T = window.PearnlyTools;
  var VENDOR = '/static/vendor/', PDFJS = VENDOR + 'pdfjs-6.3.289/', EXCELJS = VENDOR + 'exceljs-4.4.0.min.js';
  // 免费工具的上限：超过就不在浏览器里硬算（会卡住标签页），提示联系 Pearnly 团队用正式版
  var MAX_PAGES = 200, MAX_CELLS = 6000;

  function $(root, sel) { return root.querySelector(sel); }
  function show(form, name, text) {
    form.querySelectorAll('[data-state]').forEach(function (n) {
      n.hidden = n.getAttribute('data-state') !== name;
      if (!n.hidden && text != null && n.hasAttribute('data-fill')) n.textContent = n.getAttribute('data-fill').replace('{0}', text);
    });
  }
  function save(blob, name) {
    var a = document.createElement('a');
    a.href = URL.createObjectURL(blob); a.download = name;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(function () { URL.revokeObjectURL(a.href); }, 2000);
  }
  function baseName(file) { return file.name.replace(/\.[^.]+$/, ''); }

  var libs = {};
  function loadScript(src) {
    return libs[src] || (libs[src] = new Promise(function (resolve, reject) {
      var s = document.createElement('script');
      s.src = src; s.onload = resolve; s.onerror = function () { delete libs[src]; reject(new Error('load')); };
      document.head.appendChild(s);
    }));
  }
  function excel() { return loadScript(EXCELJS).then(function () { return window.ExcelJS; }); }
  function pdfjs() {
    return libs.pdf || (libs.pdf = import(PDFJS + 'pdf.min.js').then(function (lib) {
      lib.GlobalWorkerOptions.workerSrc = PDFJS + 'pdf.worker.min.js';
      return lib;
    }).catch(function (e) { delete libs.pdf; throw e; }));
  }

  // 拖进来或点选；一次一个文件
  function picker(form, accept, onFile) {
    var input = $(form, 'input[type=file]'), drop = $(form, '[data-drop]'), name = $(form, '[data-file-name]');
    function take(files) {
      var f = Array.prototype.filter.call(files || [], accept)[0];
      if (!f) return;
      if (name) { name.textContent = f.name; name.hidden = false; }
      onFile(f);
    }
    input.addEventListener('change', function () { take(input.files); input.value = ''; });
    ['dragenter', 'dragover'].forEach(function (e) { drop.addEventListener(e, function (ev) { ev.preventDefault(); drop.classList.add('over'); }); });
    ['dragleave', 'drop'].forEach(function (e) { drop.addEventListener(e, function () { drop.classList.remove('over'); }); });
    drop.addEventListener('drop', function (ev) { ev.preventDefault(); take(ev.dataTransfer.files); });
  }

  // ---------------- PDF 转 Excel ----------------
  function pdfToExcel(form) {
    var result = null, file = null, go = $(form, '[data-go]'), preview = $(form, '[data-preview]');
    function render(rows, pages) {
      var table = $(preview, 'table'), body = document.createElement('tbody'), width = rows.reduce(function (m, r) { return Math.max(m, r.length); }, 0);
      rows.slice(0, 40).forEach(function (r) {
        var tr = document.createElement('tr');
        for (var k = 0; k < width; k++) {
          var td = document.createElement('td'), c = T.sheetCell(r[k]);
          td.textContent = r[k] || '';
          if (c && c.t !== 's') td.className = 'num';
          tr.appendChild(td);
        }
        body.appendChild(tr);
      });
      table.replaceChildren(body);
      $(preview, '[data-stats]').textContent = $(preview, '[data-stats]').getAttribute('data-fill')
        .replace('{pages}', pages).replace('{rows}', rows.length).replace('{cols}', width);
      preview.hidden = false;
    }
    function read(f) {
      file = f; result = null; preview.hidden = true; go.disabled = true; show(form, 'busy');
      var task = null;
      Promise.all([pdfjs(), f.arrayBuffer()]).then(function (got) {
        task = got[0].getDocument({ data: new Uint8Array(got[1]), cMapUrl: PDFJS + 'cmaps/', cMapPacked: true, isEvalSupported: false,
                                    disableFontFace: true, useSystemFonts: false });
        return task.promise;
      }).then(function (doc) {
        if (doc.numPages > MAX_PAGES) { task.destroy(); show(form, 'too-big'); return null; }
        var n = doc.numPages, pages = [], chain = Promise.resolve();
        for (var p = 1; p <= n; p++) {
          (function (p) {
            chain = chain.then(function () { return doc.getPage(p); }).then(function (page) { return page.getTextContent(); }).then(function (tc) {
              pages.push(tc.items.map(function (it) {
                var h = Math.abs(it.transform[3]) || Math.abs(it.height) || 10;
                return { str: it.str, x0: it.transform[4], x1: it.transform[4] + it.width, y: it.transform[5], h: h };
              }));
            });
          })(p);
        }
        return chain.then(function () { task.destroy(); return pages; });
      }).then(function (pages) {
        if (!pages) return;
        var text = pages.map(function (p) { return p.map(function (i) { return i.str; }).join(''); }).join('');
        if (!text.replace(/\s/g, '')) return show(form, 'scanned');
        if (T.garbled(T.cleanThai(text))) return show(form, 'garbled');
        var rows = T.tableFromPages(pages);
        if (!rows.length) return show(form, 'scanned');
        result = { rows: rows, pages: pages.length };
        render(rows, pages.length);
        go.disabled = false;
        show(form, 'ready');
      }).catch(function (e) {
        if (task) task.destroy();
        show(form, /password/i.test(e && e.name + e.message) ? 'locked' : 'unreadable');
      });
    }
    picker(form, function (f) { return f.type === 'application/pdf' || /\.pdf$/i.test(f.name); }, read);
    go.addEventListener('click', function () {
      if (!result) return;
      go.disabled = true; show(form, 'busy');
      excel().then(function (X) {
        var wb = new X.Workbook(), ws = wb.addWorksheet('PDF');
        var widths = [];
        result.rows.forEach(function (r, i) {
          var row = ws.getRow(i + 1);
          r.forEach(function (text, k) {
            var c = T.sheetCell(text);
            widths[k] = Math.max(widths[k] || 6, Math.min(60, String(text || '').length + 2));
            if (!c) return;
            var cell = row.getCell(k + 1);
            if (c.t === 'n') { cell.value = c.v; if (c.fmt) cell.numFmt = c.fmt; }
            else if (c.t === 'd') { cell.value = new Date(Date.UTC(c.v.y, c.v.m - 1, c.v.d)); cell.numFmt = 'dd/mm/yyyy'; }
            else cell.value = c.v;
          });
        });
        ws.columns.forEach(function (col, k) { col.width = widths[k] || 10; });
        return wb.xlsx.writeBuffer();
      }).then(function (buf) {
        save(new Blob([buf], { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' }), baseName(file) + '.xlsx');
        show(form, 'done');
      }).catch(function () { show(form, 'failed'); }).then(function () { go.disabled = !result; });
    });
  }

  // ---------------- Excel 转 PDF ----------------
  var PX_PER_CHAR = 7.2;   // Excel 列宽单位（字符数）→ 像素，约等于默认字体 Calibri 11 的一个字宽
  function argb(c) {
    var v = c && c.argb;
    return v && /^[0-9A-F]{8}$/i.test(v) && v.slice(0, 2) !== '00' ? '#' + v.slice(2) : null;
  }
  function cellText(cell) {
    var v = cell.value;
    if (v == null) return '';
    if (typeof v === 'object' && !(v instanceof Date)) {
      if (v.richText) return v.richText.map(function (r) { return r.text; }).join('');
      if (v.text != null) return v.text.richText ? v.text.richText.map(function (r) { return r.text; }).join('') : String(v.text);   // 超链接（文字可能带格式）
      if (v.error) return v.error;                         // #REF! 这类错误值照 Excel 显示
      // 公式（含共享公式）：显示文件里存的计算结果；没存结果（有的程序导出时不算）就留空，不显示公式本身
      if ('formula' in v || 'sharedFormula' in v || 'result' in v) v = v.result;
      if (v == null) return '';
      if (typeof v === 'object' && !(v instanceof Date)) return v.error || '';
    }
    if (v instanceof Date) return T.formatDate(v, cell.numFmt);
    if (typeof v === 'number') return T.formatNumber(v, cell.numFmt);
    if (typeof v === 'boolean') return v ? 'TRUE' : 'FALSE';
    return String(v);
  }
  function border(b) {
    if (!b || !b.style) return '';
    var w = /thick/.test(b.style) ? 2 : /medium/.test(b.style) ? 1.5 : 1, kind = /dash|dot/.test(b.style) ? 'dashed' : /double/.test(b.style) ? 'double' : 'solid';
    return (/double/.test(b.style) ? 3 : w) + 'px ' + kind + ' ' + (argb(b.color) || '#000');
  }
  // 一张工作表 → 一个 HTML 表格（列宽、行高、合并、字体、底色、边框、对齐、数字格式；隐藏的行列跳过；只取到最后一个有内容的格）
  function sheetTable(ws) {
    var maxR = 0, maxC = 0, merges = {}, covered = {};
    ws.eachRow({ includeEmpty: false }, function (row, r) {
      row.eachCell({ includeEmpty: false }, function (cell, c) { if (cellText(cell) !== '' || cell.fill && cell.fill.fgColor) { maxR = Math.max(maxR, r); maxC = Math.max(maxC, c); } });
    });
    if (!maxR) return null;
    var area = ws.pageSetup && ws.pageSetup.printArea;
    var m = area && /^\$?([A-Z]+)\$?(\d+):\$?([A-Z]+)\$?(\d+)$/.exec(String(area).split(',')[0].split('!').pop());
    var colNo = function (s) { return s.split('').reduce(function (n, ch) { return n * 26 + ch.charCodeAt(0) - 64; }, 0); };
    var r0 = 1, c0 = 1;
    if (m) { c0 = colNo(m[1]); r0 = +m[2]; maxC = colNo(m[3]); maxR = +m[4]; }
    (ws.model.merges || []).forEach(function (ref) {
      var mm = /^([A-Z]+)(\d+):([A-Z]+)(\d+)$/.exec(ref);
      if (!mm) return;
      var a = { r: +mm[2], c: colNo(mm[1]) }, b = { r: +mm[4], c: colNo(mm[3]) };
      merges[a.r + ':' + a.c] = { rows: b.r - a.r + 1, cols: b.c - a.c + 1 };
      for (var r = a.r; r <= b.r; r++) for (var c = a.c; c <= b.c; c++) if (r !== a.r || c !== a.c) covered[r + ':' + c] = 1;
    });
    var table = document.createElement('table'), colgroup = document.createElement('colgroup'), total = 0;
    var hiddenCol = {};
    for (var c = c0; c <= maxC; c++) {
      var col = ws.getColumn(c);
      if (col.hidden) { hiddenCol[c] = 1; continue; }
      var px = Math.round((col.width || 8.43) * PX_PER_CHAR + 5);
      var el = document.createElement('col'); el.style.width = px + 'px'; colgroup.appendChild(el); total += px;
    }
    table.appendChild(colgroup);
    var tbody = document.createElement('tbody');
    for (var r = r0; r <= maxR; r++) {
      var row = ws.getRow(r);
      if (row.hidden) continue;
      var tr = document.createElement('tr');
      if (row.height) tr.style.height = Math.round(row.height * 4 / 3) + 'px';
      for (c = c0; c <= maxC; c++) {
        if (hiddenCol[c] || covered[r + ':' + c]) continue;
        var cell = row.getCell(c), td = document.createElement('td'), span = merges[r + ':' + c];
        if (span) { if (span.rows > 1) td.rowSpan = span.rows; if (span.cols > 1) td.colSpan = span.cols; }
        var text = cellText(cell), st = td.style, f = cell.font || {}, al = cell.alignment || {}, b = cell.border || {};
        td.textContent = text;
        if (f.bold) st.fontWeight = '700';
        if (f.italic) st.fontStyle = 'italic';
        if (f.underline) st.textDecoration = 'underline';
        if (f.size) st.fontSize = f.size + 'pt';
        if (f.name) st.fontFamily = '"' + f.name.replace(/"/g, '') + '", Calibri, "Sarabun", "TH Sarabun New", "Leelawadee UI", "Noto Sans Thai", Tahoma, sans-serif';
        if (argb(f.color)) st.color = argb(f.color);
        var fill = cell.fill && cell.fill.type === 'pattern' && cell.fill.pattern === 'solid' && argb(cell.fill.fgColor);
        if (fill) st.background = fill;
        var num = typeof cell.value === 'number' || cell.value && typeof cell.value === 'object' && typeof cell.value.result === 'number';
        // 和 Excel 一样：没换行的文字超出格子时，右边一格空着就延伸过去显示，右边有内容才截断（数字不延伸）
        if (text && !num && !al.wrapText && c < maxC && !cellText(row.getCell(c + 1))) td.className = 'spill';
        st.textAlign = al.horizontal === 'centerContinuous' ? 'center' : al.horizontal && al.horizontal !== 'general' ? al.horizontal : num ? 'right' : 'left';
        st.verticalAlign = al.vertical === 'middle' ? 'middle' : al.vertical === 'top' ? 'top' : 'bottom';
        if (al.wrapText) st.whiteSpace = 'pre-wrap';
        if (al.indent) st.paddingLeft = (al.indent * 9 + 3) + 'px';
        ['top', 'right', 'bottom', 'left'].forEach(function (side) { var v = border(b[side]); if (v) st['border' + side[0].toUpperCase() + side.slice(1)] = v; });
        tr.appendChild(td);
      }
      tbody.appendChild(tr);
    }
    table.appendChild(tbody);
    table.style.width = total + 'px';   // 固定布局要有表格总宽，列宽才按 Excel 的来
    var ps = ws.pageSetup || {};
    return { table: table, width: total, landscape: ps.orientation === 'landscape', fit: !!(ps.fitToPage && ps.fitToWidth) };
  }
  function excelToPdf(form) {
    var go = $(form, '[data-go]'), preview = $(form, '[data-preview]'), sheets = [], file = null;
    var root = document.getElementById('sheet-print') || document.body.appendChild(Object.assign(document.createElement('div'), { id: 'sheet-print' }));
    var pageStyle = document.getElementById('sheet-page') || document.head.appendChild(Object.assign(document.createElement('style'), { id: 'sheet-page' }));
    // 每个工作表各自的纸张方向：选“自动”时，Excel 里设成横向、或表格比纵向 A4 宽的用横向（CSS 命名页，一份 PDF 里可以纵横混排）
    function layout() {
      var orient = form.elements.orient.value, fit = form.elements.fit.checked;
      pageStyle.textContent = '@page { size: A4 portrait; margin: 12mm; } @page sheet-portrait { size: A4 portrait; margin: 12mm; } @page sheet-landscape { size: A4 landscape; margin: 12mm; }';
      root.replaceChildren();
      sheets.forEach(function (s, i) {
        var landscape = orient === 'landscape' || orient === 'auto' && (s.landscape || s.width > 760);
        var printable = landscape ? 1020 : 700;   // A4 减去 12mm 页边距后的宽度（96 dpi）
        var sec = document.createElement('section');
        sec.className = 'sheet-page';
        sec.style.page = landscape ? 'sheet-landscape' : 'sheet-portrait';
        var t = s.table.cloneNode(true);
        if ((fit || s.fit) && s.width > printable) t.style.zoom = String(Math.floor(printable / s.width * 1000) / 1000);
        sec.appendChild(t);
        root.appendChild(sec);
      });
      var box = $(preview, '[data-sheet-preview]');
      box.replaceChildren(root.cloneNode(true));
      box.firstChild.removeAttribute('id');
    }
    function read(f) {
      file = f; sheets = []; preview.hidden = true; go.disabled = true; show(form, 'busy');
      Promise.all([excel(), f.arrayBuffer()]).then(function (got) {
        var wb = new got[0].Workbook();
        return wb.xlsx.load(got[1]).then(function () { return wb; });
      }).then(function (wb) {
        var cells = 0;
        wb.worksheets.forEach(function (ws) { if (ws.state === 'visible' || !ws.state) ws.eachRow({ includeEmpty: false }, function (row) { cells += row.actualCellCount; }); });
        if (cells > MAX_CELLS) return show(form, 'too-big');
        wb.worksheets.forEach(function (ws) { if (ws.state === 'visible' || !ws.state) { var t = sheetTable(ws); if (t) sheets.push(t); } });
        if (!sheets.length) return show(form, 'empty-book');
        layout();
        preview.hidden = false; go.disabled = false;
        show(form, 'ready', String(sheets.length));
      }).catch(function () { show(form, /\.xls$/i.test(f.name) ? 'old-xls' : 'unreadable'); });
    }
    picker(form, function (f) { return /\.(xlsx|xlsm|xls)$/i.test(f.name); }, read);
    form.addEventListener('change', function (e) { if ((e.target.name === 'orient' || e.target.name === 'fit') && sheets.length) layout(); });
    go.addEventListener('click', function () {
      if (!sheets.length) return;
      var title = document.title;
      document.title = baseName(file);   // 浏览器存 PDF 时默认用页面标题当文件名
      document.body.classList.add('printing-sheet');
      var done = function () { document.body.classList.remove('printing-sheet'); document.title = title; window.removeEventListener('afterprint', done); };
      window.addEventListener('afterprint', done);
      window.print();
      setTimeout(done, 60000);   // 个别浏览器不发 afterprint：一分钟后也恢复
    });
  }

  // 一个页面两个方向：上面一排切换，地址带 #excel-to-pdf 时直接打开第二个
  document.querySelectorAll('[data-sheet-mode]').forEach(function (seg) {
    var root = seg.parentNode;
    function pick(v) { root.querySelectorAll('[data-sheet-pane]').forEach(function (p) { p.hidden = p.getAttribute('data-sheet-pane') !== v; }); }
    seg.addEventListener('change', function (e) { if (e.target.name === 'sheet-mode') pick(e.target.value); });
    if (location.hash === '#excel-to-pdf') { seg.querySelector('input[value="excel-to-pdf"]').checked = true; pick('excel-to-pdf'); }
  });

  var TOOLS = { 'pdf-to-excel': pdfToExcel, 'excel-to-pdf': excelToPdf };
  document.querySelectorAll('form[data-sheet-tool]').forEach(function (form) {
    form.addEventListener('submit', function (e) { e.preventDefault(); });
    TOOLS[form.getAttribute('data-sheet-tool')](form);
  });
})();
