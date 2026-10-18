// 产品形象小单：图来自宣传页（scripts/build-assistant-brand.py 从 website/scenes.py 生成），
// 眨眼与「眼睛跟着鼠标」照搬 website/static/site.js 的 mascots()（同一算法、同一参数），不另做。
//
// 小动画（globals.css「产品形象小单」）只在出现时播几轮、播完停在静止姿势，不像宣传页那样无限循环（静置的 CC 桌面版不再一直重画，
// docs/work/2026-10-18-cc-idle-animations.md）。这里只管两件事：
//   暂停 — 页面切到后台（visibilityState hidden）时给外层标 data-cc-paused，动画停住；回到前台去掉，接着播剩下的。
//   回放 — play 变了（例如问候语换了），而且上一次已经播完，就从头再播一次：外层标 data-cc-replay 摘掉动画、读一次版面让它生效、
//          再去掉，浏览器按新动画从头开始。还在播时不打断（从头开始会跳一下）。减少动效时 globals.css 不给动画，回放也就没有动画可播。

import { useEffect, useRef, useState } from "react";

const cache = new Map<string, Promise<string>>();

function load(src: string): Promise<string> {
  let text = cache.get(src);
  if (!text) {
    text = fetch(src).then((res) => (res.ok ? res.text() : ""));
    cache.set(src, text);
  }
  return text;
}

/** 形象里还有没播完（在播或在后台停住）的小动画。外层自己的动画（加载页的上下浮）不算。 */
function stillPlaying(root: HTMLElement): boolean {
  if (typeof root.getAnimations !== "function") return false;
  return root.getAnimations({ subtree: true }).some((a) => (a.effect as KeyframeEffect | null)?.target !== root && a.playState !== "finished");
}

function replay(root: HTMLElement) {
  if (!root.querySelector("svg") || stillPlaying(root)) return;
  root.setAttribute("data-cc-replay", "");
  root.getBoundingClientRect();
  root.removeAttribute("data-cc-replay");
}

export function Mascot({ src, className, play }: { src: string; className?: string; play?: string }) {
  const [svg, setSvg] = useState("");
  const host = useRef<HTMLSpanElement>(null);

  useEffect(() => {
    void load(src).then(setSvg);
  }, [src]);

  useEffect(() => {
    const root = host.current;
    if (!root) return;
    const sync = () => root.toggleAttribute("data-cc-paused", document.visibilityState === "hidden");
    sync();
    document.addEventListener("visibilitychange", sync);
    return () => document.removeEventListener("visibilitychange", sync);
  }, []);

  // 第一次出现由浏览器自己从头播；之后 play 变了才回放
  const shownPlay = useRef(play);
  useEffect(() => {
    if (shownPlay.current === play) return;
    shownPlay.current = play;
    if (host.current) replay(host.current);
  }, [play]);

  useEffect(() => {
    const root = host.current;
    if (!svg || !root || matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const looks = Array.from(root.querySelectorAll<SVGGElement>(".hm .m-look, .mascot .m-look"));
    if (!looks.length) return;
    let px = 0;
    let py = 0;
    let queued = false;
    const update = () => {
      queued = false;
      for (const g of looks) {
        const r = ((g.closest(".hm") as Element | null) ?? g.ownerSVGElement!).getBoundingClientRect();
        if (r.bottom < 0 || r.top > window.innerHeight) continue;
        const dx = px - (r.left + r.width / 2);
        const dy = py - (r.top + r.height * 0.3);
        const d = Math.sqrt(dx * dx + dy * dy) || 1;
        const k = Math.min(1, d / 240) * (g.closest(".hm-cc") ? 7 : 5);
        g.setAttribute("transform", `translate(${((dx / d) * k).toFixed(2)} ${((dy / d) * k).toFixed(2)})`);
      }
    };
    const onMove = (e: PointerEvent) => {
      px = e.clientX;
      py = e.clientY;
      if (!queued) {
        queued = true;
        requestAnimationFrame(update);
      }
    };
    window.addEventListener("pointermove", onMove, { passive: true });
    return () => window.removeEventListener("pointermove", onMove);
  }, [svg]);

  return <span ref={host} className={className} dangerouslySetInnerHTML={{ __html: svg }} />;
}
