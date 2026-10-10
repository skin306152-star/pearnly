// 本站白名单方法的调用（站点会话登录；写操作带 www/assistant 注入的 CSRF 令牌）。
// 浏览器从不直接连模型厂商：引擎与密钥只在服务端。

const BASE = "/api/method/pearnly_core.assistant.api.";

declare global {
  interface Window {
    csrf_token?: string;
  }
}

function errorOf(body: unknown, status: number): string {
  const b = (body ?? {}) as { _server_messages?: string; exception?: string; message?: string };
  if (b._server_messages) {
    try {
      const first = JSON.parse(JSON.parse(b._server_messages)[0]) as { message?: string };
      if (first.message) return first.message.replace(/<[^>]+>/g, "");
    } catch {
      /* fall through */
    }
  }
  if (typeof b.exception === "string") return b.exception.split(":").slice(1).join(":").trim() || b.exception;
  return `request failed (${status})`;
}

async function parse<T>(res: Response): Promise<T> {
  const body = await res.json().catch(() => null);
  if (res.status === 401 || res.status === 403) {
    if (res.status === 401) location.href = "/api/method/pearnly_core.identity.begin_login?redirect_to=/assistant";
  }
  if (!res.ok) throw new Error(errorOf(body, res.status));
  return (body as { message: T }).message;
}

export async function get<T>(method: string, args: Record<string, string | number | undefined> = {}): Promise<T> {
  const query = new URLSearchParams();
  for (const [k, v] of Object.entries(args)) if (v !== undefined && v !== null) query.set(k, String(v));
  const res = await fetch(`${BASE}${method}?${query}`, { cache: "no-store", credentials: "same-origin" });
  return parse<T>(res);
}

export async function post<T>(method: string, args: Record<string, unknown> = {}): Promise<T> {
  const res = await fetch(`${BASE}${method}`, {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-Frappe-CSRF-Token": window.csrf_token ?? "" },
    body: JSON.stringify(args),
  });
  return parse<T>(res);
}

export async function upload<T>(method: string, args: Record<string, string>, file: File): Promise<T> {
  const form = new FormData();
  for (const [k, v] of Object.entries(args)) form.set(k, v);
  form.set("file", file, file.name);
  const res = await fetch(`${BASE}${method}`, {
    method: "POST",
    credentials: "same-origin",
    headers: { "X-Frappe-CSRF-Token": window.csrf_token ?? "" },
    body: form,
  });
  return parse<T>(res);
}
