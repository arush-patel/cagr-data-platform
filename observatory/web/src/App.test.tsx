import { screen } from "@testing-library/react";

import { renderApp } from "./test-utils";

test("the home page opens Review", async () => {
  renderApp("/", {
    "/api/health": { version: "0.1.0", database: true, detail: null, reviewer: "arush" },
    "/api/review/items?status=pending": [],
  });
  expect(await screen.findByText("Database connected")).toBeInTheDocument();
  expect(await screen.findAllByText("Nothing is waiting for review.")).not.toHaveLength(0);
});

test("says when the database can't be reached", async () => {
  renderApp("/review", {
    "/api/health": { version: "0.1.0", database: false, detail: "OperationalError", reviewer: null },
  });
  expect(await screen.findByText("Database unreachable: OperationalError")).toBeInTheDocument();
  expect(await screen.findByText(/The queue couldn't be loaded/)).toBeInTheDocument();
});
