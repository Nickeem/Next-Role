import { vi } from "vitest";

type Handler = (url: URL, init: RequestInit) => { status?: number; body?: unknown };

/** Replace fetch with a router over path + method. Records every call. */
export function mockFetch(routes: Record<string, Handler>) {
  const calls: { method: string; path: string; url: string; init: RequestInit }[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = new URL(String(input), "https://app.test");
    const method = init.method ?? "GET";
    calls.push({ method, path: url.pathname, url: url.href, init });
    const handler = routes[`${method} ${url.pathname}`];
    if (!handler) return new Response(JSON.stringify({ detail: "no mock" }), { status: 404 });
    const { status = 200, body = {} } = handler(url, init);
    return new Response(status === 204 ? null : JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json" },
    });
  });
  vi.stubGlobal("fetch", fetchMock);
  return calls;
}
