/**
 * 扫身份证页的取景判断：什么时候自动按快门。纯计算，不碰 DOM，便于单独测。
 *
 * 只判"这一帧够不够好"，不做识别、不做透视校正 —— 识别仍由服务端同一个模型读原图
 * （它本来就能读斜拍的照片）。判断在手机上每 150 毫秒做一次，只看取景框里缩小后的灰度图，
 * 不加载 OpenCV 这类几 MB 的库（展厅里用流量，第一次打开要快）。
 *
 * 自动拍的条件：框的四条边里至少三条上有明显的卡片边缘、画面清楚、亮度正常、没有大片反光，
 * 并且连续几帧几乎不动。阈值按真机实测再调；拍不到时页面会给手动快门。
 */

/** 泰国身份证是 ID-1 尺寸：85.6 × 54 毫米。 */
export const CARD_RATIO = 85.6 / 54;

export interface Rect {
  x: number;
  y: number;
  w: number;
  h: number;
}

/** 取景框：画面中间、按身份证比例、占画面宽（或高）的 86%。坐标是视频像素。 */
export function guideRect(videoWidth: number, videoHeight: number): Rect {
  const w = Math.min(videoWidth * 0.86, videoHeight * 0.86 * CARD_RATIO);
  const h = w / CARD_RATIO;
  return { x: (videoWidth - w) / 2, y: (videoHeight - h) / 2, w, h };
}

/** 上传用的裁剪区：取景框四周各放宽 6%，卡片稍微没对齐也不会被切掉字。 */
export function cropRect(guide: Rect, videoWidth: number, videoHeight: number): Rect {
  const mx = guide.w * 0.06;
  const my = guide.h * 0.06;
  const x = Math.max(0, guide.x - mx);
  const y = Math.max(0, guide.y - my);
  return {
    x,
    y,
    w: Math.min(videoWidth, guide.x + guide.w + mx) - x,
    h: Math.min(videoHeight, guide.y + guide.h + my) - y,
  };
}

export interface FrameStats {
  /** 拉普拉斯方差：越大越清楚。 */
  sharpness: number;
  /** 平均亮度 0–255。 */
  brightness: number;
  /** 过曝像素（≥ 248）的比例：反光。 */
  glare: number;
  /** 框四条边里检出卡片边缘的条数（0–4）。 */
  edges: number;
}

/** RGBA 像素 → 灰度（ITU-R BT.601 权重）。 */
export function toGray(rgba: ArrayLike<number>, pixels: number): Uint8Array {
  const gray = new Uint8Array(pixels);
  for (let i = 0; i < pixels; i += 1) {
    const o = i * 4;
    gray[i] = (rgba[o] * 299 + rgba[o + 1] * 587 + rgba[o + 2] * 114) / 1000;
  }
  return gray;
}

/**
 * 边缘：在框的某一条边附近（内外各一小段）逐行（或逐列）求垂直于这条边的平均梯度，
 * 取最强的那一行。卡片边缘是一条贯穿的直线，平均下来仍然很强；桌面纹理、字迹平均下来就弱。
 */
function edgeStrength(gray: Uint8Array, width: number, height: number, side: "top" | "bottom" | "left" | "right"): number {
  const horizontal = side === "top" || side === "bottom";
  const span = horizontal ? height : width;
  const band = Math.max(2, Math.round(span * 0.12));
  const center = side === "top" || side === "left" ? Math.round(span * 0.06) : span - 1 - Math.round(span * 0.06);
  let best = 0;
  for (let d = -band; d <= band; d += 1) {
    const line = center + d;
    if (line < 1 || line >= span - 1) continue;
    let sum = 0;
    let count = 0;
    if (horizontal) {
      for (let x = Math.round(width * 0.1); x < width - Math.round(width * 0.1); x += 1) {
        sum += Math.abs(gray[(line + 1) * width + x] - gray[(line - 1) * width + x]);
        count += 1;
      }
    } else {
      for (let y = Math.round(height * 0.1); y < height - Math.round(height * 0.1); y += 1) {
        sum += Math.abs(gray[y * width + line + 1] - gray[y * width + line - 1]);
        count += 1;
      }
    }
    best = Math.max(best, count ? sum / count : 0);
  }
  return best;
}

/** 一帧取景框里的统计（`gray` 是缩小后的取景框灰度图，`width × height`）。 */
export function analyze(gray: Uint8Array, width: number, height: number, edgeThreshold = EDGE_THRESHOLD): FrameStats {
  const pixels = width * height;
  let sum = 0;
  let bright = 0;
  for (let i = 0; i < pixels; i += 1) {
    sum += gray[i];
    if (gray[i] >= 248) bright += 1;
  }
  let lapSum = 0;
  let lapSq = 0;
  let n = 0;
  for (let y = 1; y < height - 1; y += 1) {
    for (let x = 1; x < width - 1; x += 1) {
      const i = y * width + x;
      const lap = 4 * gray[i] - gray[i - 1] - gray[i + 1] - gray[i - width] - gray[i + width];
      lapSum += lap;
      lapSq += lap * lap;
      n += 1;
    }
  }
  const mean = n ? lapSum / n : 0;
  const edges = (["top", "bottom", "left", "right"] as const)
    .filter((side) => edgeStrength(gray, width, height, side) >= edgeThreshold).length;
  return {
    sharpness: n ? lapSq / n - mean * mean : 0,
    brightness: pixels ? sum / pixels : 0,
    glare: pixels ? bright / pixels : 0,
    edges,
  };
}

/** 两帧之间的平均差：拿稳没有。 */
export function motion(previous: Uint8Array | null, current: Uint8Array): number {
  if (!previous || previous.length !== current.length) return Number.POSITIVE_INFINITY;
  let sum = 0;
  for (let i = 0; i < current.length; i += 1) sum += Math.abs(current[i] - previous[i]);
  return current.length ? sum / current.length : 0;
}

export const EDGE_THRESHOLD = 14;
export const SHARPNESS_THRESHOLD = 300;
export const GLARE_LIMIT = 0.03;
export const MOTION_LIMIT = 6;
/** 连续多少帧都合格才拍（每 150 毫秒一帧，约半秒）。 */
export const STEADY_FRAMES = 3;

export type Hint = "place" | "focus" | "light" | "glare" | "hold" | "ready";

/** 这一帧该提示什么；`ready` 表示这一帧合格。 */
export function hint(stats: FrameStats, moved: number): Hint {
  if (stats.edges < 3) return "place";
  if (stats.brightness < 55 || stats.brightness > 225) return "light";
  if (stats.glare > GLARE_LIMIT) return "glare";
  if (stats.sharpness < SHARPNESS_THRESHOLD) return "focus";
  if (moved > MOTION_LIMIT) return "hold";
  return "ready";
}
