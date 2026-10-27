import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import { App } from "./App";

// Answers fetch from a table of recorded API responses, keyed by path and query.
export function renderApp(path: string, responses: Record<string, unknown>) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) =>
      url in responses
        ? new Response(JSON.stringify(responses[url]), { status: 200 })
        : new Response("not found", { status: 404, statusText: "Not Found" }),
    ),
  );
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <App />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}
