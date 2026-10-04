<script setup lang="ts">
// 扫身份证（LINE 内，票据模式 `scan`）：对准取景框自动拍 → 直传本站点 → 回聊天继续原流程。
//
// 只负责"拍一张够清楚的卡片照片并交上去"。识别、复核、建档仍是聊天里那条流程（`intake.scan`）：
// 交上去之后跳回交接页，由它替业务员在聊天里发一句话（带免费回覆令牌开始识别）再关窗。
// 不给相册选图：拍不了就回聊天直接发照片，原来的路一直都在。
import { onBeforeUnmount, onMounted, ref } from "vue";
import { Button, ErrorMessage, Spinner } from "frappe-ui";

import LineLayout from "../../components/LineLayout.vue";
import { ApiError } from "../../lib/api";
import { ensureScanTicket, forgetTicket, loadConfig, returnToChat, uploadScan } from "../../lib/dms-editor";
import { createTexts, type Locale } from "../../lib/i18n";
import {
  CARD_RATIO,
  SAMPLE_WIDTH,
  SETTLE_FRAMES,
  STEADY_FRAMES,
  analyze,
  cropRect,
  guideRect,
  hint as frameHint,
  motion,
  toGray,
  type Hint,
  type Rect,
} from "../../lib/id-scan";
import { loadLiffSdk } from "../../lib/liff";

const { t, locale, load: loadTexts, setLocale } = createTexts([], "dms.");

const stage = ref<"starting" | "aiming" | "sending" | "failed" | "nocamera">("starting");
const tip = ref<Hint>("place");
const errorText = ref("");
const video = ref<HTMLVideoElement | null>(null);
const frame = ref<Rect>({ x: 0, y: 0, w: 0, h: 0 });

const TIP_KEYS: Record<Hint, string> = {
  place: "dms.edit.scanTipPlace",
  focus: "dms.edit.scanTipFocus",
  light: "dms.edit.scanTipLight",
  glare: "dms.edit.scanTipGlare",
  hold: "dms.edit.scanTipHold",
  ready: "dms.edit.scanTipHold",
};
const ERROR_KEYS: Record<string, string> = {
  "dms_booking.expired": "dms.edit.scanExpired",
  "dms_scan.too_large": "dms.edit.scanTooLarge",
  "dms_booking.line_auth_required": "dms.edit.liffUnavailable",
  "dms_booking.not_bound": "dms.edit.liffUnavailable",
};
/** 取景框缩成 360 × 227 来判断（每帧几毫秒）。 */
const SAMPLE_HEIGHT = Math.round(SAMPLE_WIDTH / CARD_RATIO);

let ticket = "";
let stream: MediaStream | null = null;
let timer = 0;
let previous: Uint8Array | null = null;
let steady = 0;
let settled = 0;
let busy = false;
const sampler = document.createElement("canvas");
sampler.width = SAMPLE_WIDTH;
sampler.height = SAMPLE_HEIGHT;

function stopCamera(): void {
  window.clearInterval(timer);
  stream?.getTracks().forEach((track) => track.stop());
  stream = null;
}

/** 取景框：页面上与视频像素里各一份（视频按 cover 铺满取景区，见 `guideRect`）。 */
function currentGuide(el: HTMLVideoElement) {
  return guideRect(el.videoWidth, el.videoHeight, el.clientWidth || el.videoWidth, el.clientHeight || el.videoHeight);
}

function layout(): void {
  const el = video.value;
  if (!el || !el.videoWidth) return;
  frame.value = currentGuide(el).view;
}

function tick(): void {
  const el = video.value;
  if (busy || !el || el.readyState < 2 || !el.videoWidth) return;
  layout();
  const ctx = sampler.getContext("2d", { willReadFrequently: true });
  if (!ctx) return;
  const guide = currentGuide(el).video;
  ctx.drawImage(el, guide.x, guide.y, guide.w, guide.h, 0, 0, SAMPLE_WIDTH, SAMPLE_HEIGHT);
  const gray = toGray(ctx.getImageData(0, 0, SAMPLE_WIDTH, SAMPLE_HEIGHT).data, SAMPLE_WIDTH * SAMPLE_HEIGHT);
  const moved = motion(previous, gray);
  previous = gray;
  tip.value = frameHint(analyze(gray, SAMPLE_WIDTH, SAMPLE_HEIGHT), moved);
  steady = tip.value === "ready" ? steady + 1 : 0;
  // 其余都合格、只是还在轻微晃动：累计够了也拍（真机手持很难"完全不动"）。
  settled = tip.value === "ready" || tip.value === "hold" ? settled + 1 : 0;
  if (steady >= STEADY_FRAMES || settled >= SETTLE_FRAMES) void capture();
}

function explain(err: unknown): string {
  const code = err instanceof ApiError ? err.message : "";
  if (ERROR_KEYS[code]) return t(ERROR_KEYS[code]);
  if (err instanceof ApiError && err.status === 409) return t("dms.edit.scanExpired");
  return t("dms.edit.scanFailed");
}

function fail(err: unknown): void {
  stopCamera();
  errorText.value = explain(err);
  stage.value = "failed";
}

