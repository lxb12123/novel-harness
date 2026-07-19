// fetch 薄封装 + 结构化错误。
//
// 后端两种错误形状都要认（复刻原型的 errMsg）：
// - HTTPException（load_project 的 404 等）→ 详情在 `.detail`。
// - 自定义 exception handler（§1.3 那张表：unknown_name / ambiguous_name / …）→ 详情在顶层。
// ApiError 把两者归一成一个带 `error` 码 + `message` + 可选 `candidates` 的对象，
// 让 UI 能按 error 码分支（歧义弹选择器、找不到引语提示加长）。

export interface ApiErrorBody {
  error?: string;
  message?: string;
  surface?: string;
  quote?: string;
  got?: string;
  want?: string;
  path?: string | null;
  candidates?: unknown[];
  [k: string]: unknown;
}

export class ApiError extends Error {
  status: number;
  body: ApiErrorBody;
  constructor(status: number, body: ApiErrorBody) {
    super(body.message || body.error || `HTTP ${status}`);
    this.status = status;
    this.body = body;
  }
  get code(): string {
    return this.body.error || `http_${this.status}`;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
  });
  const raw = await res.json().catch(() => null);
  if (!res.ok) {
    // FastAPI 把 HTTPException 详情裹进 .detail；自定义处理器放顶层。两种都拆。
    const detail = (raw && (raw.detail ?? raw)) || {};
    throw new ApiError(res.status, typeof detail === "object" ? detail : { message: String(detail) });
  }
  return raw as T;
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) }),
  put: <T>(path: string, body: unknown) =>
    request<T>(path, { method: "PUT", body: JSON.stringify(body) }),
};

export const proj = (pid: string, tail = "") => `/api/projects/${encodeURIComponent(pid)}${tail}`;
