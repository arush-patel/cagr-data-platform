# Observatory

**In short:** the platform's web app. A Python API (`observatory/api`, package `cagr_observatory`)
serves JSON under `/api` and the React UI (`observatory/web`) everywhere else. It runs on the
laptop, on `localhost` only. The plan is [spec 0005](../specs/0005-observatory-review-page.md).

## What is built

| Part | Status |
|---|---|
| App shell, with a link to each page and a sign of whether the database is reachable | Built |
| `GET /api/health`: the version, and whether the read login (`CAGR_READ_DATABASE_URL`) connects | Built |
| Review page: the queue (waiting and history), each item's reason, numbers and chart | Built (reads only) |
| Approve, reject and merge on the page | Next (slice O3) |

## The Review page

It lists what spec 0002's rules couldn't decide: rows of `ref.price_adjustment` and
`ref.listing_change` with `status = 'pending'`. History lists the ones a person decided.

| API | Returns |
|---|---|
| `GET /api/review/items?status=pending` (or `decided`) | The queue: title, the one-sentence reason, sessions waiting |
| `GET /api/review/items/adjustment/{id}` | An adjustment's factors, closes, ISINs, NSE's announcement and the chart's closes |
| `GET /api/review/items/change/{id}` | A listing change's payload and evidence, and the listings involved |
| `GET /api/listings/search?q=` | Listings by symbol, ISIN or name |

- **The reason sentence** is written in `cagr_observatory/review/reasons.py`, from what spec 0002
  stored. Nothing is worked out again.
- **The chart** shows closes 20 sessions either side of the ex-date, on a log scale, unadjusted
  (dashed) and adjusted by the proposed factor (solid). Until spec 0003's
  `clean.bar_1d_unadjusted` exists, the closes come from the raw bhavcopy through the listing's
  dated symbols, in its listing series and never T0. That is one function,
  `queries.closes_around`.
- An item waiting more than 3 sessions is marked, as the `review_waiting` check warns.
- Keys `j` and `k` move through the queue.

## Running it

```sh
cd observatory/web && npm ci && npm run build && cd -   # build the UI once
uv run observatory                                     # API and UI on http://localhost:8400
```

While working on the UI, run Vite as well. It reloads on every change and sends `/api` to the
API above:

```sh
cd observatory/web && npm run dev                      # http://localhost:5173
```

| Setting | Default | Meaning |
|---|---|---|
| `CAGR_READ_DATABASE_URL` | none | The read-only login the API reads through (the toolkit's `cagr.db`) |
| `CAGR_OBSERVATORY_PORT` | `8400` | The port |
| `CAGR_OBSERVATORY_HOST` | `127.0.0.1` | Keep it on `localhost` until sign-in exists |

## Checks

The Python side is covered by the repository's usual four commands. The UI has its own, and CI
runs them in the `observatory-web` job:

```sh
cd observatory/web
npm run typecheck && npm run lint && npm test && npm run build
```

## Changing the API

The UI's types are generated from the API, so they can't drift. After changing a response:

```sh
uv run python -m cagr_observatory.openapi          # writes observatory/web/src/api/openapi.json
cd observatory/web && npm run api-types            # writes src/api/schema.d.ts
```

A test fails if `openapi.json` is out of date, and CI fails if `schema.d.ts` is.

## Rules

- The UI only calls the API. It never talks to the database.
- The API reads through the toolkit. The toolkit never imports the Observatory (a test checks it).