async function capture(): Promise<void> {
  const el = video.value;
  if (busy || !el || !el.videoWidth) return;
  busy = true;
  // 原分辨率裁取景框（四周放宽一点），不缩小：送去识别的照片不比在聊天里发的差。
  const area = cropRect(currentGuide(el).video, el.videoWidth, el.videoHeight);
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(area.w);
  canvas.height = Math.round(area.h);
  canvas.getContext("2d")?.drawImage(el, area.x, area.y, area.w, area.h, 0, 0, canvas.width, canvas.height);
  const image = canvas.toDataURL("image/jpeg", 0.92);
  stopCamera();
  stage.value = "sending";
  try {
    let result: { code: string; marker: string };
    try {
      result = await uploadScan(ticket, image);
    } catch (err) {
      // 票据 30 分钟过期：换一张再交一次，不让业务员重拍。
      if (!(err instanceof ApiError) || err.status !== 401) throw err;
      forgetTicket();
      ticket = await ensureScanTicket();
      result = await uploadScan(ticket, image);
    }
    returnToChat(result);
  } catch (err) {
    busy = false;
    fail(err);
  }
}

async function startCamera(): Promise<void> {
  if (!navigator.mediaDevices?.getUserMedia) {
    stage.value = "nocamera";
    return;
  }
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      audio: false,
      video: { facingMode: { ideal: "environment" }, width: { ideal: 1920 }, height: { ideal: 1080 } },
    });
  } catch {
    stage.value = "nocamera";
    return;
  }
  const el = video.value;
  if (!el) return;
  el.srcObject = stream;
  await el.play().catch(() => undefined);
  busy = false;
  steady = 0;
  settled = 0;
  previous = null;
  stage.value = "aiming";
  timer = window.setInterval(tick, 150);
}

/** 回 LINE：在 LINE 里关掉这一页，普通浏览器里退一步。 */
function backToChat(): void {
  stopCamera();
  const liff = (window as unknown as { liff?: Record<string, () => unknown> }).liff;
  if (liff && typeof liff.isInClient === "function" && liff.isInClient()) {
    (liff.closeWindow as () => void)();
    return;
  }
  history.back();
}

onMounted(async () => {
  try {
    await loadTexts();
  } catch {
    /* 文案拉不到就显示 key（看得见），不假装页面正常 */
  }
  try {
    const config = await loadConfig();
    if (config.liff_id) void loadLiffSdk();
    ticket = await ensureScanTicket();
  } catch (err) {
    fail(err);
    return;
  }
  await startCamera();
});

onBeforeUnmount(stopCamera);

function changeLocale(value: Locale): void {
  setLocale(value);
}
</script>

<template>
  <LineLayout :locale="locale" :disabled="stage === 'sending'" @update:locale="changeLocale">
    <section class="pn-scan">
      <div v-show="stage === 'starting' || stage === 'aiming'" class="pn-scan-view">
        <video ref="video" class="pn-scan-video" playsinline muted autoplay @loadedmetadata="layout" />
        <div
          class="pn-scan-frame"
          :class="{ 'pn-scan-frame-ready': tip === 'ready' }"
          :style="{ left: `${frame.x}px`, top: `${frame.y}px`, width: `${frame.w}px`, height: `${frame.h}px` }"
        />
      </div>

      <p v-if="stage === 'aiming'" class="pn-scan-tip">{{ t(TIP_KEYS[tip]) }}</p>
      <div v-if="stage === 'starting' || stage === 'sending'" class="pn-scan-state">
        <Spinner class="h-5 w-5" />
        <span>{{ stage === "sending" ? t("dms.edit.scanSending") : t("dms.edit.loading") }}</span>
      </div>
      <p v-if="stage === 'nocamera'" class="pn-scan-tip">{{ t("dms.edit.scanNoCamera") }}</p>
      <ErrorMessage v-if="stage === 'failed'" :message="errorText" />

      <div class="pn-scan-actions">
        <!-- 手动快门一直在：反光、光线差自动拍不到时随时能按。 -->
        <Button v-if="stage === 'aiming'" variant="solid" :label="t('dms.edit.scanShoot')" @click="capture" />
        <Button v-if="stage === 'failed'" variant="solid" :label="t('dms.edit.scanRetry')" @click="startCamera" />
        <Button variant="outline" theme="gray" :label="t('dms.edit.scanBack')" @click="backToChat" />
      </div>
    </section>
  </LineLayout>
</template>

<style scoped>
.pn-scan {
  display: grid;
  gap: 12px;
}
/* 取景区铺满宽度、固定 5:4，画面按 cover 裁边铺满：框占宽 90%，卡要拿近才对得上，字也更清楚
   （2026-10-04 真机：限高的竖长画面里框太小）。提示和拍照按钮始终在屏幕内。 */
.pn-scan-view {
  position: relative;
  overflow: hidden;
  width: 100%;
  aspect-ratio: 5 / 4;
  border-radius: 8px;
  background: #000;
}
.pn-scan-video {
  display: block;
  width: 100%;
  height: 100%;
  object-fit: cover;
}
.pn-scan-frame {
  position: absolute;
  border: 2px solid rgba(255, 255, 255, 0.9);
  border-radius: 10px;
  box-shadow: 0 0 0 9999px rgba(0, 0, 0, 0.35);
  pointer-events: none;
}
.pn-scan-frame-ready {
  border-color: var(--ink-green-3, #30a66d);
}
.pn-scan-tip {
  margin: 0;
  text-align: center;
  color: var(--ink);
}
.pn-scan-state {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
}
.pn-scan-actions {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: 8px;
}
</style>
