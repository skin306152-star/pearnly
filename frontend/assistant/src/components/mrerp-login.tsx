// MR.ERP 连接的登录（ADR-026 第 7 条，评估 §14.4，2026-11-02；11-23 用户「第一个弹窗不行吗，非要再设计一个单独的输入框干啥」）。
//
// 「连接 MR.ERP」弹窗（mrerp-connect.tsx）里直接输用户名和密码，点「连接」一步完成：
//   1. 本页向站点要一张一次性票（Cowork：companion.api.mrerp_login_ticket；ERP：pearnly_core.cc_desktop.api.mrerp_login_ticket，
//      按产品的通道选），票绑住这个人、这条连接和这台电脑（带本机设备密钥，同登记电脑那一个）；
//   2. 票与用户名、密码交给本机桥（lib/desktop.ts 的 authorizeMrErp），CC 桌面版经机器通道交给站点，站点收下了再存这台电脑的
//      凭据管理器。密码只走本机桥这一条：不经网络请求、不存（state 交完即清）、不进错误上报和日志。
// 谁能写站点（主会话 11-02 定，服务端判）：管理员的授权写进连接（直写用），「已授权」那行跟着变；连接使用名单上的成员只存自己这台
// 电脑（给自己代录用），站点那行不变，下面多一行同样写法的「已授权：用户名 · 这台电脑 · 时间」。
// 浏览器、手机里两格不可用（没有本机桥，同文件夹授权「只在 CC 里」）；没有 mrerp_login_inline 能力的旧桌面版提示更新。
// Cowork 与 ERP 用同一个组件。
import type { FC } from "react";
import { st } from "@/i18n";
import { authorizeMrErp, channelOf, deviceKey, type MrErpLoginResult } from "@/lib/desktop";
import { postMethod } from "@/lib/frappe";
import { formatWhen } from "@/lib/when";

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

/** 要一张一次性票，连同用户名、密码交给本机桥授权这条连接。票要不到、浏览器里（没有桥）回 null。网络请求里只有连接号与设备密钥。 */
export async function authorizeConnection(
  product: string | undefined,
  connection: string,
  username: string,
  password: string,
): Promise<MrErpLoginResult | null> {
  const key = await deviceKey();
  const issued = await postMethod<{ ok: boolean; ticket?: string }>(TICKET[channelOf(product)]!, { connection, device_key: key });
  if (!issued?.ok || !issued.ticket) return null;
  return authorizeMrErp(issued.ticket, username, password);
}

// map: mrerp-auth@cowork
/** 站点那行「已授权 / 未授权」与（使用成员）本机这次的「已授权」。 */
export const MrErpLoginStatus: FC<{ credential?: MrErpCredential; local?: MrErpCredential }> = ({ credential, local }) => {
  const detail = authorizedDetail(credential);
  const localDetail = authorizedDetail(local);
  return (
    <div className="flex flex-col items-start gap-(--cc-space-8)" data-slot="cc-mrerp-login">
      <span className={detail ? "text-body" : "text-ink-3 text-body"}>{detail ? st.mrerpAuthorized(detail) : st.mrerpNotAuthorized}</span>
      {localDetail && <span className="text-body" data-slot="cc-mrerp-login-local">{st.mrerpAuthorized(localDetail)}</span>}
    </div>
  );
};
