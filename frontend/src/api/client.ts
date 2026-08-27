// fetch 薄封装 + 结构化错误。
//
// 后端两种错误形状都要认（复刻原型的 errMsg）：
// - HTTPException（load_project 的 404 等）→ 详情在 `.detail`。
// - 自定义 exception handler（§1.3 那张表：unknown_name / ambiguous_name / …）→ 详情在顶层。
// ApiError 把两者归一成一个带 `error` 码 + `message` + 可选 `candidates` 的对象，
// 让 UI 能按 error 码分支（歧义弹选择器、找不到引语提示加长）。

export interface ApiErrorBody {
  error?: string;
  /** 码的插值参数（国际化第四批）。后端不再算最终句子，`error` 是码、
   *  `params` 是填模板用的原始事实——组句在前端 `backendMessages.ts` 里发生。
   *  这一位是**过渡期**才有的双轨兼容：`message` 还没从旧端点删干净时，
   *  两个字段可能同时存在，读端一律优先 `error` + `params`。 */
  params?: Record<string, unknown>;
  /** @deprecated 国际化第四批之前后端算好的最终句子。正在从各端点逐个撤下——
   *  见 `error`/`params` 那两行。仍然存在的端点，读端照旧当唯一真话使用。 */
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
  // PUT 是「整个换成这份」，PATCH 是「只改这一处」。章节总结走后者：那条路由收的是
  // 一段正文，而这一章的总结在库里还挂着来源、状态和它取代的那几行（迁移 013）。
  patch: <T>(path: string, body: unknown) =>
    request<T>(path, { method: "PATCH", body: JSON.stringify(body) }),
  del: <T>(path: string) => request<T>(path, { method: "DELETE" }),
};

export const proj = (pid: string, tail = "") => `/api/projects/${encodeURIComponent(pid)}${tail}`;

/** 读一个 TXT 文件成文本。中文老稿常是 GBK：先按 UTF-8 解，出现替换符 � 就改判 GBK。
 *  编码难题交给浏览器（后端只收解好的文本），避开服务端 chardet 那一坨。 */
export async function readTextFile(file: File): Promise<string> {
  const buf = await file.arrayBuffer();
  const utf8 = new TextDecoder("utf-8").decode(buf);
  if (!utf8.includes("�")) return utf8;
  try {
    return new TextDecoder("gbk").decode(buf);
  } catch {
    return utf8; // 浏览器不支持 gbk 解码器时退回 UTF-8（至少不炸）
  }
}
