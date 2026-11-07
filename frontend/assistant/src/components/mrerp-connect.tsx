// MR.ERP 的「连接」（用户 11-07：连接设置里加 MR.ERP 一行；MR.ERP 连接只留这一个入口。评估
// docs/work/2026-11-07-cc-connect-company-picker-评估.md §1.4 与 docs/work/2026-11-07-mrerp-connect-one-entry-评估.md）。
//
//   MrerpRow      — 设置 → 连接最上面一行「MR.ERP」：状态（未连接 / 未授权 / 已授权：用户名 · 电脑 · 时间）+「连接」。只在产品
//                   声明了 connect.mrerp 时出（共用层 projects() 的 connect：Cowork 给 connections 接口；ERP 只在 MR.ERP 账本开着时
//                   给），页面不按产品名分支。CC 桌面版「此电脑 → 连接」与浏览器的「连接」同一行。
//   MrerpConnect  — 点「连接」的弹窗（原定时任务里的那一个搬来；定时任务、Cowork Desk 的「连接」都改成打开设置的连接一节）：
//                   系统地址（存下走 configure，只给管理员，服务端判）+ MrErpLogin（密码只在 CC 桌面版原生小窗填，浏览器、手机里
//                   按钮灰）。存下或授权成功后请服务端读一次这条连接的账套目录（声明接口的 request_refresh），选择公司弹窗的 MR.ERP
//                   标签下就有这些公司。
// 不提供断开 / 删除连接（产品本来就不允许删连接）。

import { Dialog as DialogPrimitive } from "radix-ui";
import { XIcon } from "lucide-react";
import { type FC, useCallback, useEffect, useMemo, useState } from "react";
import { authorizedDetail, type MrErpCredential, MrErpLogin } from "@/components/mrerp-login";
import { st, t } from "@/i18n";
import { getMethod, postMethod, shownError } from "@/lib/frappe";
import { useProjects } from "@/lib/managed-agents/projects";
import { useProfile } from "@/lib/profile";
import { Marquee } from "@/components/ui/marquee";
import { reportCaught, withReport } from "../lib/caught";

export type MrerpConnection = {
  name: string; adapter: string; enabled?: number | boolean; revision?: number; main?: boolean;
  config?: Record<string, string>; credential?: MrErpCredential;
};

