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

  // ---- 首页演示：三段"现实 → 系统"小故事。编排都在标记里（见 build.py 的说明），这里只是通用时间轴 ----
  function demo() {
    var root = document.querySelector('.demo');
    if (!root) return;
    var tabs = root.querySelectorAll('[data-tab]');
    var bar = root.querySelector('.progress i');
    var order = [], timers = [], current = 0, visible = false, started = false;
    var SPEED = 0.6;  // 标记里按 10 秒左右编排，整体压到 6 秒左右播放
    for (var i = 0; i < tabs.length; i++) order.push(tabs[i].getAttribute('data-tab'));
    var hashed = /^#demo-(\w+)/.exec(location.hash);
    if (hashed && order.indexOf(hashed[1]) >= 0) current = order.indexOf(hashed[1]);

    function clear() { timers.forEach(clearTimeout); timers = []; }
    function later(fn, ms) { timers.push(setTimeout(fn, ms)); }
    function steps(el) {
      return el.getAttribute('data-at').split(',').map(function (part) {
        var bits = part.trim().split(':');
        var cls = bits[1] || 'on', off = cls.charAt(0) === '-';
        return { at: +bits[0], cls: off ? cls.slice(1) : cls, off: off };
      });
    }
    function type(el, done) {
      var text = el.getAttribute('data-type');
      if (done || reduce) { el.textContent = text; return; }
      var n = 0;
      (function tick() { el.textContent = text.slice(0, ++n); if (n < text.length) later(tick, 28); })();
    }
    function count(el, cls, done) {
      el.getAttribute('data-count').split('|').forEach(function (rule) {
        var r = rule.split(':');
        if (r[0] !== cls) return;
        var from = +r[1], to = +r[2], t0 = performance.now();
        if (done || reduce) { el.textContent = to; return; }
        (function tick(now) {
          var k = Math.min(1, (now - t0) / 650);
          el.textContent = Math.round(from + (to - from) * (1 - Math.pow(1 - k, 3)));
          if (k < 1) requestAnimationFrame(tick);
        })(t0);
      });
    }
    function center(el, base) {
      var r = el.getBoundingClientRect();
      return { x: r.left + r.width / 2 - base.left, y: r.top + r.height / 2 - base.top };
    }
    function fly(el, scene) {
      var src = scene.querySelector(el.getAttribute('data-from')), dst = scene.querySelector(el.getAttribute('data-to'));
      if (!src || !dst || !el.animate) return;
      var base = scene.getBoundingClientRect(), a = center(src, base), b = center(dst, base);
      var w = el.offsetWidth / 2, h = el.offsetHeight / 2, lift = Math.min(90, Math.abs(b.x - a.x) * 0.25 + 30);
      var at = function (p, s, o) {
        var x = a.x + (b.x - a.x) * p - w, y = a.y + (b.y - a.y) * p - h - Math.sin(p * Math.PI) * lift;
        return { transform: 'translate(' + x + 'px,' + y + 'px) scale(' + s + ')', opacity: o };
      };
      el.animate([at(0, 0.6, 0), at(0.15, 1, 1), at(0.5, 1.08, 1), at(0.9, 1, 1), at(1, 0.7, 0)],
                 { duration: +(el.getAttribute('data-dur') || 750) * SPEED, easing: 'cubic-bezier(.45,0,.25,1)', fill: 'both' });
    }
    // 配对的线：从一个元素的右边缘连到另一个元素的左边缘，加 on 时由 CSS 画出来
    function thread(el, scene) {
      var src = scene.querySelector(el.getAttribute('data-from')), dst = scene.querySelector(el.getAttribute('data-to'));
      if (!src || !dst) return;
      var base = scene.getBoundingClientRect(), r1 = src.getBoundingClientRect(), r2 = dst.getBoundingClientRect();
      var x1 = r1.right - base.left - 4, y1 = r1.top + r1.height / 2 - base.top;
      if (r2.top > r1.bottom + 20) {  // 手机上两栏叠起来：从这一行的右端往下弯到卡片顶部
        var x2v = r2.left + r2.width / 2 - base.left, y2v = r2.top - base.top + 2;
        el.setAttribute('d', 'M' + x1 + ' ' + y1 + ' C' + (x1 + 28) + ' ' + y1 + ' ' + x2v + ' ' + (y2v - 70) + ' ' + x2v + ' ' + y2v);
        return;
      }
      var x2 = r2.left - base.left + 4, y2 = r2.top + r2.height / 2 - base.top, dx = Math.max(40, (x2 - x1) / 2);
      el.setAttribute('d', 'M' + x1 + ' ' + y1 + ' C' + (x1 + dx) + ' ' + y1 + ' ' + (x2 - dx) + ' ' + y2 + ' ' + x2 + ' ' + y2);
    }
    function apply(el, step, scene, done) {
      if (el.classList.contains('fly')) { if (!done) fly(el, scene); return; }
      if (el.classList.contains('thread') && !step.off) thread(el, scene);
      el.classList.toggle(step.cls, !step.off);
      if (step.off) return;
      if (step.cls === 'on' && el.hasAttribute('data-type')) type(el, done);
      if (el.hasAttribute('data-count')) count(el, step.cls, done);
      el.querySelectorAll(':scope > [data-count]').forEach(function (c) { count(c, step.cls, done); });
    }
    function reset(scene) {
      scene.querySelectorAll('[data-at]').forEach(function (el) {
        steps(el).forEach(function (st) { el.classList.remove(st.cls); });
        if (el.hasAttribute('data-type')) el.textContent = '';
        if (el.getAnimations) el.getAnimations().forEach(function (an) { an.cancel(); });
      });
      scene.querySelectorAll('[data-count]').forEach(function (el) { el.textContent = el.getAttribute('data-count').split('|')[0].split(':')[1]; });
    }

    function play(index, auto) {
      clear();
      current = index;
      var name = order[index], scene;
      for (var i = 0; i < tabs.length; i++) tabs[i].setAttribute('aria-selected', String(i === index));
      root.querySelectorAll('.scene').forEach(function (s) {
        var active = s.getAttribute('data-scene') === name;
        s.classList.toggle('active', active);
        if (active) scene = s;
      });
      reset(scene);
      var all = [];
      scene.querySelectorAll('[data-at]').forEach(function (el) { steps(el).forEach(function (st) { all.push([el, st]); }); });
      all.sort(function (x, y) { return x[1].at - y[1].at; });
      if (reduce) {
        all.forEach(function (pair) { apply(pair[0], pair[1], scene, true); });
        return;
      }
      all.forEach(function (pair) { later(function () { apply(pair[0], pair[1], scene, false); }, pair[1].at * SPEED); });
      var total = +scene.getAttribute('data-total') * SPEED;
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
    if (!('IntersectionObserver' in window)) { visible = true; play(current); return; }
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

  // ---- 首页主视觉：小单把书桌照片的一角卷起来，露出底下整理好的账（几何与 website/poster.py geom() 同一套） ----
  function peel() {
    var svg = document.querySelector('.pz');
    if (!svg) return;
    var card = svg.closest('.peel'), p0 = +svg.getAttribute('data-p0');
    function q(sel) { return svg.querySelector(sel); }
    function all(sel) { return svg.querySelectorAll(sel); }
    var clip = q('.pz-clip'), body = q('.pz-body'), legs = all('.pz-leg'), armPen = q('.pz-arm-pen'), handPen = q('.pz-hand-pen'),
      pen = q('.pz-pen'), armPush = q('.pz-arm-push'), handPush = q('.pz-hand-push'), ground = q('.pz-ground'), caption = q('.pz-caption'),
      flaps = all('.pz-flap'), gap = q('.pz-gapshade'), g1 = q('.pz-g'), g2 = q('.pz-g2');
    var SCALE = 1.45, GROUND = 772, STRIDE = 30, HP = 560;
    function f(v) { return Math.round(v * 10) / 10; }
    function clamp(v) { return Math.max(0, Math.min(1, v)); }
    function set(el, k, v) { el.setAttribute(k, v); }
    function limb(a, b, bend) {
      var dx = b[1] - a[1], dy = a[0] - b[0], d = Math.sqrt(dx * dx + dy * dy) || 1;
      return 'M' + f(a[0]) + ' ' + f(a[1]) + 'Q' + f((a[0] + b[0]) / 2 + dx / d * bend) + ' ' + f((a[1] + b[1]) / 2 + dy / d * bend) + ' ' + f(b[0]) + ' ' + f(b[1]);
    }
    // 进度 p → 左下角斜卷的纸卷 + 小单每个部件（与 poster.py state() / roll_attrs() 同一套公式）
    function draw(p) {
      var a = 120 + 170 * p, b = a * 1.75, ln = Math.sqrt(a * a + b * b), ux = b / ln, uy = a / ln, nx = uy, ny = -ux;
      var t0 = a * uy, fx = ux * t0, fy = HP - a + uy * t0, dist = a * b / ln, tx = fx + nx * dist * .6, ty = fy + ny * dist * .6;
      var m1x = tx / 2 - nx * dist * .1, m1y = (HP - a + ty) / 2 - ny * dist * .1;
      var m2x = (b + tx) / 2 - nx * dist * .14, m2y = (HP + ty) / 2 - ny * dist * .14;
      var fin = clamp((p - .9) / .1), ph = b / STRIDE, walk = 1 - fin, bob = -4 * Math.abs(Math.cos(ph)) * walk, tilt = 16 - 20 * fin;
      var hip = [b - 90 - 20 * fin, GROUND - 40 * SCALE + 2 + bob], t = tilt * Math.PI / 180, c = Math.cos(t), sn = Math.sin(t);
      function rot(px, py) { px *= SCALE; py *= SCALE; return [hip[0] + px * c - py * sn, hip[1] + px * sn + py * c]; }
      // 写实翻页角（与 poster.py curl() 同一套公式）：翻起的纸背、它的投影、折痕下的暗影都跟着折线走
      set(clip, 'd', 'M0 0H1440V' + HP + 'H' + f(b) + 'L0 ' + f(HP - a) + 'Z');
      var flapD = 'M0 ' + f(HP - a) + 'Q' + f(m1x) + ' ' + f(m1y) + ' ' + f(tx) + ' ' + f(ty) + 'Q' + f(m2x) + ' ' + f(m2y) + ' ' + f(b) + ' ' + HP + 'Z';
      for (var i = 0; i < flaps.length; i++) set(flaps[i], 'd', flapD);
      set(gap, 'd', 'M0 ' + f(HP - a) + 'L' + f(b) + ' ' + HP + 'H0Z');
      set(g1, 'x1', f(fx)); set(g1, 'y1', f(fy)); set(g1, 'x2', f(tx)); set(g1, 'y2', f(ty));
      set(g2, 'x1', f(fx)); set(g2, 'y1', f(fy));
      var swing = 20 * Math.sin(ph) * walk;
      [[-14, swing, -1], [14, -swing, 1]].forEach(function (l, i) {
        var h = rot(l[0], 0), fx = h[0] + l[1] + 5 * fin * l[2];
        set(legs[i], 'd', 'M' + f(h[0]) + ' ' + f(h[1]) + 'L' + f((h[0] + fx) / 2 + 8 * walk) + ' ' + f((h[1] + GROUND) / 2 - 2) + 'L' + f(fx) + ' ' + GROUND + 'h' + 16 * l[2]);
      });
      var push = [b + (tx - b) * .1, HP + (ty - HP) * .1 + bob], wave = rot(70, -126), hand = [push[0] + (wave[0] - push[0]) * fin, push[1] + (wave[1] - push[1]) * fin];
      var penAt = rot(-58, -24);
      set(body, 'transform', 'translate(' + f(hip[0]) + ' ' + f(hip[1]) + ') rotate(' + f(tilt) + ') scale(' + SCALE + ')');
      set(armPen, 'd', limb(rot(-36, -46), penAt, -12));
      set(handPen, 'cx', f(penAt[0])); set(handPen, 'cy', f(penAt[1]));
      set(pen, 'transform', 'translate(' + f(penAt[0]) + ' ' + f(penAt[1]) + ') rotate(-24) translate(0 -64)');
      set(armPush, 'd', limb(rot(36, -50), hand, 14));
      set(handPush, 'cx', f(hand[0])); set(handPush, 'cy', f(hand[1]));
      set(ground, 'd', 'M' + f(hip[0] - 100) + ' ' + GROUND + ' m0 4 h200 M' + f(hip[0] - 70) + ' ' + GROUND + ' m0 11 h140');
      set(caption, 'x', f(hip[0]));
    }
    // 手机上只取左边一段
    function fit() {
      var view = svg.getAttribute(card.clientWidth < 640 ? 'data-view-sm' : 'data-view');
      svg.setAttribute('viewBox', view);
      q('.pz-frame').setAttribute('width', view.split(' ')[2]);
    }
    fit();
    window.addEventListener('resize', fit);
    if (reduce) { draw(p0); return; }
    // 卡片从屏幕下方往上走约 3/4 屏就卷到头；平滑跟随，只在看得见、进度有变化时重画
    var cur = 0, last = 0, queued = false, seen = true;
    function goal() {
      var r = svg.getBoundingClientRect(), vh = window.innerHeight;
      return clamp((vh * .95 - r.top) / (vh * .75));
    }
    function frame(now) {
      queued = false;
      var g = goal();
      cur += (g - cur) * Math.min(1, (now - last) / 120 || .15);
      last = now;
      draw(cur);
      if (Math.abs(g - cur) > .0008) queue();
    }
    function queue() { if (!queued) { queued = true; requestAnimationFrame(frame); } }
    if ('IntersectionObserver' in window) new IntersectionObserver(function (e) { seen = e[0].isIntersecting; if (seen) queue(); }).observe(svg);
    window.addEventListener('scroll', function () { if (seen) queue(); }, { passive: true });
    last = performance.now();
    draw(0);
    queue();
  }

  // ---- 预约演示按钮：手机上直接打开预填好消息的 LINE 对话；电脑上留在加好友页（扫码） ----
  function lineMessages() {
    if (!/Android|iPhone|iPad|iPod/i.test(navigator.userAgent)) return;
    document.querySelectorAll('[data-line-message]').forEach(function (a) { a.href = a.getAttribute('data-line-message'); });
  }

  function init() { dots(); reveal(); topbar(); demo(); players(); story(); mascots(); peel(); lineMessages(); }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
