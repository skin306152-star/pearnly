// 代录对话里的资料上传（评估 docs/work/2026-11-04-cc-code-style.md 第 5 节，根治 BJC：上传后看不到状态 → 重复上传、重复扣费）：
//   - 输入框「＋」、拖进对话、拍照放进来的每一份，发送那一刻起就有一张卡片（components/run/upload-card.tsx），一份一个请求：
//     上传中按已传字节报百分比（live-source.ts 的 uploadWithProgress），上传失败就地「重试」同一份；
//   - 传上去以后的状态以站点为准（task_uploads：任务的上传记录 + 每份的识别读数，刷新、换设备、手机上都一样）：排队中 → 识别中
//     n/m 页 → 已识别 N 张（· N 张不在这个月）/ 识别失败；
//   - 重取：挂上时一次；本页刚传完一份、识别的站点事件来了、回到前台、实时通道重连时合成一次；还有在路上的就每 POLL_MS 再取一次
//     （页面看得见时）。任一份变成「已识别」时调 onRead（资料重取，不再只在上传返回时取一次）；
//   - 同一份再传就是又一张卡片、照常识别、照常扣费，不比对、不提示（产品口径：上传识别都扣费）。
// 本页传的在站点记录出来之前按本页的进度画（站点没有 task_uploads 的旧版本时一直按本页的）；对上站点那一行（同名、同大小、
// 时间不早于本页开始传的那一刻）后换成站点的。

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { create } from "zustand";
import { uploadBusy, uploadState } from "@/components/run/upload-state";
import type { TaskUpload, UploadState } from "@/components/run/types";
import { co, op } from "@/i18n";
import { monthLabel } from "@/lib/companies";
import { shownError } from "@/lib/frappe";
import { onConnectionChange, onSiteEvent } from "@/lib/managed-agents/realtime";
import { reportCaught } from "@/lib/caught";
import type { OperatorSource } from "./types";

/** 有在路上的（排队、识别）时多久向站点再取一次（毫秒）；只在页面看得见时。 */
export const POLL_MS = 10_000;
/** 识别进度、识别结果的站点事件（共用识别的进度推送、Cowork 录入变化）：来了就重取。 */
export const UPLOAD_EVENTS = ["pearnly_ocr_work", "cowork_intake_changed"] as const;
/** 站点记的上传时间可能比本页开始传的时刻早一点（两边时钟不齐）：对得上的宽限（毫秒）。 */
const CLOCK_SLACK_MS = 60_000;

/** 本页传的一份。phase：uploading 在传、sent 传完了（站点记录还没对上）、failed 没传上去。 */
export type LocalUpload = {
  id: string;
  task: string;
  company: string;
  period: string;
  file: File;
  at: number;
  percent: number;
  phase: "uploading" | "sent" | "failed";
  reason: string;
};

export const useLocalUploads = create<{ rows: LocalUpload[] }>(() => ({ rows: [] }));

const patch = (id: string, change: Partial<LocalUpload>) =>
  useLocalUploads.setState((s) => ({ rows: s.rows.map((row) => (row.id === id ? { ...row, ...change } : row)) }));

/** 本页传完一份就通知卡片那一头重取站点记录（不等下一次轮询）。 */
const sentListeners = new Set<(task: string) => void>();

async function push(source: OperatorSource, row: LocalUpload) {
  try {
    await source.intake({ company: row.company, period: row.period, task: row.task, file: row.file, onProgress: (percent) => patch(row.id, { percent }) });
    patch(row.id, { phase: "sent", percent: 100 });
    sentListeners.forEach((listener) => listener(row.task));
  } catch (caughtError) { reportCaught(caughtError, "operator-upload");
    patch(row.id, { phase: "failed", reason: shownError(caughtError) });
  }
}

/** 放进来的几份：一份一张卡片，按顺序一份一个请求。 */
export async function sendUploads(source: OperatorSource, target: { task: string; company: string; period: string }, files: readonly File[]): Promise<void> {
  if (!target.task || !files.length) return;
  const at = Date.now();
  const rows = files.map((file, index): LocalUpload => ({
    id: `up-${at}-${index}-${Math.random().toString(36).slice(2, 8)}`,
    ...target,
    file,
    at: at + index,
    percent: 0,
    phase: "uploading",
    reason: "",
  }));
  useLocalUploads.setState((s) => ({ rows: [...s.rows, ...rows] }));
  for (const row of rows) await push(source, row);
}

/** 上传失败的那一份原样再传（同一张卡片）。 */
export function retryUpload(source: OperatorSource, id: string): void {
  const row = useLocalUploads.getState().rows.find((r) => r.id === id);
  if (!row || row.phase !== "failed") return;
  const again = { ...row, phase: "uploading" as const, percent: 0, reason: "" };
  patch(id, again);
  void push(source, again);
}

