import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import { App } from "./App";

function renderAt(path: string, health: unknown) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(JSON.stringify(health), { status: 200 })),
  );
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <App />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

test("the home page opens Review", async () => {
  renderAt("/", { version: "0.1.0", database: true, detail: null });
  expect(screen.getByRole("heading", { name: "Waiting" })).toBeInTheDocument();
  expect(await screen.findByText("Database connected")).toBeInTheDocument();
});

test("says when the database can't be reached", async () => {
  renderAt("/review", { version: "0.1.0", database: false, detail: "OperationalError" });
  expect(await screen.findByText("Database unreachable")).toBeInTheDocument();
});
