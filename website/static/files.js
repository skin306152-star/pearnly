/* pearnly.com 文件类免费工具：图片转 PDF、合并 / 拆分 PDF。文件只在浏览器里读和生成，不上传、不发请求。
 * 生成 PDF 用 static/vendor 里的 pdf-lib（只在这两页加载）；页码范围的解析在 tools.js（pageRange，有 node 测试）。 */
(function () {
  'use strict';
  var Lib = window.PDFLib, T = window.PearnlyTools;
  var A4 = [595.28, 841.89], MARGIN = 28, LONG_SIDE = 2480;   // A4（pt）、页边距；图片长边缩到 2480 像素（A4 300dpi），文件不会太大

  function $(root, sel) { return root.querySelector(sel); }
  function show(root, name) {
    root.querySelectorAll('[data-state]').forEach(function (n) { n.hidden = n.getAttribute('data-state') !== name; });
  }
  function save(bytes, name) {
    var a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([bytes], { type: 'application/pdf' }));
    a.download = name;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(function () { URL.revokeObjectURL(a.href); }, 2000);
  }
  function readBytes(file) { return file.arrayBuffer().then(function (b) { return new Uint8Array(b); }); }
  function sizeText(n) { return n < 1048576 ? Math.max(1, Math.round(n / 1024)) + ' KB' : (n / 1048576).toFixed(1) + ' MB'; }

  // 文件列表：可以上下移、删掉；拖进来或点选都行
  function fileList(form, accept, onChange) {
    var items = [], list = $(form, '[data-list]'), input = $(form, 'input[type=file]'), drop = $(form, '[data-drop]');
    var labels = { up: list.getAttribute('data-up'), down: list.getAttribute('data-down'), remove: list.getAttribute('data-remove'), pages: list.getAttribute('data-pages') };
    function render() {
      list.textContent = '';
      items.forEach(function (it, i) {
        var li = document.createElement('li');
        var thumb = document.createElement(it.thumb ? 'img' : 'span');
        thumb.className = 'thumb';
        if (it.thumb) { thumb.src = it.thumb; thumb.alt = ''; } else thumb.textContent = 'PDF';
        var name = document.createElement('span');
        name.className = 'name'; name.textContent = it.file.name;
        var meta = document.createElement('span');
        meta.className = 'file-meta'; meta.textContent = sizeText(it.file.size) + (it.pages ? ' · ' + it.pages + ' ' + labels.pages : '');
        var tools = document.createElement('span');
        tools.className = 'row-actions';
        [['up', '↑', i === 0], ['down', '↓', i === items.length - 1], ['remove', '×', false]].forEach(function (b) {
          var btn = document.createElement('button');
          btn.type = 'button'; btn.textContent = b[1]; btn.disabled = b[2]; btn.setAttribute('aria-label', labels[b[0]] + ' · ' + it.file.name);
          btn.addEventListener('click', function () {
            if (b[0] === 'remove') { if (it.thumb) URL.revokeObjectURL(it.thumb); items.splice(i, 1); }
            else { var j = b[0] === 'up' ? i - 1 : i + 1, tmp = items[j]; items[j] = items[i]; items[i] = tmp; }
            render();
          });
          tools.appendChild(btn);
        });
        li.appendChild(thumb); li.appendChild(name); li.appendChild(meta); li.appendChild(tools);
        list.appendChild(li);
      });
      list.hidden = !items.length;
      onChange(items);
    }
    function add(files) {
      var picked = Array.prototype.filter.call(files || [], accept);
      Promise.all(picked.map(function (f) { return Promise.resolve(onChange.inspect ? onChange.inspect(f) : {}).then(function (extra) { return Object.assign({ file: f }, extra); }); }))
        .then(function (more) { items = items.concat(more.filter(Boolean)); render(); });
    }
    input.addEventListener('change', function () { add(input.files); input.value = ''; });
    if (drop) {
      ['dragenter', 'dragover'].forEach(function (e) { drop.addEventListener(e, function (ev) { ev.preventDefault(); drop.classList.add('over'); }); });
      ['dragleave', 'drop'].forEach(function (e) { drop.addEventListener(e, function () { drop.classList.remove('over'); }); });
      drop.addEventListener('drop', function (ev) { ev.preventDefault(); add(ev.dataTransfer.files); });
    }
    return { items: function () { return items; }, reset: function (list2) { items = list2; render(); } };
  }

  // 一张图片 → 画到 canvas（浏览器会按拍照方向摆正；PNG / WebP 等都转成 JPEG）→ JPEG 字节和像素尺寸
  function toJpeg(file) {
    return new Promise(function (resolve, reject) {
      var img = new Image(), url = URL.createObjectURL(file);
      img.onload = function () {
        var k = Math.min(1, LONG_SIDE / Math.max(img.naturalWidth, img.naturalHeight));
        var w = Math.round(img.naturalWidth * k), h = Math.round(img.naturalHeight * k);
        var c = document.createElement('canvas');
        c.width = w; c.height = h;
        var ctx = c.getContext('2d');
        ctx.fillStyle = '#fff'; ctx.fillRect(0, 0, w, h); ctx.drawImage(img, 0, 0, w, h);
        URL.revokeObjectURL(url);
        c.toBlob(function (blob) {
          if (!blob) return reject(new Error('encode'));
          blob.arrayBuffer().then(function (b) { resolve({ bytes: new Uint8Array(b), w: w, h: h }); });
        }, 'image/jpeg', 0.86);
      };
      img.onerror = function () { URL.revokeObjectURL(url); reject(new Error('decode')); };
      img.src = url;
    });
  }

  function imageToPdf(form) {
    var go = $(form, '[data-go]'), list;
    function ready(items) { go.disabled = !items.length; show(form, items.length ? 'ready' : 'empty'); }
    ready.inspect = function (f) { return { thumb: URL.createObjectURL(f) }; };
    list = fileList(form, function (f) { return /^image\//.test(f.type) || /\.(jpe?g|png|webp|gif|bmp)$/i.test(f.name); }, ready);
    go.addEventListener('click', function () {
      var items = list.items(), fit = form.elements.size.value === 'fit';
      if (!items.length) return;
      go.disabled = true; show(form, 'busy');
      var doc;
      Lib.PDFDocument.create().then(function (d) {
        doc = d;
        return items.reduce(function (p, it) {
          return p.then(function () { return toJpeg(it.file); }).then(function (j) {
            return doc.embedJpg(j.bytes).then(function (img) {
              var pw, ph, w, h;
              if (fit) { pw = j.w * 72 / 150; ph = j.h * 72 / 150; w = pw; h = ph; }   // 按 150dpi 算页面大小，图片铺满整页
              else {
                var land = j.w > j.h;
                pw = land ? A4[1] : A4[0]; ph = land ? A4[0] : A4[1];
                var k = Math.min((pw - 2 * MARGIN) / j.w, (ph - 2 * MARGIN) / j.h);
                w = j.w * k; h = j.h * k;
              }
              doc.addPage([pw, ph]).drawImage(img, { x: (pw - w) / 2, y: (ph - h) / 2, width: w, height: h });
            });
          });
        }, Promise.resolve());
      }).then(function () { return doc.save(); }).then(function (bytes) {
        save(bytes, form.getAttribute('data-name'));
        show(form, 'done');
      }).catch(function () { show(form, 'failed'); }).then(function () { go.disabled = !list.items().length; });
    });
    ready([]);
  }

  // 读 PDF：有密码的（加密）直接报，不尝试破解
  function loadPdf(bytes) { return Lib.PDFDocument.load(bytes, { updateMetadata: false }); }

  function mergeSplit(form) {
    var go = $(form, '[data-go]'), pages = form.elements.pages, list, note = '';
    function mode() { return form.elements.mode.value; }
    function ready(items) {
      var split = mode() === 'split';
      $(form, '[data-split]').hidden = !split;
      var total = items[0] && items[0].pages;
      $(form, '[data-total]').textContent = total ? String(total) : '–';
      var ok = split ? items.length === 1 && !!T.pageRange(pages.value, total) : items.length >= 2;
      go.disabled = !ok;
      $(form, '[data-go-merge]').hidden = split; $(form, '[data-go-split]').hidden = !split;
      var bad = split && items.length === 1 && pages.value.trim() && !T.pageRange(pages.value, total);
      show(form, note || (bad ? 'bad-pages' : split && items.length > 1 ? 'one-file' : !split && items.length === 1 ? 'need-two' : 'ready'));
      note = '';   // 读不了的文件只提示一次，下一次操作恢复正常提示
    }
    ready.inspect = function (f) {
      return readBytes(f).then(loadPdf).then(function (d) { return { pages: d.getPageCount() }; })
        .catch(function (e) { note = /encrypt/i.test(String(e && e.message)) ? 'locked' : 'unreadable'; return null; });
    };
    list = fileList(form, function (f) { return f.type === 'application/pdf' || /\.pdf$/i.test(f.name); }, ready);
    form.addEventListener('change', function (e) { if (e.target.name === 'mode') ready(list.items()); });
    pages.addEventListener('input', function () { ready(list.items()); });
    go.addEventListener('click', function () {
      var items = list.items(), split = mode() === 'split', result = 'failed';
      go.disabled = true; show(form, 'busy');
      Lib.PDFDocument.create().then(function (out) {
        var chain = Promise.resolve();
        items.forEach(function (it) {
          chain = chain.then(function () { return readBytes(it.file).then(loadPdf); }).then(function (src) {
            var idx = split ? T.pageRange(pages.value, src.getPageCount()) : src.getPageIndices();
            return out.copyPages(src, idx).then(function (copied) { copied.forEach(function (p) { out.addPage(p); }); });
          });
        });
        return chain.then(function () { return out.save(); });
      }).then(function (bytes) {
        var base = items[0].file.name.replace(/\.pdf$/i, '');
        save(bytes, split ? base + ' (' + pages.value.replace(/\s/g, '') + ').pdf' : form.getAttribute('data-name'));
        result = 'done';
      }).catch(function (e) { if (/encrypt/i.test(String(e && e.message))) result = 'locked'; })
        .then(function () { ready(list.items()); show(form, result); });
    });
    ready([]);
  }

  var TOOLS = { 'image-to-pdf': imageToPdf, 'merge-split-pdf': mergeSplit };
  document.querySelectorAll('form[data-file-tool]').forEach(function (form) {
    form.addEventListener('submit', function (e) { e.preventDefault(); });
    if (!Lib) return show(form, 'failed');
    TOOLS[form.getAttribute('data-file-tool')](form);
  });
})();
