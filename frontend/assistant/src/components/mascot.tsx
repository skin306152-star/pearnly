// 产品形象小单：图来自宣传页（scripts/build-assistant-brand.py 从 website/scenes.py 生成），
// 眨眼与「眼睛跟着鼠标」照搬 website/static/site.js 的 mascots()（同一算法、同一参数），不另做。

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

export function Mascot({ src, className }: { src: string; className?: string }) {
  const [svg, setSvg] = useState("");
  const host = useRef<HTMLSpanElement>(null);

  useEffect(() => {
    void load(src).then(setSvg);
  }, [src]);

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
