// 与站点后端说话的唯一入口。
//
// 关键约定（照 Frappe 的实际行为写，不是照文档猜）：
// - 白名单方法都在 `/api/method/<点分路径>`；会话靠站点 cookie，前后端同源，不传 token。
// - 写操作要带 CSRF。Frappe 校验的是 `X-Frappe-CSRF-Token` 请求头与**会话里存的那个值**
//   是否相等（`frappe/auth.py:81` 的 validate_csrf_token）。会话里的值前端看不到，
//   所以由站点自己的启动接口下发（见 shell.ts），不从 cookie 里找 —— Frappe 并不下发
//   csrf cookie，去找只会拿到 undefined。

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly excType?: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

let csrfToken = "";

/** 启动接口拿到 csrf 之后由 shell.ts 交给这里；写操作会自动带上。 */
export function setCsrfToken(token: string): void {
  csrfToken = token || "";
}

type CallOptions = {
  /** 默认按参数是否为空猜；写操作显式给 POST 更稳。 */
  method?: "GET" | "POST";
  signal?: AbortSignal;
};

export async function call<T>(
  path: string,
  params: Record<string, unknown> = {},
  options: CallOptions = {},
): Promise<T> {
  const method = options.method ?? "GET";
  const url = new URL(`/api/method/${path}`, window.location.origin);
  const headers: Record<string, string> = { Accept: "application/json" };

  let body: string | undefined;
  if (method === "GET") {
    for (const [key, value] of Object.entries(params)) {
      if (value !== undefined && value !== null && value !== "") {
        url.searchParams.set(key, String(value));
      }
    }
  } else {
    headers["Content-Type"] = "application/json";
    if (csrfToken) headers["X-Frappe-CSRF-Token"] = csrfToken;
    body = JSON.stringify(params);
  }

  const response = await fetch(url, { method, headers, body, signal: options.signal });
  const text = await response.text();
  let payload: unknown = null;
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      payload = text;
    }
  }

  if (!response.ok) {
    // Frappe 把真实异常藏在 `_server_messages`（一段字符串化的 JSON）里；
    // 不挖出来，界面上就只能看到一句没用的 "Internal Server Error"。
    throw new ApiError(extractMessage(payload) || response.statusText, response.status, extractExcType(payload));
  }

  // 白名单方法把返回值放在 `message` 下
  if (payload && typeof payload === "object" && "message" in payload) {
    return (payload as { message: T }).message;
  }
  return payload as T;
}

function extractExcType(payload: unknown): string | undefined {
  if (payload && typeof payload === "object" && "_exc_type" in payload) {
    return String((payload as Record<string, unknown>)._exc_type);
  }
  return undefined;
}

function extractMessage(payload: unknown): string {
  if (!payload || typeof payload !== "object") return "";
  const bag = payload as Record<string, unknown>;
  if (bag._server_messages) {
    try {
      const messages = JSON.parse(String(bag._server_messages)) as string[];
      const first = messages?.[0];
      if (first) {
        const parsed = JSON.parse(first) as { message?: string };
        if (parsed.message) return stripHtml(parsed.message);
      }
    } catch {
      /* 拿不到就退回下面的通用文案 */
    }
  }
  if (typeof bag.message === "string" && bag.message) return stripHtml(bag.message);
  if (typeof bag.exception === "string" && bag.exception) return bag.exception.split("\n")[0] ?? "";
  return "";
}

function stripHtml(html: string): string {
  const div = document.createElement("div");
  div.innerHTML = html;
  return (div.textContent || "").trim();
}
