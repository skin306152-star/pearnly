// MR.ERP 连接的登录（ADR-026 第 7 条，评估 §14.4，2026-11-02）：网页不再收 MR.ERP 密码。
//
// 只显示「已授权：用户名 · 电脑 · 时间」（服务端连接视图的 credential，老连接只有用户名）和「在 CC 桌面版中授权」：
//   1. 本页向站点要一张一次性票（Cowork：companion.api.mrerp_login_ticket；ERP：pearnly_core.cc_desktop.api.mrerp_login_ticket，
//      按产品的通道选），票绑住这个人、这条连接和这台电脑（带本机设备密钥，同登记电脑那一个）；
//   2. 票交给桥（lib/desktop.ts 的 authorizeMrErp），CC 桌面版弹原生小窗收用户名和密码，经机器通道交给站点，站点收下了再存
//      这台电脑的凭据管理器。页面从头到尾拿不到密码。
// 谁能写站点（主会话 11-02 定，服务端判）：管理员的授权写进连接（直写用），上面那行跟着变；连接使用名单上的成员只存自己这台电脑
// （给自己代录用），站点那行不变，下面多一行同样写法的「已授权：用户名 · 这台电脑 · 时间」。
// 浏览器、手机里按钮不可用（同文件夹授权「只在 CC 里」）；没有 mrerp_login 能力的旧桌面版提示更新。Cowork 与 ERP 用同一个组件。
import { useState, type FC } from "react";
import { LoaderIcon } from "lucide-react";
import { co, st } from "@/i18n";
import { authorizeMrErp, channelOf, deviceKey, openSettings, useDesktopFeatures } from "@/lib/desktop";
import { postMethod } from "@/lib/frappe";
import { formatWhen } from "@/lib/when";
import { reportCaught } from "@/lib/caught";

export type MrErpCredential = { username?: string; device?: string; at?: string } | null;

const TICKET: Record<string, string> = {
  cowork: "pearnly_cowork.companion.api.mrerp_login_ticket",
  core: "pearnly_core.cc_desktop.api.mrerp_login_ticket",
};

/** 「已授权：用户名 · 电脑 · 时间」里的那一串；没有用户名就是未授权（空串）。 */
export function authorizedDetail(credential: MrErpCredential | undefined): string {
  if (!credential?.username) return "";
  return [credential.username, credential.device, formatWhen(credential.at)].filter(Boolean).join(" · ");
}

// map: mrerp-auth@cowork
export const MrErpLogin: FC<{
  product?: string;
  connection: { name: string; credential?: MrErpCredential } | null;
  /** 还没有连接时先建一条（回连接号）；不给就要求先有连接 */
  ensure?: () => Promise<string | null>;
  onDone: () => void;
}> = ({ product, connection, ensure, onDone }) => {
  const { app, mrerpLogin } = useDesktopFeatures();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  // 使用成员只存了这台电脑：站点那行不变，这里记着本机这次的
  const [local, setLocal] = useState<MrErpCredential>(null);
  const detail = authorizedDetail(connection?.credential);
  const localDetail = authorizedDetail(local);
  const authorize = async () => {
    setError("");
    setBusy(true);
    try {
      const name = connection?.name ?? (ensure ? await ensure() : null);
      if (!name) return;
      const key = await deviceKey();
      const issued = await postMethod<{ ok: boolean; ticket?: string }>(TICKET[channelOf(product)]!, { connection: name, device_key: key });
      if (!issued?.ok || !issued.ticket) return setError(st.mrerpAuthorizeFailed);
      const result = await authorizeMrErp(issued.ticket);
      if (!result || (!result.ok && result.cancelled)) return;
      if (!result.ok) return setError(st.mrerpAuthorizeFailed);
      if (!result.written) return setLocal(result.authorized);
      onDone();
    } catch (e) {
      reportCaught(e, "mrerp-login");
      setError(st.mrerpAuthorizeFailed);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="flex flex-col items-start gap-(--cc-space-8)" data-slot="cc-mrerp-login">
      <span className={detail ? "text-body" : "text-ink-3 text-body"}>{detail ? st.mrerpAuthorized(detail) : st.mrerpNotAuthorized}</span>
      {localDetail && <span className="text-body" data-slot="cc-mrerp-login-local">{st.mrerpAuthorized(localDetail)}</span>}
      {app && !mrerpLogin ? (
        <button type="button" className="cc-btn" onClick={() => openSettings("system")}>
          {co.updateDesktop}
        </button>
      ) : (
        <button type="button" className="cc-btn" data-slot="cc-mrerp-authorize" disabled={!app || busy} onClick={() => void authorize()}>
          {busy && <LoaderIcon className="cc-spinner" />}
          {st.mrerpAuthorize}
        </button>
      )}
      {error && <span role="alert" className="text-danger text-body">{error}</span>}
    </div>
  );
};