const DEFAULT_URL = "https://www.mrerp4sme.com";
const requestKey = () => `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
/** 存下或授权后请服务端读一次这条连接的账套目录（声明的连接接口的 request_refresh，同「刷新账套」；不按产品写死接口）。 */
const refreshCatalog = (api: string, name: string) => postMethod(api + "request_refresh", { name, request_key: requestKey() });

/** 产品声明的 MR.ERP 连接接口前缀（"" = 这个站点不出 MR.ERP 一行）。 */
export const useMrerpApi = (): string => useProjects((s) => s.connect.mrerp ?? "");

/** 主连接（本归属下的那一条，服务端在 records 里标 main）。 */
export const mainMrerp = (rows: readonly MrerpConnection[] | null | undefined): MrerpConnection | null =>
  (rows ?? []).find((row) => row.adapter === "mrerp" && row.main) ?? null;

/** 这一行的状态：没有连接「未连接」；有连接没授权「未授权」；授权过「已授权：用户名 · 电脑 · 时间」。 */
export function mrerpStatus(row: Pick<MrerpConnection, "credential"> | null): string {
  if (!row) return st.notConnected;
  const detail = authorizedDetail(row.credential);
  return detail ? st.mrerpAuthorized(detail) : st.mrerpNotAuthorized;
}

// map: mrerp-connect@cowork
/** 设置 → 连接最上面一行「MR.ERP」。 */
export const MrerpRow: FC = () => {
  const api = useMrerpApi();
  const [row, setRow] = useState<MrerpConnection | null | undefined>(undefined);
  const [open, setOpen] = useState(false);
  const reload = useCallback(() => {
    if (!api) return;
    void getMethod<MrerpConnection[]>(api + "records", { limit: 100 })
      .then((rows) => setRow(mainMrerp(rows)), withReport(() => setRow(null), "mrerp-connect"));
  }, [api]);
  useEffect(reload, [reload]);
  if (!api || row === undefined) return null;
  return (
    <>
      <div data-marquee-host="" className="cc-settings-row" data-slot="cc-mrerp-row">
        <div className="min-w-0 flex-1">
          <Marquee as="div" className="truncate text-body">MR.ERP</Marquee>
          <Marquee as="div" className="truncate text-muted-foreground text-caption" data-slot="cc-mrerp-status">{mrerpStatus(row)}</Marquee>
        </div>
        <button type="button" className="cc-btn" data-slot="cc-mrerp-connect" onClick={() => setOpen(true)}>
          {st.connect}
        </button>
      </div>
      {open && (
        <MrerpConnect
          api={api}
          connection={row}
          onClose={(changed, name) => {
            setOpen(false);
            reload();
            if (changed && name) void refreshCatalog(api, name).catch(withReport(() => undefined, "mrerp-connect"));
          }}
        />
      )}
    </>
  );
};

/** 「连接 MR.ERP」弹窗。onClose(changed, name)：存了地址或授权成功（要读一次目录）与那条连接。 */
export const MrerpConnect: FC<{ api: string; connection: MrerpConnection | null; onClose: (changed: boolean, name: string) => void }> = ({
  api,
  connection,
  onClose,
}) => {
  const product = useProfile()?.product;
  const key = useMemo(requestKey, []);
  const saved = connection?.config?.system_url || DEFAULT_URL;
  const [url, setUrl] = useState(saved);
  const [current, setCurrent] = useState<MrerpConnection | null>(connection);
  const [changed, setChanged] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  // 地址没改、已有连接时不再存；没有连接先建一条
  const save = async (): Promise<MrerpConnection | null> => {
    if (current && url === (current.config?.system_url || DEFAULT_URL)) return current;
    const next = await postMethod<MrerpConnection>(api + "configure", {
      adapter: "mrerp", request_key: key, label: "MR.ERP", config: { system_url: url },
      ...(current ? { name: current.name, expected_revision: current.revision } : {}),
    });
    setCurrent(next);
    setChanged(true);
    return next;
  };
  const submit = async () => {
    setBusy(true);
    setError("");
    try {
      const next = await save();
      onClose(true, next?.name ?? "");
    } catch (e) { reportCaught(e, "mrerp-connect");
      setError(shownError(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <DialogPrimitive.Root open onOpenChange={(v) => !v && onClose(changed, current?.name ?? "")}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="cc-overlay z-(--cc-z-confirm)" />
        <DialogPrimitive.Content
          aria-describedby={undefined}
          data-slot="cc-mrerp-connect-dialog"
          data-size="sm"
          className="cc-dialog cc-enter cc-modal fixed left-1/2 z-(--cc-z-confirm) flex -translate-x-1/2 -translate-y-1/2 flex-col"
        >
          <div className="cc-dialog-head">
            <DialogPrimitive.Title className="cc-dialog-title">{`${st.connect} MR.ERP`}</DialogPrimitive.Title>
            <DialogPrimitive.Close className="cc-icon-btn" aria-label={t("close")}>
              <XIcon />
            </DialogPrimitive.Close>
          </div>
          <div className="cc-dialog-body flex flex-col gap-(--cc-space-16)">
            <label className="flex flex-col gap-(--cc-space-6)">
              <span className="text-body">{st.systemUrl}</span>
              <input className="cc-input" value={url} onChange={(e) => setUrl(e.target.value)} data-slot="cc-mrerp-url" />
            </label>
            <MrErpLogin
              product={product}
              connection={current}
              ensure={async () => (await save().catch((e: unknown) => { reportCaught(e, "mrerp-connect"); setError(shownError(e)); return null; }))?.name ?? null}
              onDone={() => {
                setChanged(true);
                void getMethod<MrerpConnection[]>(api + "records", { limit: 100 })
                  .then((rows) => setCurrent(rows?.find((row) => row.name === current?.name) ?? mainMrerp(rows)))
                  .catch(withReport(() => undefined, "mrerp-connect"));
              }}
            />
          </div>
          <div className="cc-dialog-foot">
            {error && <span role="alert" className="me-auto text-danger text-body">{error}</span>}
            <DialogPrimitive.Close asChild>
              <button type="button" className="cc-btn">{t("cancel")}</button>
            </DialogPrimitive.Close>
            <button type="button" className="cc-btn" data-primary="" disabled={busy || !url} onClick={() => void submit()}>
              {st.connect}
            </button>
          </div>
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
};
