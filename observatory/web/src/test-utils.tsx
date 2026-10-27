import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import { App } from "./App";

// Answers fetch from a table of recorded API responses, keyed by path and query, with
// "POST " in front for a post. A response made with refused() is sent with status 409.
export function refused(detail: string) {
  return { __status: 409, detail };
}

export function renderApp(path: string, responses: Record<string, unknown>) {
  const fetch = vi.fn(async (url: string, init?: RequestInit) => {
    const key = init?.method === "POST" ? `POST ${url}` : url;
    if (!(key in responses)) return new Response("not found", { status: 404, statusText: "Not Found" });
    const body = responses[key] as { __status?: number };
    return new Response(JSON.stringify(body), { status: body?.__status ?? 200 });
  });
  vi.stubGlobal("fetch", fetch);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <App />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return fetch;
}
