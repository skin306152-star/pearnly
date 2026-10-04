/**
 * 扫身份证页的取景判断：什么时候自动按快门。纯计算，不碰 DOM，便于单独测。
 *
 * 只判"这一帧够不够好"，不做识别、不做透视校正 —— 识别仍由服务端同一个模型读原图
 * （它本来就能读斜拍的照片）。判断在手机上每 150 毫秒做一次，只看取景框里缩小后的灰度图，
 * 不加载 OpenCV 这类几 MB 的库（展厅里用流量，第一次打开要快）。
 *
 * 自动拍的条件：框的四条边里至少三条上有卡片边缘、亮度正常，**身份证号、姓名、地址三块都清楚、
 * 有字、没有反光**（"看清必须要的字段才拍"，用户 2026-10-04），并且连续约 0.6 秒稳定。
 * 拍不到时页面上一直有手动快门。
 */

/** 泰国身份证是 ID-1 尺寸：85.6 × 54 毫米。 */
export const CARD_RATIO = 85.6 / 54;

export interface Rect {
  x: number;
  y: number;
  w: number;
  h: number;
}

/** 取景框占取景区宽度的比例（2026-10-04 真机：86% 的竖屏画面里框太小，卡要拿得很远）。 */
export const GUIDE_WIDTH = 0.9;

/** 视频按 object-fit: cover 铺进取景区：缩放倍数与偏移（页面像素 = 视频像素 × k + 偏移）。 */
export function coverMap(videoWidth: number, videoHeight: number, viewWidth: number, viewHeight: number) {
  const k = Math.max(viewWidth / videoWidth, viewHeight / videoHeight);
  return { k, ox: (viewWidth - videoWidth * k) / 2, oy: (viewHeight - videoHeight * k) / 2 };
}

/** 取景框：页面上（`view`）与视频像素里（`video`）各一份。框在取景区正中、按身份证比例、占宽 90%。 */
export function guideRect(videoWidth: number, videoHeight: number, viewWidth = videoWidth, viewHeight = videoHeight) {
  const w = Math.min(viewWidth * GUIDE_WIDTH, viewHeight * GUIDE_WIDTH * CARD_RATIO);
  const h = w / CARD_RATIO;
  const view: Rect = { x: (viewWidth - w) / 2, y: (viewHeight - h) / 2, w, h };
  const { k, ox, oy } = coverMap(videoWidth, videoHeight, viewWidth, viewHeight);
  const video: Rect = { x: (view.x - ox) / k, y: (view.y - oy) / k, w: w / k, h: h / k };
  return { view, video };
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

/**
 * 一定要看清的三块（按泰国身份证正面版式，取景框内的比例坐标 x0, y0, x1, y1）：
 * 13 位身份证号、泰文姓名、地址。2026-10-04 用真卡截图校准：清楚的每块清晰度约 1450–1550，
 * 拿远一倍约 930–1070，轻度模糊约 630–740，手晃拖影约 400–460。
 */
export const FIELD_REGIONS = {
  number: [0.44, 0.12, 0.82, 0.24],
  name: [0.3, 0.22, 0.86, 0.35],
  address: [0.12, 0.66, 0.66, 0.81],
} as const;
export type Field = keyof typeof FIELD_REGIONS;

export interface FieldStats {
  sharpness: number;
  /** 灰度标准差：这块有没有字（没对准、被手指挡住时很低）。 */
  contrast: number;
  glare: number;
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
  fields: Record<Field, FieldStats>;
}

function regionStats(gray: Uint8Array, width: number, height: number, box: readonly number[]): FieldStats {
  const x0 = Math.max(1, Math.round(box[0] * width));
  const x1 = Math.min(width - 1, Math.round(box[2] * width));
  const y0 = Math.max(1, Math.round(box[1] * height));
  const y1 = Math.min(height - 1, Math.round(box[3] * height));
  let n = 0, sum = 0, sq = 0, lapSum = 0, lapSq = 0, bright = 0;
  for (let y = y0; y < y1; y += 1) {
    for (let x = x0; x < x1; x += 1) {
      const i = y * width + x;
      const v = gray[i];
      const lap = 4 * v - gray[i - 1] - gray[i + 1] - gray[i - width] - gray[i + width];
      n += 1;
      sum += v;
      sq += v * v;
      lapSum += lap;
      lapSq += lap * lap;
      if (v >= 248) bright += 1;
    }
  }
  if (!n) return { sharpness: 0, contrast: 0, glare: 0 };
  const mean = sum / n;
  const lapMean = lapSum / n;
  return {
    sharpness: lapSq / n - lapMean * lapMean,
    contrast: Math.sqrt(Math.max(0, sq / n - mean * mean)),
    glare: bright / n,
  };
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
  const fields = Object.fromEntries(
    (Object.keys(FIELD_REGIONS) as Field[]).map((name) => [name, regionStats(gray, width, height, FIELD_REGIONS[name])]),
  ) as Record<Field, FieldStats>;
  return {
    sharpness: n ? lapSq / n - mean * mean : 0,
    brightness: pixels ? sum / pixels : 0,
    glare: pixels ? bright / pixels : 0,
    edges,
    fields,
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
/** 每块都要达到的清晰度（取景框缩成 360 像素宽时；校准见 `FIELD_REGIONS`）。 */
export const FIELD_SHARPNESS = 800;
/** 每块至少这么多字迹对比，低于它多半是没对准或被挡住。 */
export const FIELD_CONTRAST = 18;
/** 任何一块反光超过这个比例都不拍（号码处一点反光就会读错位）。 */
export const FIELD_GLARE = 0.05;
/**
 * 两帧平均差的上限。手拿卡、手拿手机，真机静止时也到不了 6（2026-10-04 iPhone：5 分钟停在"拿稳"）；
 * 20 又太松，刚对上就拍（同日第二轮：拍到的三张都读不清）。晃动造成的糊由逐块清晰度挡住。
 */
export const MOTION_LIMIT = 12;
/** 连续多少帧都合格才拍（每 150 毫秒一帧，约 0.6 秒）。 */
export const STEADY_FRAMES = 4;
/** 三块都清楚、只差"拿稳"的帧累计到这么多（约 2 秒）也拍。 */
export const SETTLE_FRAMES = 14;
/** 判断用的取景框缩略尺寸。 */
export const SAMPLE_WIDTH = 360;

export type Hint = "place" | "focus" | "light" | "glare" | "hold" | "ready";

/** 这一帧该提示什么；`ready` 表示这一帧合格。 */
export function hint(stats: FrameStats, moved: number): Hint {
  if (stats.edges < 3) return "place";
  if (stats.brightness < 55 || stats.brightness > 225) return "light";
  const fields = Object.values(stats.fields);
  if (fields.some((field) => field.glare > FIELD_GLARE)) return "glare";
  if (fields.some((field) => field.contrast < FIELD_CONTRAST)) return "place";
  if (fields.some((field) => field.sharpness < FIELD_SHARPNESS)) return "focus";
  if (moved > MOTION_LIMIT) return "hold";
  return "ready";
}
