export class ApiError extends Error {
  status: number;
  code?: string;
  constructor(message: string, status: number, code?: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}
export async function api<T = any>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const headers = new Headers(options.headers);
  if (options.body && !(options.body instanceof FormData))
    headers.set("Content-Type", "application/json");
  const response = await fetch(`/api${path}`, {
    ...options,
    headers,
    credentials: "same-origin",
  });
  if (!response.ok) {
    const data = await response
      .json()
      .catch(() => ({ detail: response.statusText }));
    const detail = data.detail;
    const message =
      typeof detail === "string"
        ? detail
        : Array.isArray(detail)
          ? detail
              .map((x) => `${x.loc?.slice(1).join(".") || "参数"}：${x.msg}`)
              .join("；")
          : detail?.message || `请求失败（${response.status}）`;
    throw new ApiError(message, response.status, detail?.code);
  }
  return response.status === 204 ? (undefined as T) : response.json();
}
export const post = (path: string, body: unknown = {}) =>
  api(path, { method: "POST", body: JSON.stringify(body) });
export const put = (path: string, body: unknown = {}) =>
  api(path, { method: "PUT", body: JSON.stringify(body) });
export const remove = (path: string) => api(path, { method: "DELETE" });
export const idPath = (kind: string, id: string) =>
  `/v2/${kind}/${encodeURIComponent(id)}`;

export async function readAssetContent(id: string) {
  const response = await fetch(`/api${idPath("data-assets", id)}/content`, {
    credentials: "same-origin",
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new ApiError(
      body.detail?.message || "数据内容无法读取",
      response.status,
      body.detail?.code,
    );
  }
  const mediaType = response.headers.get("content-type") || "";
  if (mediaType.includes("application/json")) return response.json();
  if (mediaType.startsWith("text/")) return response.text();
  return { binary: true, media_type: mediaType };
}
