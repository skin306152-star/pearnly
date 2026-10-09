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
      if (r2.top > r1.bottom + 20) {  // 手机上两栏叠起来：从这一行的右端往下弯到卡片顶部（data-to-top 指定叠起来时连到哪个元素）
        if (el.getAttribute('data-to-top')) r2 = scene.querySelector(el.getAttribute('data-to-top')).getBoundingClientRect();
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


  // ---- 品牌形象 น้องใบ：眼睛跟着指针，眨眼错开，点首屏那只会跳一下 ----
  function mascots() {
    var looks = Array.prototype.slice.call(document.querySelectorAll('.mascot .m-look, .hm .m-look'));
    document.querySelectorAll('.mascot .m-blink').forEach(function (g, i) { g.style.animationDelay = (-((i * 1.7) % 5.5)) + 's'; });
    document.querySelectorAll('.hero-mascot').forEach(function (m) {
      m.addEventListener('click', function () { m.classList.remove('hop'); void m.getBoundingClientRect(); m.classList.add('hop'); });
    });
    if (reduce || !looks.length) return;
    var px = 0, py = 0, queued = false;
    function update() {
      queued = false;
      looks.forEach(function (g) {
        // 整张 SVG 里只有一只小单的（品牌形象）按整张算；手绘场景里的小单按它自己的身体算
        var r = (g.closest('.hm') || g.ownerSVGElement).getBoundingClientRect();
        if (r.bottom < 0 || r.top > window.innerHeight) return;
        var dx = px - (r.left + r.width / 2), dy = py - (r.top + r.height * 0.3), d = Math.sqrt(dx * dx + dy * dy) || 1, k = Math.min(1, d / 240) * 5;
        g.setAttribute('transform', 'translate(' + (dx / d * k).toFixed(2) + ' ' + (dy / d * k).toFixed(2) + ')');
      });
    }
    window.addEventListener('pointermove', function (e) { px = e.clientX; py = e.clientY; if (!queued) { queued = true; requestAnimationFrame(update); } }, { passive: true });
  }

  // ---- 首页主视觉“过去 / 现在”（website/story.py）：每一帧都由时间 t 算出来，同一个 t 永远画出同一帧；循环播放 ----
  function story() {
    var svg = document.querySelector('.st');
    if (!svg) return;
    var card = svg.closest('.st-card'), capEl = card.querySelector('.st-cap');
    var CAPS = JSON.parse(card.getAttribute('data-caps'));
    var K = 2, SX = .82, HOME = [470, 452], SLAB = 6, PILE_BASE = 358, DONE_BASE = 388, ITEMS = 16, END = 14000;
    var T2 = 4800, T3 = 6250, T4 = 11500;             // ② 转折、③ 现在（记号笔盖满画面的那一刻）、④ 完成
    var HOLD = 1200, WIPE = 700;                       // 播完停一下，记号笔刷过去接回开头
    var C1 = [300, 1800, 3300];                        // 过去：每张 1.5 秒
    var SNAP = [7750, 8350, 8880, 9330, 9700];         // 现在：前五张一张张拍，剩下的一口气
    for (var s0 = 0; SNAP.length < ITEMS; s0++) SNAP.push(10000 + s0 * 75);
    var ARRIVE = SNAP.map(function (s) { return s + 680; });
    var HN0 = [606, 388], HF0 = [668, 380], READ = [580, 292], KEYS = [[652, 380], [676, 376], [700, 380], [664, 378]];
    var PHONE_DESK = [687, 351], PHONE_HOLD = [588, 254], TABLE = svg.getAttribute('data-table').split(' ').map(Number);
    var OLD = ['บจ. ขนส่งด่วนไทย\t321.00', 'ร้านเครื่องเขียนสยาม\t193.10····⌫⌫⌫⌫⌫39.10', 'บจ. แพ็คกิ้งพลัส\t2,247.00'];

    function q(s) { return svg.querySelector(s); }
    function qa(s) { return Array.prototype.slice.call(svg.querySelectorAll(s)); }
    var E = {
      body: q('.st-body'), face: q('.st-face'), look: q('.st-look'), bags: q('.st-bags'), sweat: q('.st-sweat'), excl: q('.st-excl'),
      armN: q('.st-arm-n'), armF: q('.st-arm-f'), handN: q('.st-hand-n'), handF: q('.st-hand-f'), legN: q('.st-leg-near'), legF: q('.st-leg-far'),
      held: q('.st-held'), slabs: qa('.st-slab'), ptop: q('.st-slab-top'), dsheets: qa('.st-dsheet'), dtop: q('.st-dsheet-top'),
      pflat: q('.st-pflat'), pglow: q('.st-pglow'), pscreen: q('.st-pscreen'), buzz: q('.st-buzz'), bubble: q('.st-bubble'),
      pup: q('.st-pup'), shutter: q('.st-shutter'), ok: q('.st-ok'), vdoc: q('.st-vdoc'), burst: q('.st-burst'), stream: q('.st-stream'),
      steam: q('.st-mug .st-steam'), mug2: q('.st-mug2'), mug3: q('.st-mug3'),
      fly: qa('.st-fly'), chips: qa('.st-chip'), rows: qa('.st-row'), count: q('.st-count'), stamp: q('.st-stamp'),
      old: q('.st-scr-old'), oldS: qa('.st-old-s'), oldA: qa('.st-old-a'), cell: q('.st-cell'), typo: q('.st-typo'),
      app: q('.st-scr-app'), off: q('.st-scr-off'), boot: q('.st-scr-boot'),
      dusk: q('.st-sky-dusk'), nightSky: q('.st-sky-night'), stars: qa('.st-star'), starG: q('.st-stars'),
      sun: q('.st-sun'), moon: q('.st-moon'), cloud1: q('.st-cloud1'), cloud2: q('.st-cloud2'), bird: q('.st-bird'),
      hour: q('.st-hour'), min: q('.st-min'), night: q('.st-night'), glow: q('.st-glow'), light: q('.st-light'), bulb: q('.st-bulb'),
      flash: q('.st-flash'), swipe: q('.st-swipe'), puff: q('.st-puff'), sparks: qa('.st-spark'), frame: q('.st-frame')
    };
    var EYES = {}, MOUTHS = {};
    qa('.st-e').forEach(function (el) { EYES[el.getAttribute('class').split('st-e-')[1]] = el; });
    qa('.st-m').forEach(function (el) { MOUTHS[el.getAttribute('class').split('st-m-')[1]] = el; });
    E.rows = E.rows.map(function (g) { return { g: g, hl: g.querySelector('.st-rowhl'), tick: g.querySelector('.st-rowtick') }; });

    // ---- 小工具 ----
    function c01(v) { return v < 0 ? 0 : v > 1 ? 1 : v; }
    function io(k) { return k < .5 ? 4 * k * k * k : 1 - Math.pow(-2 * k + 2, 3) / 2; }
    function out(k) { return 1 - Math.pow(1 - k, 3); }
    function back(k) { var a = 1.70158; return 1 + (a + 1) * Math.pow(k - 1, 3) + a * Math.pow(k - 1, 2); }
    function lin(k) { return k; }
    function seg(t, a, b, e) { return (e || io)(c01((t - a) / (b - a))); }
    function mix(a, b, k) { return [a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k]; }
    function f(v) { return Math.round(v * 10) / 10; }
    function f2(v) { return Math.round(v * 1000) / 1000; }
    function count(list, t, d) { var c = 0; for (var i = 0; i < list.length; i++) if (t >= list[i] + (d || 0)) c++; return c; }
    function set(el, k, v) { var key = '_' + k; if (el[key] !== v) { el[key] = v; el.setAttribute(k, v); } }
    function op(el, v) { set(el, 'opacity', String(Math.round(c01(v) * 100) / 100)); }
    function move(el, x, y, extra) { set(el, 'transform', 'translate(' + f(x) + ' ' + f(y) + ')' + (extra || '')); }
    function text(el, v) { if (el._text !== v) { el._text = v; el.textContent = v; } }
    function arc(a, b, k, lift) { var p = mix(a, b, k); p[1] -= Math.sin(k * Math.PI) * lift; return p; }
    function quad(a, c, b, k) { return [(1 - k) * (1 - k) * a[0] + 2 * k * (1 - k) * c[0] + k * k * b[0], (1 - k) * (1 - k) * a[1] + 2 * k * (1 - k) * c[1] + k * k * b[1]]; }
    function bump(t, list, dur) { var v = 0; list.forEach(function (s) { var d = t - s; if (d >= 0 && d < dur) v = Math.max(v, Math.sin(d / dur * Math.PI)); }); return v; }

    // ---- 单据：过去每看完一张，堆里少一张、手边那一小叠多一张；现在一张张被拍走 ----
    function pileN(t) { return t < T3 ? 16 - count(C1, t, 350) : ITEMS - count(SNAP, t, 80); }
    function doneN(t) { return t < T3 ? count(C1, t, 1500) : 0; }

    // ---- 旧软件里逐字敲出来的那一行（⌫ 删一个字，· 停一下）；返回 [供应商, 金额, 是否正在改错, 光标在第几格] ----
    function typed(c, t) {
      var u = t - C1[c] - 650, toks = Array.from(OLD[c]);
      if (u < 0) return ['', '', false, -1];
      var total = 0; toks.forEach(function (x) { total += x === '⌫' ? .7 : 1; });
      var budget = c01(u / 600) * total, buf = [], err = false, firstPause = OLD[c].indexOf('·');
      for (var i = 0, acc = 0; i < toks.length; i++) {
        acc += toks[i] === '⌫' ? .7 : 1;
        if (acc > budget + 1e-6) break;
        if (toks[i] === '⌫') buf.pop(); else if (toks[i] !== '·') buf.push(toks[i]);
        err = firstPause >= 0 && i >= firstPause - 1 && toks.slice(i + 1).indexOf('⌫') >= 0;
      }
      var str = buf.join(''), tab = str.indexOf('\t');
      return tab < 0 ? [str, '', false, u < 600 ? 0 : -1] : [str.slice(0, tab), str.slice(tab + 1), err, u < 600 ? 1 : -1];
    }

    // ---- 小单每一帧的姿势：侧身坐在椅子上（turn=1 时转过来正对你），近手（左手）在身前，远手（右手）在身后 ----
    function pose(t) {
      var m = { x: HOME[0], y: HOME[1], r: 0, sy: 1, turn: 0, hn: HN0.slice(), hf: HF0.slice(), eyes: 'open', mouth: 'smile', look: [3, 0],
                bags: 0, sweat: 0, excl: 0, held: null, phone: 'flat', kick: 0 };
      if (t < T2) act1(t, m); else if (t < T3) act2(t, m); else if (t < T4) act3(t, m); else act4(t, m);
      return m;
    }
    // ① 过去：左手从手边的单据堆拿一张、凑近看，右手一根手指敲；看完放到手边那一小叠。越到晚上越累：往下塌、冒汗、黑眼圈
    function act1(t, m) {
      var tired = seg(t, 2400, 4400);
      m.y += 6 * tired; m.r = 3 * tired; m.bags = tired; m.mouth = t < 300 ? 'smile' : t < 2600 ? 'flat' : 'wavy'; m.sweat = t > 2000 ? 1 : 0;
      for (var c = 0; c < 3; c++) {
        var u = t - C1[c], k;
        if (u < 0 || u > 1500) continue;
        var grab = [598, PILE_BASE - pileN(C1[c] + 349) * SLAB - 6], drop = [584, DONE_BASE - doneN(C1[c] + 1499) * SLAB - 8];
        if (u < 350) {
          k = seg(u, 0, 350); m.hn = mix(HN0, grab, k); m.r += 4 * k; m.look = [3, -2 * k];
        } else if (u < 650) {
          k = seg(u, 350, 650); m.hn = mix(grab, READ, k); m.r += 4 - 2 * k; m.look = [4, -2];
          m.held = { at: m.hn, rot: -6, sy: 1 }; if (u > 450) m.eyes = 'squint';
        } else if (u < 1250) {
          k = seg(u, 650, 800); m.r += 2 + 3 * k;
          m.hn = [READ[0] + Math.sin(u / 90) * 1.5, READ[1] + Math.sin(u / 130) * 2]; m.held = { at: m.hn, rot: -6, sy: 1 };
          var p = Math.floor((u - 650) / 110), fr = ((u - 650) % 110) / 110, key = KEYS[(p + c) % KEYS.length];
          m.hf = [key[0], key[1] - Math.sin(fr * Math.PI) * 11];
          m.look = [4, 0]; m.eyes = u < 820 ? 'squint' : 'open';
          if (typed(c, t)[2]) { m.eyes = 'wide'; m.mouth = 'o'; m.look = [5, -1]; }      // 敲错了一位
        } else {
          k = seg(u, 1250, 1500); m.hn = mix(READ, drop, k); m.hf = mix(KEYS[(5 + c) % KEYS.length], HF0, k);
          m.held = { at: m.hn, rot: -6 * (1 - k), sy: 1 - .8 * k, o: u > 1480 ? 0 : 1 }; m.r += 5 * (1 - k); m.look = [3, 2 * k];
        }
      }
    }
    // ② 转折：趴到桌上叹气；手机震、弹出 Pearnly 的消息，一下坐直
    function act2(t, m) {
      m.bags = 1; m.mouth = 'wavy';
      var down = seg(t, 4800, 5250) * (1 - seg(t, 5650, 5900, back));
      m.y = HOME[1] + 6 + 8 * down; m.r = 3 + 17 * down; m.sy = 1 - .05 * down; m.eyes = down > .5 ? 'closed' : 'open';
      m.hn = mix(HN0, [622, 390], seg(t, 4800, 5200)); m.hf = mix(HF0, [700, 384], seg(t, 4800, 5200));
      if (t >= 5650) { m.eyes = 'wide'; m.mouth = 'o'; m.look = [6, 3]; m.excl = seg(t, 5650, 5820, back); }
    }
    // ③ 现在：敲一下键盘开屏，拿起手机对着手边的单据堆一张张拍，最后一口气全进去
    function act3(t, m) {
      var tap = seg(t, 6450, 6700, lin);
      if (tap > 0 && tap < 1) m.hf = [676, 380 - Math.sin(tap * Math.PI) * 13];
      var reach = seg(t, 7050, 7350), lift = seg(t, 7350, 7700);
      m.hn = mix(mix(HN0, PHONE_DESK, reach), PHONE_HOLD, lift);
      m.y -= 8 * lift; m.r = 3 * reach + 3 * lift; m.phone = t < 7350 ? 'flat' : 'up';
      var kick = bump(t, SNAP.slice(0, 5), 180) + .5 * bump(t, SNAP.slice(5), 70);
      m.hn[1] -= 5 * kick; m.y -= 2 * kick;
      if (t > 7700 && t < 9950) m.look = [4, 2];
      if (t >= 9950) { m.look = [5, -2]; m.mouth = t < 10700 ? 'o' : 'big'; m.eyes = t < 10700 ? 'wide' : 'happy'; }
      var pump = seg(t, 10900, 11150) * (1 - seg(t, 11300, 11500));
      if (pump > 0) m.hf = mix(m.hf, [540, 246 - 12 * Math.sin(seg(t, 11050, 11300, lin) * Math.PI * 2)], pump);
    }
    // ④ 完成：放下手机，往椅背一靠呼一口气，转过来朝你挥手，晃晃腿
    function act4(t, m) {
      m.eyes = 'happy'; m.mouth = 'big';
      var sit = seg(t, 11500, 11950);
      m.y -= 8 * (1 - sit); m.r = 6 * (1 - sit);
      m.hn = t < 11950 ? mix(PHONE_HOLD, PHONE_DESK, sit) : mix(PHONE_DESK, HN0, seg(t, 11950, 12150));
      m.phone = t < 11950 ? 'up' : 'flat';
      var lean = seg(t, 12150, 12550) * (1 - seg(t, 12950, 13250));
      m.r -= 10 * lean; m.x -= 6 * lean; m.hf = mix(HF0, [606, 394], lean);
      if (t >= 12450 && t < 12950) m.mouth = 'smile';
      m.turn = seg(t, 13050, 13450);
      if (t >= 13250) {
        var w = seg(t, 13250, 13550), wave = Math.sin(seg(t, 13550, END, lin) * Math.PI * 4) * 16;
        m.hn = mix(m.hn, [372 + wave, 262], w); m.look = [0, 0]; m.eyes = t < 13450 ? 'happy' : 'open';
        m.kick = Math.sin(seg(t, 13450, END, lin) * Math.PI * 6) * w;
      }
    }

    function limb(a, b, bend) {
      var dx = b[1] - a[1], dy = a[0] - b[0], d = Math.sqrt(dx * dx + dy * dy) || 1;
      return 'M' + f(a[0]) + ' ' + f(a[1]) + 'Q' + f((a[0] + b[0]) / 2 + dx / d * bend) + ' ' + f((a[1] + b[1]) / 2 + dy / d * bend) + ' ' + f(b[0]) + ' ' + f(b[1]);
    }
    function bp(m, px, py) {
      var a = m.r * Math.PI / 180, c = Math.cos(a), s = Math.sin(a), sx = SX + (1 - SX) * m.turn;
      px *= K * sx; py *= K * m.sy;
      return [m.x + px * c - py * s, m.y + px * s + py * c];
    }
    function drawMascot(m, t) {
      var sx = SX + (1 - SX) * m.turn;
      set(E.body, 'transform', 'translate(' + f(m.x) + ' ' + f(m.y) + ') rotate(' + f(m.r) + ') scale(' + f2(K * sx) + ' ' + f2(K * m.sy) + ')');
      set(E.face, 'transform', 'translate(' + f(9 * (1 - m.turn)) + ' 0)');
      for (var e in EYES) op(EYES[e], e === m.eyes ? 1 : 0);
      for (var mo in MOUTHS) op(MOUTHS[mo], mo === m.mouth ? 1 : 0);
      set(EYES.open, 'transform', m.eyes === 'open' && (t % 2900) < 120 ? 'translate(0 -72) scale(1 .12) translate(0 72)' : '');
      set(E.look, 'transform', 'translate(' + f(m.look[0]) + ' ' + f(m.look[1]) + ')');
      op(E.bags, m.bags * .9);
      var sp = (t % 900) / 900;
      op(E.sweat, m.sweat * (sp < .15 ? sp / .15 : 1 - (sp - .15) / .85)); move(E.sweat, 0, 14 * sp);
      op(E.excl, m.excl); set(E.excl, 'transform', 'translate(2 -128) scale(' + f2(Math.max(.01, m.excl)) + ') translate(-2 128)');
      var sn = bp(m, 26 - 64 * m.turn, -48), sf = bp(m, 34 + 4 * m.turn, -54);   // 侧身时两肩都在身体前侧，转过来时近肩回到左边
      set(E.armN, 'd', limb(sn, m.hn, 14)); set(E.armF, 'd', limb(sf, m.hf, 12));
      set(E.handN, 'cx', f(m.hn[0])); set(E.handN, 'cy', f(m.hn[1])); set(E.handF, 'cx', f(m.hf[0])); set(E.handF, 'cy', f(m.hf[1]));
      // 腿：髋跟着身子走，脚踩在地上（挥手时晃一晃）
      var hip = bp(m, 0, -2), kk = m.kick * 8;
      set(E.legN, 'd', 'M' + f(hip[0]) + ' ' + f(hip[1]) + 'L552 450L' + f(560 + kk) + ' ' + f(522 - Math.abs(kk) * .6) + 'h16');
      set(E.legF, 'd', 'M' + f(hip[0] + 14) + ' ' + f(hip[1] - 4) + 'L566 442L' + f(578 - kk) + ' ' + f(518 - Math.abs(kk) * .6) + 'h16');
      if (m.held) { op(E.held, m.held.o === undefined ? 1 : m.held.o); move(E.held, m.held.at[0] + 18, m.held.at[1] - 22, ' rotate(' + f(m.held.rot) + ') scale(1 ' + f2(m.held.sy) + ')'); }
      else op(E.held, 0);
    }

    // ---- 每一帧（looped：上一轮刚刷过来，开头先让记号笔刷走） ----
    function render(t, looped) {
      var m = pose(Math.min(t, END)), tt = Math.min(t, END);
      var night = tt < T3 ? seg(tt, 2700, 3900) : 0, dusk = tt < T3 ? seg(tt, 1500, 2700) : 0;
      drawMascot(m, t);

      // 窗外：白天 → 黄昏 → 夜里（第二天早上又是白天）；太阳落下、月亮升起、星星眨眼，第二天飞过一只鸟
      op(E.dusk, dusk); op(E.nightSky, night); op(E.starG, night);
      E.stars.forEach(function (s, i) { op(s, .55 + .45 * Math.sin(t / 260 + i * 1.7)); });
      var sun = tt < T3 ? mix([1262, 96], [1176, 262], seg(tt, 300, 3300)) : mix([1240, 106], [1264, 90], seg(tt, T3, END, lin));
      move(E.sun, sun[0], sun[1]);
      op(E.moon, tt < T3 ? seg(tt, 3300, 4100) : 0); var moon = mix([1132, 196], [1156, 104], seg(tt, 3300, 5800)); move(E.moon, moon[0], moon[1]);
      move(E.cloud1, 1090 + (t * .012) % 280, 120); move(E.cloud2, 1070 + (t * .008 + 150) % 280, 196, ' scale(.7)');
      var bird = seg(tt, 8200, 9600, lin);
      op(E.bird, bird > 0 && bird < 1 ? 1 : 0);
      move(E.bird, 1086 + 250 * bird, 130 - 28 * bird + Math.sin(bird * 12) * 4, ' scale(1 ' + (Math.floor(t / 110) % 2 ? '-.6' : '1') + ')');

      // 钟：过去一下午转过去十几个小时，现在只走了十几分钟
      var h = tt < T2 ? 9 + 11.5 * seg(tt, 0, T2) : tt < T3 ? 20.5 + .5 * seg(tt, T2, T3, lin) : tt < T4 ? 9 + .2 * seg(tt, T3, T4, lin) : 9.2 + .05 * seg(tt, T4, END, lin);
      set(E.hour, 'transform', 'rotate(' + f((h % 12) * 30) + ' 440 96)'); set(E.min, 'transform', 'rotate(' + f((h % 1) * 360) + ' 440 96)');

      // 夜里屋里暗下来，台灯“啪”地亮起，屏幕的光照在脸上
      op(E.night, .36 * night);
      var lampOn = tt >= 3700 && tt < T3 && !(tt > 3740 && tt < 3800);
      op(E.light, lampOn ? .85 : 0); set(E.bulb, 'fill', lampOn ? '#fff1a0' : '#fff7d1'); op(E.glow, .5 * night);

      // 单据堆与手边已录的那一小叠
      var N = pileN(tt), D = doneN(tt);
      E.slabs.forEach(function (s, i) { op(s, i < N ? 1 : 0); });
      op(E.ptop, N > 0 ? 1 : 0); move(E.ptop, 0, PILE_BASE - N * SLAB);
      E.dsheets.forEach(function (s, i) { op(s, i < D ? 1 : 0); });
      op(E.dtop, D > 0 ? 1 : 0); move(E.dtop, 0, DONE_BASE - D * SLAB);

      // 咖啡：过去凉了、空杯越摆越多；现在是一杯热的
      op(E.mug2, tt < T3 ? seg(tt, 2300, 2500, back) : 0); op(E.mug3, tt < T3 ? seg(tt, 3900, 4100, back) : 0);
      op(E.steam, tt < T3 ? 1 - seg(tt, 900, 1700) : 1); move(E.steam, Math.sin(t / 260) * 2.5, -((t / 30) % 6));

      // 手机：桌上震动、弹出 Pearnly 的 LINE 消息；拿起来对着单据堆拍
      var buzzing = tt >= 5350 && tt < 5950 && ((tt - 5350) % 400) < 270;
      op(E.pflat, m.phone === 'flat' ? 1 : 0); move(E.pflat, buzzing ? Math.sin(t / 12) * 2.6 : 0, 0);
      op(E.buzz, buzzing ? 1 : 0);
      var lit = tt >= 5350 && tt < T3;
      op(E.pglow, lit ? .9 : 0); set(E.pscreen, 'fill', lit ? '#dff7e8' : '#2a2c33');
      var bub = tt >= 5400 && tt < T3 ? seg(tt, 5400, 5600, back) : 0;
      op(E.bubble, bub); set(E.bubble, 'transform', 'translate(744 284) scale(' + f2(Math.max(.01, bub)) + ')');
      var upK = m.phone === 'up' ? (tt < T4 ? seg(tt, 7350, 7480) : 1 - seg(tt, 11800, 11950)) : 0;
      var pc = [m.hn[0] + 22, m.hn[1] - 32];   // 手从下面托着手机
      op(E.pup, upK > .02 ? 1 : 0); move(E.pup, pc[0], pc[1], ' rotate(-3) scale(1 ' + f2(Math.max(.05, upK)) + ')');
      var snapD = -1, snapI = -1;
      SNAP.forEach(function (s, i) { if (tt >= s && (snapD < 0 || tt - s < snapD)) { snapD = tt - s; snapI = i; } });
      op(E.shutter, snapD >= 0 && snapD < 140 ? 1 - snapD / 140 : 0);
      var okK = snapD >= 120 && snapD < 520 ? seg(snapD, 120, 260, back) * (1 - seg(snapD, 420, 520)) : 0;
      op(E.ok, okK); set(E.ok, 'transform', 'translate(38 -16) scale(' + f2(Math.max(.01, okK)) + ')');
      op(E.vdoc, N > 0 ? 1 : 0); move(E.vdoc, snapD >= 140 && snapD < 320 ? 34 * (1 - seg(snapD, 140, 320)) : 0, 0);
      var big = snapI >= 0 && snapI < 5, bd = snapD >= 0 && snapD < 220 ? 1 - snapD / 220 : 0;
      op(E.burst, big ? bd : bd * .5); move(E.burst, pc[0], pc[1] + 34, ' scale(' + f2(.6 + .6 * (1 - bd)) + ')');
      op(E.flash, snapD >= 0 && snapD < 160 ? (big ? .3 : .1) * (1 - snapD / 160) : 0);

      // 拍下的单据飞进手机，数据从手机沿一道弧飞进屏幕（一口气全进去时连成一道数据流）
      var a0 = [pc[0] + 40, pc[1] - 14], c0 = [(a0[0] + TABLE[0]) / 2, Math.min(a0[1], TABLE[1]) - 150];
      E.fly.forEach(function (el, k) {
        var d = tt - SNAP[k];
        if (d < 0 || d >= 320) { op(el, 0); return; }
        var p = io(d / 320), at = arc([598, PILE_BASE - (ITEMS - k) * SLAB - 8], pc, p, 50);
        op(el, d > 260 ? 1 - (d - 260) / 60 : 1); move(el, at[0], at[1], ' rotate(' + f(-20 * p) + ') scale(' + f2(1 - .72 * p) + ')');
      });
      E.chips.forEach(function (el, k) {
        var d = tt - SNAP[k] - 260;
        if (d < 0 || d >= 420) { op(el, 0); return; }
        var p = io(d / 420), at = quad(a0, c0, TABLE, p);
        op(el, d < 60 ? d / 60 : d > 360 ? (420 - d) / 60 : 1); move(el, at[0], at[1], ' scale(' + f2(.8 + .4 * Math.sin(p * Math.PI)) + ')');
      });
      var flow = tt >= T3 ? seg(tt, 9950, 10150) * (1 - seg(tt, 11050, 11300)) : 0;
      op(E.stream, .75 * flow);
      if (flow > 0) {
        set(E.stream, 'd', 'M' + f(a0[0]) + ' ' + f(a0[1]) + 'Q' + f(c0[0]) + ' ' + f(c0[1]) + ' ' + TABLE[0] + ' ' + TABLE[1]);
        set(E.stream, 'stroke-dashoffset', String(f(-(t / 9) % 130)));
      }

      // 屏幕：过去是旧表格（逐字敲、敲错删掉重敲）；第二天先黑屏，敲一下开机进 Pearnly，一行行长出来、打勾，最后盖“已入账”
      op(E.old, tt < T3 ? 1 : 0); op(E.off, tt >= T3 && tt < 6900 ? 1 : 0);
      var boot = seg(tt, 6650, 6900);
      op(E.boot, tt >= 6650 && tt < 7000 ? 1 - seg(tt, 6900, 7000) : 0);
      set(E.boot, 'transform', 'translate(0 292) scale(1 ' + f2(1 + boot * 40) + ') translate(0 -292)');
      op(E.app, tt >= T3 ? seg(tt, 6850, 7050) : 0);
      var cursor = null, err = false;
      for (var c = 0; c < 3; c++) {
        var row = typed(c, tt), blink = Math.floor(t / 260) % 2 === 0;
        if (row[3] >= 0) { cursor = [c, row[3]]; err = row[2]; }
        text(E.oldS[2 + c], row[0] + (row[3] === 0 && blink ? '|' : ''));
        text(E.oldA[2 + c], row[1] + (row[3] === 1 && blink ? '|' : ''));
      }
      op(E.cell, cursor ? 1 : 0);
      if (cursor) { set(E.cell, 'y', String(249 + cursor[0] * 19 + 2 * 19 - 2)); set(E.cell, 'x', cursor[1] ? '1040' : '886'); set(E.cell, 'width', cursor[1] ? '92' : '154'); }
      op(E.typo, err ? 1 : 0);
      var fr = ARRIVE.map(function (a) { return out(c01((tt - a) / 260)); });
      E.rows.forEach(function (r, k) {
        if (tt < T3 || fr[k] <= 0) { op(r.g, 0); return; }
        var shift = 0; for (var j = k + 1; j < ITEMS; j++) shift += fr[j];
        op(r.g, fr[k]); move(r.g, 0, 22 * (shift - (1 - fr[k])));
        set(r.tick, 'stroke-dashoffset', String(f2(1 - c01((tt - ARRIVE[k] - 120) / 220))));
        op(r.hl, fr[k] * (1 - c01((tt - ARRIVE[k] - 350) / 700)));
      });
      text(E.count, count(ARRIVE, tt) + ' / ' + ITEMS);
      var st = seg(tt, 11550, 11900, back);
      op(E.stamp, tt >= 11550 ? c01((tt - 11550) / 150) : 0); set(E.stamp, 'transform', 'translate(1012 306) scale(' + f2(1.6 - .6 * st) + ')');

      // 小特效：叹气、靠回椅背时的“呼”、星星
      var puff = tt < T3 ? seg(tt, 5000, 5200) * (1 - seg(tt, 5450, 5650)) : seg(tt, 12450, 12600) * (1 - seg(tt, 12850, 13050));
      var mouth = bp(m, 24, -48), rise = tt < T3 ? seg(tt, 5000, 5650, lin) : seg(tt, 12450, 13050, lin);
      op(E.puff, puff); move(E.puff, mouth[0] + 10 + 14 * rise, mouth[1] - 6 - 30 * rise, ' scale(' + f2(.7 + .5 * rise) + ')');
      E.sparks.forEach(function (el, i) {
        var k = seg(tt, 13350 + i * 90, 13600 + i * 90, back), tw = 1 + .15 * Math.sin(t / 140 + i);
        var base = el._base || (el._base = el.getAttribute('transform'));
        op(el, k); set(el, 'transform', base + ' scale(' + f2(Math.max(.01, k) * tw) + ') rotate(' + f((t / 20 + i * 40) % 360) + ')');
      });

      // 转场：黄色记号笔从左刷到右（中间换到第二天；播完再刷一次接回开头）
      var wk = tt >= 5900 && tt < 6600 ? (tt - 5900) / 700 : t >= END + HOLD ? (t - END - HOLD) / WIPE : looped && t < WIPE / 2 ? .5 + t / WIPE : -1;
      move(E.swipe, wk < 0 ? -2600 : -2500 + 4200 * wk, 0);

      // 下面一行手写字：按剧情逐字写出来
      var cap = '';
      CAPS.forEach(function (c) { if (tt >= c[0]) cap = Array.from(c[1]).slice(0, Math.max(0, Math.floor((tt - c[0]) / 40))).join(''); });
      text(capEl, cap || ' ');
    }

    // 手机上只取中间一段（椅子 → 显示器）
    function fit() {
      var view = svg.getAttribute(card.clientWidth < 640 ? 'data-view-sm' : 'data-view'), v = view.split(' ');
      svg.setAttribute('viewBox', view);
      set(E.frame, 'x', v[0]); set(E.frame, 'y', v[1]); set(E.frame, 'width', v[2]); set(E.frame, 'height', v[3]);
    }
    svg._render = render;   // 调试：在控制台里 document.querySelector('.st')._render(毫秒) 跳到任一帧
    fit();
    window.addEventListener('resize', fit);
    if (reduce) { render(END); return; }

    // ---- 播放：出现在屏幕里才开始，滑走或切到别的标签页就暂停；播完停一下，记号笔刷过去从头再来 ----
    var start = 0, elapsed = 0, raf = 0, playing = false, visible = false, looped = false, LOOP = END + HOLD + WIPE / 2;
    function frame(now) {
      raf = 0;
      if (!playing) return;
      var t = now - start;
      if (t >= LOOP) { start += LOOP; t -= LOOP; looped = true; }
      render(t, looped);
      raf = requestAnimationFrame(frame);
    }
    function play() { if (playing || !visible || document.hidden) return; playing = true; start = performance.now() - elapsed; raf = requestAnimationFrame(frame); }
    function pause() { if (!playing) return; playing = false; elapsed = performance.now() - start; if (raf) cancelAnimationFrame(raf); raf = 0; }
    document.addEventListener('visibilitychange', function () { if (document.hidden) pause(); else play(); });
    render(0, false);
    if (!('IntersectionObserver' in window)) { visible = true; play(); return; }
    new IntersectionObserver(function (e) { visible = e[0].isIntersecting; if (visible) play(); else pause(); }, { threshold: .3 }).observe(svg);
  }

  // ---- 预约演示按钮：手机上直接打开预填好消息的 LINE 对话；电脑上留在加好友页（扫码） ----
  function lineMessages() {
    if (!/Android|iPhone|iPad|iPod/i.test(navigator.userAgent)) return;
    document.querySelectorAll('[data-line-message]').forEach(function (a) { a.href = a.getAttribute('data-line-message'); });
  }

  function init() { dots(); reveal(); topbar(); demo(); players(); mascots(); story(); lineMessages(); }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
