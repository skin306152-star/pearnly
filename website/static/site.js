/* pearnly.com 站点交互：点阵背景（随鼠标起伏）、滚动渐入、顶栏、首页演示、总览视频播放。
 * 不发任何请求；系统开了“减少动态效果”时全部静止。 */
(function () {
  'use strict';
  var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  // ---- 点阵背景 ----
  function dots() {
    var canvas = document.querySelector('canvas.dots');
    if (!canvas || !canvas.getContext) return;
    var ctx = canvas.getContext('2d');
    var GAP = 22, R = 1, REACH = 150;
    var w = 0, h = 0, dpr = 1;
    var mouse = { x: -9999, y: -9999, tx: -9999, ty: -9999 };
    var running = false, t0 = performance.now();

    function size() {
      dpr = Math.min(window.devicePixelRatio || 1, 2);
      w = window.innerWidth; h = window.innerHeight;
      canvas.width = Math.round(w * dpr); canvas.height = Math.round(h * dpr);
      canvas.style.width = w + 'px'; canvas.style.height = h + 'px';
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }

    function draw(now) {
      var t = (now - t0) / 1000;
      mouse.x += (mouse.tx - mouse.x) * 0.12;
      mouse.y += (mouse.ty - mouse.y) * 0.12;
      ctx.clearRect(0, 0, w, h);
      var offset = (window.scrollY || 0) % GAP;
      for (var y = -offset; y < h + GAP; y += GAP) {
        for (var x = GAP / 2; x < w; x += GAP) {
          // 缓慢的斜向波纹，让点阵“呼吸”
          var wave = reduce ? 0 : 0.5 + 0.5 * Math.sin(t * 0.7 - (x + y) * 0.008);
          var a = 0.10 + wave * 0.06, r = R, px = x, py = y;
          var dx = x - mouse.x, dy = y - mouse.y, d = Math.sqrt(dx * dx + dy * dy);
          if (d < REACH) {
            var k = 1 - d / REACH; k = k * k;
            a += k * 0.45; r += k * 1.3;
            if (d > 0.1) { px += dx / d * k * 7; py += dy / d * k * 7; }
          }
          ctx.globalAlpha = a;
          ctx.beginPath(); ctx.arc(px, py, r, 0, 6.2832); ctx.fill();
        }
      }
      if (running) requestAnimationFrame(draw);
    }

    function start() { if (!running && !reduce && !document.hidden) { running = true; requestAnimationFrame(draw); } }
    function stop() { running = false; }

    ctx.fillStyle = '#141413';
    size();
    draw(performance.now());
    window.addEventListener('resize', function () { size(); draw(performance.now()); });
    window.addEventListener('scroll', function () { if (reduce) draw(performance.now()); }, { passive: true });
    if (reduce) return;
    window.addEventListener('pointermove', function (e) { mouse.tx = e.clientX; mouse.ty = e.clientY; }, { passive: true });
    document.addEventListener('pointerleave', function () { mouse.tx = mouse.ty = -9999; });
    document.addEventListener('visibilitychange', function () { if (document.hidden) stop(); else start(); });
    start();
  }

  // ---- 滚动渐入 ----
  function reveal() {
    var items = document.querySelectorAll('.reveal');
    if (reduce || !('IntersectionObserver' in window)) {
      for (var i = 0; i < items.length; i++) items[i].classList.add('in');
      return;
    }
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) { if (e.isIntersecting) { e.target.classList.add('in'); io.unobserve(e.target); } });
    }, { rootMargin: '0px 0px -8% 0px' });
    for (var j = 0; j < items.length; j++) io.observe(items[j]);
  }

  // ---- 顶栏：滚过 40px 后从透明全宽条收成胶囊 ----
  function topbar() {
    var bar = document.querySelector('.topbar');
    if (!bar) return;
    var on = function () { bar.classList.toggle('scrolled', window.scrollY > 40); };
    window.addEventListener('scroll', on, { passive: true });
    on();
  }

  // ---- 首页演示：扫描单据 → 字段逐个出现 → 完成 → 换下一个产品 ----
  function demo() {
    var root = document.querySelector('.demo');
    if (!root) return;
    var tabs = root.querySelectorAll('[data-tab]');
    var bar = root.querySelector('.progress i');
    var order = [], timers = [], current = 0, visible = false, started = false;
    for (var i = 0; i < tabs.length; i++) order.push(tabs[i].getAttribute('data-tab'));

    function clear() { timers.forEach(clearTimeout); timers = []; }
    function later(fn, ms) { timers.push(setTimeout(fn, ms)); }

    function play(index, auto) {
      clear();
      current = index;
      var name = order[index];
      for (var i = 0; i < tabs.length; i++) tabs[i].setAttribute('aria-selected', String(i === index));
      var scenes = root.querySelectorAll('.scene');
      var scene;
      for (var s = 0; s < scenes.length; s++) {
        var active = scenes[s].getAttribute('data-scene') === name;
        scenes[s].classList.toggle('active', active);
        if (active) scene = scenes[s];
      }
      var fields = scene.querySelectorAll('.f');
      var msgs = scene.querySelectorAll('.msg');
      var done = scene.querySelector('.done');
      var doc = scene.querySelector('.doc');
      scene.querySelectorAll('.on, .hit').forEach(function (el) { el.classList.remove('on', 'hit'); });
      done.classList.remove('on');
      if (reduce) {
        fields.forEach(function (f) { f.classList.add('on'); });
        msgs.forEach(function (m) { m.classList.add('on'); });
        done.classList.add('on');
        return;
      }
      var at = 200;
      msgs.forEach(function (m) { later(function () { m.classList.add('on'); }, at); at += 650; });
      later(function () { doc.classList.add('scanning'); }, at);
      at += 500;
      fields.forEach(function (f) {
        later(function () {
          f.classList.add('on');
          var hit = doc.querySelector('[data-k="' + f.getAttribute('data-k') + '"]');
          if (hit) { hit.classList.add('hit'); later(function () { hit.classList.remove('hit'); }, 700); }
        }, at);
        at += 420;
      });
      later(function () { doc.classList.remove('scanning'); done.classList.add('on'); }, at + 100);
      var total = at + 3200;
      if (bar) {
        bar.style.transition = 'none'; bar.style.width = '0%';
        void bar.offsetWidth;
        bar.style.transition = 'width ' + total + 'ms linear'; bar.style.width = '100%';
      }
      if (auto !== false) later(function () { if (visible) play((current + 1) % order.length); else started = false; }, total);
    }

    for (var t = 0; t < tabs.length; t++) {
      (function (index) { tabs[index].addEventListener('click', function () { play(index); }); })(t);
    }
    // 悬停字段时在单据上标出对应位置
    root.addEventListener('pointerover', function (e) {
      var f = e.target.closest && e.target.closest('.f');
      root.querySelectorAll('.doc .hl').forEach(function (el) { el.classList.remove('hl'); });
      if (!f) return;
      var scene = f.closest('.scene');
      var hit = scene.querySelector('.doc [data-k="' + f.getAttribute('data-k') + '"]');
      if (hit) hit.classList.add('hl');
    });
    root.addEventListener('pointerleave', function () {
      root.querySelectorAll('.doc .hl').forEach(function (el) { el.classList.remove('hl'); });
    });

    if (!('IntersectionObserver' in window)) { visible = true; play(0); return; }
    new IntersectionObserver(function (entries) {
      visible = entries[0].isIntersecting;
      if (visible && !started) { started = true; play(current); }
    }, { threshold: 0.35 }).observe(root);
  }

  // ---- 总览视频：自定义播放按钮 ----
  function players() {
    document.querySelectorAll('.player').forEach(function (box) {
      var video = box.querySelector('video'), btn = box.querySelector('.playbtn');
      if (!video || !btn) return;
      box.classList.add('ready');
      video.controls = false;
      btn.addEventListener('click', function () { video.controls = true; video.play(); });
      video.addEventListener('play', function () { video.controls = true; box.classList.add('playing'); });
      video.addEventListener('pause', function () { if (video.ended || video.currentTime === 0) box.classList.remove('playing'); });
      video.addEventListener('ended', function () { box.classList.remove('playing'); });
    });
  }


  // ---- 首页滚动故事：进度只由滚动位置决定，往回滚就倒放 ----
  function story() {
    var stage = document.querySelector('.ss-stage');
    if (!stage) return;
    var section = stage.parentNode, data = JSON.parse(stage.getAttribute('data-story'));
    var q = function (s) { return stage.querySelector(s); }, qa = function (s) { return Array.prototype.slice.call(stage.querySelectorAll(s)); };
    var receipt = q('.ss-receipt'), vf = q('.ss-vf'), scan = q('.ss-scan'), flash = q('.ss-flash'), card = q('.ss-card'),
        ledger = q('.ss-ledger'), rowsEl = q('.ss-rows'), saved = q('.ss-saved'), finale = q('.ss-finale'),
        backdrop = q('.ss-backdrop'), filledEl = q('.ss-filled'), caption = q('.ss-caption'), count = q('.ss-count'), pile = q('.ss-pile');
    var marks = qa('.ss-marks span'), fields = qa('.ss-f'), slots = qa('.ss-v'), oks = qa('.ss-slot');
    var rows = data.rows, TOTAL = rows.length;
    var chips = fields.map(function (f, i) {
      var c = document.createElement('div');
      c.className = 'ss-chip' + (i === 0 ? ' th' : '');
      c.textContent = f.textContent;
      stage.appendChild(c);
      return c;
    });
    var MINIS = 14, minis = [];
    for (var n = 0; n < MINIS; n++) {
      var d = document.createElement('div');
      d.className = 'ss-mini';
      d.innerHTML = '<i></i><i></i><i></i><i></i><i></i><i></i><b>✓</b>';
      d._rot = ((n * 47) % 21) - 10; d._dx = ((n * 29) % 13) - 6; d._dy = -n * 3;
      pile.insertBefore(d, count);
      minis.push(d);
    }
    function clamp(v) { return Math.min(1, Math.max(0, v)); }
    function seg(p, a, b) { return clamp((p - a) / (b - a)); }
    function ease(t) { return 1 - Math.pow(1 - t, 3); }
    function io(t) { return t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2; }
    function lerp(a, b, t) { return a + (b - a) * t; }
    var shown = 0;
    function setRows(k) {
      if (k === shown) return;
      if (k < shown) { rowsEl.innerHTML = ''; shown = 0; }
      for (var i = shown; i < k; i++) {
        var tr = document.createElement('tr');
        if (i === 0) tr.className = 'mine';
        rows[i].forEach(function (x) { var td = document.createElement('td'); td.textContent = x; tr.appendChild(td); });
        rowsEl.insertBefore(tr, rowsEl.firstChild);
      }
      shown = k;
    }
    var AT = [0, 0.2, 0.42, 0.64, 0.8];
    function frame() {
      var r = section.getBoundingClientRect();
      var p = reduce ? 1 : clamp(-r.top / (r.height - window.innerHeight));
      var m = window.innerWidth <= 760;
      var st = stage.getBoundingClientRect();

      // 1 拍：单据进来，取景框收拢，快门一闪
      var a = ease(seg(p, 0, 0.16)), vfIn = ease(seg(p, 0.06, 0.17));
      receipt.style.transform = 'translateY(' + lerp(60, 0, a) + 'vh) rotate(' + lerp(-12, -3, a) + 'deg)';
      vf.style.transform = 'scale(' + lerp(1.35, 1, vfIn) + ')';
      vf.style.opacity = vfIn * (1 - seg(p, 0.4, 0.48));
      flash.style.opacity = p > 0.165 && p < 0.2 ? (1 - seg(p, 0.165, 0.2)) * 0.9 : 0;

      // 2 读：扫描线 + 荧光笔
      var s = seg(p, 0.2, 0.38);
      scan.style.opacity = s > 0 && s < 1 ? 1 : 0;
      scan.style.top = lerp(-2, 100, s) + '%';
      fields.forEach(function (f, i) { f.style.setProperty('--hl', ease(seg(p, 0.21 + i * 0.033, 0.25 + i * 0.033))); });

      // 3 核：字段飞进表单，逐项打勾
      var fade = seg(p, 0.4, 0.52), filled = 0;
      receipt.style.opacity = 1 - fade * 0.55;
      receipt.style.filter = 'blur(' + fade * 1.2 + 'px)';
      receipt.style.visibility = p > 0.78 ? 'hidden' : 'visible';
      var k = io(seg(p, 0.64, 0.72));
      chips.forEach(function (c, i) {
        var t = io(seg(p, 0.4 + i * 0.028, 0.5 + i * 0.028));
        var src = fields[i].getBoundingClientRect(), dst = slots[i].getBoundingClientRect();
        var x = lerp(src.left, dst.left, t) - st.left, y = lerp(src.top + src.height / 2, dst.top + dst.height / 2, t) - st.top;
        var lift = Math.sin(t * Math.PI) * (m ? 26 : 60);
        c.style.transform = 'translate(' + x + 'px,' + (y - c.offsetHeight / 2 - lift) + 'px) scale(' + lerp(0.9, 1, t) + ')';
        c.style.opacity = p < 0.4 + i * 0.028 ? 0 : 1 - seg(p, 0.64, 0.665);
        var ok = oks[i].querySelector('.ss-ok');
        if (ok) ok.classList.toggle('on', t >= 1 && p > 0.52 + i * 0.028);
        if (t >= 1) filled++;
      });
      card.style.opacity = (m ? seg(p, 0.3, 0.4) : 0.35 + seg(p, 0.3, 0.4) * 0.65) * (1 - seg(p, 0.64, 0.7));

      // 4 记：表单收成账本里的一行，整个月一行行补进来
      card.style.transform = 'translateY(' + k * (m ? 40 : 90) + 'px) scaleY(' + lerp(1, 0.2, k) + ')';
      ledger.classList.toggle('on', m && p > 0.66);
      ledger.style.opacity = m ? seg(p, 0.66, 0.72) : 0.35 + seg(p, 0.6, 0.7) * 0.65;
      ledger.style.transform = m ? '' : 'translateY(' + (-k * (card.offsetHeight + 22) / 2) + 'px)';
      var rowsNow = p < 0.7 ? 0 : 1 + Math.floor(ease(seg(p, 0.78, 0.92)) * (TOTAL - 1));
      setRows(rowsNow);
      saved.classList.toggle('on', p > 0.7);

      // 整个月的单据一张张落到这一摞上，和账本同步
      var pt = seg(p, 0.76, 0.92);
      minis.forEach(function (d, i) {
        var t = ease(clamp((pt - i / MINIS) * MINIS / 2.2));
        d.style.opacity = t > 0 ? 1 : 0;
        d.style.transform = 'translate(' + d._dx + 'px, calc(' + lerp(-60, 0, t) + 'vh + ' + d._dy + 'px)) rotate(' + lerp(d._rot * 3, d._rot, t) + 'deg)';
      });
      count.style.opacity = p > 0.77 ? 1 : 0;
      count.textContent = data.docs + ' ' + Math.max(1, rowsNow) + ' / ' + TOTAL;

      // 计数器：系统填的格子一直涨，你打的字一直是 0
      filledEl.textContent = p >= 0.7 ? rowsNow * 5 : filled;

      // 收尾
      var f = ease(seg(p, 0.92, 0.98));
      finale.style.opacity = f; backdrop.style.opacity = f;
      finale.style.transform = 'translateY(' + lerp(20, 0, f) + 'px)';
      finale.classList.toggle('on', f > 0.9);
      var step = p < 0.2 ? 0 : p < 0.42 ? 1 : p < 0.64 ? 2 : 3;
      marks.forEach(function (el, i) { el.classList.toggle('on', i <= step); });
      var at = 0;
      AT.forEach(function (v, i) { if (p >= v) at = i; });
      if (caption.textContent !== data.captions[at]) caption.textContent = data.captions[at];
      caption.style.opacity = 1 - f;
    }
    var ticking = false;
    function onScroll() { if (!ticking) { ticking = true; requestAnimationFrame(function () { frame(); ticking = false; }); } }
    window.addEventListener('scroll', onScroll, { passive: true });
    window.addEventListener('resize', onScroll);
    if (document.fonts && document.fonts.ready) document.fonts.ready.then(frame);
    frame();
  }

  // ---- 品牌形象 น้องใบ：眼睛跟着指针，眨眼错开，点首屏那只会跳一下 ----
  function mascots() {
    var looks = Array.prototype.slice.call(document.querySelectorAll('.mascot .m-look'));
    document.querySelectorAll('.mascot .m-blink').forEach(function (g, i) { g.style.animationDelay = (-((i * 1.7) % 5.5)) + 's'; });
    document.querySelectorAll('.hero-mascot').forEach(function (m) {
      m.addEventListener('click', function () { m.classList.remove('hop'); void m.getBoundingClientRect(); m.classList.add('hop'); });
    });
    if (reduce || !looks.length) return;
    var px = 0, py = 0, queued = false;
    function update() {
      queued = false;
      looks.forEach(function (g) {
        var r = g.ownerSVGElement.getBoundingClientRect();
        if (r.bottom < 0 || r.top > window.innerHeight) return;
        var dx = px - (r.left + r.width / 2), dy = py - (r.top + r.height * 0.3), d = Math.sqrt(dx * dx + dy * dy) || 1, k = Math.min(1, d / 240) * 5;
        g.setAttribute('transform', 'translate(' + (dx / d * k).toFixed(2) + ' ' + (dy / d * k).toFixed(2) + ')');
      });
    }
    window.addEventListener('pointermove', function (e) { px = e.clientX; py = e.clientY; if (!queued) { queued = true; requestAnimationFrame(update); } }, { passive: true });
  }

  function init() { dots(); reveal(); topbar(); demo(); players(); story(); mascots(); }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