/** 对话里的一张卡片：key 稳定（本页的换成站点的那一行时不跳）；at 是上传那一刻（毫秒，按它插进对话）。 */
export type UploadItem = { key: string; name: string; size: number; at: number; state: UploadState; local?: string };

/** 识别失败码 → 给人看的一句（同台账的异常码说法；认不出的写「需要处理」）。 */
const failReason = (code: string) => (co.ledger.code as Record<string, string>)[code] ?? op.unknownReason;

const siteMs = (value: string) => {
  const ms = Date.parse(value);
  return Number.isFinite(ms) ? ms : 0;
};

/** 本页传的与站点记录合成卡片：站点的每一行一张；本页传的对上了站点那一行就并进去，没对上（在传、刚传完、没传上去）单独一张。 */
export function mergeUploads(server: readonly TaskUpload[], local: readonly LocalUpload[], period: string): UploadItem[] {
  const month = period ? monthLabel(period) : undefined;
  const claimed = new Map<string, string>();
  for (const row of local) {
    if (row.phase !== "sent") continue;
    const hit = server.find((s) => !claimed.has(s.original || s.job) && s.filename === row.file.name && s.size === row.file.size && siteMs(s.uploaded_at) >= row.at - CLOCK_SLACK_MS);
    if (hit) claimed.set(hit.original || hit.job, row.id);
  }
  const items: UploadItem[] = server.map((row) => {
    const mine = claimed.get(row.original || row.job);
    const at = local.find((r) => r.id === mine)?.at ?? siteMs(row.uploaded_at);
    return { key: mine ?? `site-${row.original || row.job}`, name: row.filename, size: row.size, at, state: uploadState(row, failReason, month), ...(mine ? { local: mine } : {}) };
  });
  const taken = new Set(claimed.values());
  for (const row of local) {
    if (taken.has(row.id)) continue;
    const state: UploadState =
      row.phase === "uploading" ? { kind: "uploading", percent: row.percent }
        : row.phase === "failed" ? { kind: "failed", stage: "upload", reason: row.reason }
          : { kind: "queued" };
    items.push({ key: row.id, name: row.file.name, size: row.file.size, at: row.at, state, local: row.id });
  }
  return items.sort((a, b) => a.at - b.at);
}

/** 一个任务的上传卡片（文件头）。onRead：有一份刚识别完（资料要重取）。 */
export function useTaskUploads(source: OperatorSource, task: { name: string; period: string } | null, onRead?: () => void): UploadItem[] {
  const name = task?.name ?? "";
  const [server, setServer] = useState<TaskUpload[]>([]);
  const local = useLocalUploads((s) => s.rows);
  const mine = useMemo(() => local.filter((row) => row.task === name), [local, name]);
  const ask = useRef(0);
  const read = useCallback(async () => {
    if (!name) return;
    const ticket = ++ask.current;
    try {
      const rows = await source.taskUploads(name);
      if (ticket === ask.current && rows) setServer(rows);
    } catch (caughtError) { reportCaught(caughtError, "operator-uploads");
      // 取不到：卡片照上一次的样子，下一次再取
    }
  }, [source, name]);
  useEffect(() => {
    setServer([]);
    void read();
  }, [read]);
  const items = useMemo(() => mergeUploads(server, mine, task?.period ?? ""), [server, mine, task?.period]);
  const busy = items.some((item) => uploadBusy(item.state));
  // 识别完的份数多了：资料重取
  const doneCount = server.filter((row) => row.status === "done").length;
  const seenDone = useRef<number | null>(null);
  const latestRead = useRef(onRead);
  latestRead.current = onRead;
  useEffect(() => {
    if (seenDone.current !== null && doneCount > seenDone.current) latestRead.current?.();
    seenDone.current = doneCount;
  }, [doneCount]);
  useEffect(() => {
    seenDone.current = null;
  }, [name]);
  // 重取：本页传完一份、站点事件、回到前台、重连；有在路上的再按 POLL_MS 轮询（看得见时）
  useEffect(() => {
    if (!name) return;
    let timer = 0;
    const soon = () => {
      window.clearTimeout(timer);
      timer = window.setTimeout(() => void read(), 1000);
    };
    const onSent = (sent: string) => sent === name && soon();
    sentListeners.add(onSent);
    const offs = UPLOAD_EVENTS.map((event) => onSiteEvent(event, soon));
    const offConnection = onConnectionChange((connected) => connected && soon());
    const onVisible = () => document.visibilityState === "visible" && soon();
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      window.clearTimeout(timer);
      sentListeners.delete(onSent);
      offs.forEach((off) => off());
      offConnection();
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [name, read]);
  useEffect(() => {
    if (!name || !busy) return;
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") void read();
    }, POLL_MS);
    return () => window.clearInterval(timer);
  }, [name, busy, read]);
  return items;
}
