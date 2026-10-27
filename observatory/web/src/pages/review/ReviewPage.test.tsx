import { fireEvent, screen } from "@testing-library/react";

import type { AdjustmentDetail, ChangeDetail, Item } from "../../api/client";
import { refused, renderApp } from "../../test-utils";

// The chart draws on a canvas, which jsdom doesn't have.
vi.mock("./PriceChart", () => ({ PriceChart: () => <div>chart</div> }));

const base = {
  proposed_by: "rules",
  decided_by: null,
  decided_at: null,
  note: null,
  status: "pending",
} as const;

const jump: Item = {
  ...base,
  kind: "adjustment",
  id: 7,
  listing_id: 1,
  symbol: "KAUSH",
  name: "Kaushalya Ltd",
  date: "2024-02-06",
  title: "Unexplained jump ×99.64",
  reason: "The close moved ×99.64 and no NSE announcement explains it.",
  waiting_sessions: 5,
  overdue: true,
};

const merge: Item = {
  ...base,
  kind: "change",
  id: 3,
  listing_id: 9,
  symbol: "NEWCO",
  name: null,
  date: "2024-03-01",
  title: "Possible merge",
  reason: "NEWCO appeared on 01 Mar 2024 as a new listing. It may be the same security as OLDCO.",
  waiting_sessions: 1,
  overdue: false,
};

const jumpDetail: AdjustmentDetail = {
  ...jump,
  factor: 100,
  expected_factor: null,
  observed_factor: 99.64,
  chart_factor: 100,
  close_before: 0.42,
  close_on: 41.85,
  isin_before: "INE000K01011",
  isin_on: "INE000K01029",
  action: null,
  bars: [{ date: "2024-02-05", close: 0.42, adjusted: 42 }],
};

const mergeDetail: ChangeDetail = {
  ...merge,
  change_kind: "merge",
  payload: { merged_id: 9, into_id: 2 },
  evidence: { rule: "5.2.2", candidates: [2] },
  listings: [
    {
      listing_id: 9,
      symbol: "NEWCO",
      isin: "INE000B01011",
      name: null,
      asset_class: "equity",
      board: "main",
      status: "active",
      first_session: "2024-03-01",
      last_session: "2024-03-29",
    },
    {
      listing_id: 2,
      symbol: "OLDCO",
      isin: "INE000A01011",
      name: null,
      asset_class: "equity",
      board: "main",
      status: "active",
      first_session: "2024-01-01",
      last_session: "2024-01-31",
    },
  ],
};

const responses = {
  "/api/health": { version: "0.1.0", database: true, detail: null, reviewer: "arush" },
  "/api/review/items?status=pending": [jump, merge],
  "/api/review/items/adjustment/7": jumpDetail,
  "/api/review/items/change/3": mergeDetail,
};

test("the queue groups items and marks the overdue ones", async () => {
  renderApp("/review", responses);
  expect(await screen.findByText("Price adjustments (1)")).toBeInTheDocument();
  expect(screen.getByText("Listing changes (1)")).toBeInTheDocument();
  expect(screen.getByText("5 sessions")).toHaveClass("overdue");
  expect(screen.getByText("1 session")).not.toHaveClass("overdue");
});

test("an adjustment shows its reason, numbers and chart", async () => {
  renderApp("/review", responses);
  fireEvent.click(await screen.findByRole("button", { name: /KAUSH/ }));
  expect(await screen.findByText(jump.reason)).toBeInTheDocument();
  expect(screen.getByText("INE000K01011 → INE000K01029")).toBeInTheDocument();
  expect(screen.getByText(/Waiting longer than 3 sessions/)).toBeInTheDocument();
  expect(screen.getByText("chart")).toBeInTheDocument();
});

test("a possible merge shows both listings", async () => {
  renderApp("/review/change/3", responses);
  expect(await screen.findByText(merge.reason)).toBeInTheDocument();
  expect(screen.getByRole("cell", { name: "OLDCO" })).toBeInTheDocument();
  expect(screen.getByText("OLDCO (listing 2)")).toBeInTheDocument();
});

test("j moves to the next item", async () => {
  renderApp("/review/adjustment/7", responses);
  await screen.findByText(jump.reason);
  fireEvent.keyDown(window, { key: "j" });
  expect(await screen.findByText(merge.reason)).toBeInTheDocument();
});

test("approve records the decision and opens the next item", async () => {
  const fetch = renderApp("/review/adjustment/7", {
    ...responses,
    "POST /api/review/items/adjustment/7/approve": { ...jump, status: "applied", decided_by: "arush" },
  });
  fireEvent.click(await screen.findByRole("button", { name: "Approve factor 100" }));
  expect(await screen.findByText(merge.reason)).toBeInTheDocument();
  const post = fetch.mock.calls.find(([, init]) => init?.method === "POST");
  expect(JSON.parse(String(post?.[1]?.body))).toEqual({ note: null, into_id: null });
});

test("reject needs a note, and a refusal is shown in words", async () => {
  renderApp("/review/adjustment/7", {
    ...responses,
    "POST /api/review/items/adjustment/7/reject": refused("adjustment 7 is already applied"),
  });
  const rejectButton = await screen.findByRole("button", { name: "Reject" });
  expect(rejectButton).toBeDisabled();
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "a series move" } });
  fireEvent.click(rejectButton);
  expect(await screen.findByText("adjustment 7 is already applied")).toBeInTheDocument();
});

test("a merge names the listing it merges into", async () => {
  renderApp("/review/change/3", responses);
  expect(await screen.findByRole("button", { name: "Approve: merge into OLDCO" })).toBeEnabled();
});

test("without a reviewer the page only reads", async () => {
  renderApp("/review/adjustment/7", {
    ...responses,
    "/api/health": { version: "0.1.0", database: true, detail: null, reviewer: null },
  });
  expect(await screen.findByText(/This Observatory only reads/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Reject" })).toBeNull();
});
