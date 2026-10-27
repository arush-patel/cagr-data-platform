# Observatory

**In short:** the platform's web app. A Python API (`observatory/api`, package `cagr_observatory`)
serves JSON under `/api` and the React UI (`observatory/web`) everywhere else. It runs on the
laptop, on `localhost` only. The plan is [spec 0005](../specs/0005-observatory-review-page.md).

## What is built

| Part | Status |
|---|---|
| App shell, with a link to each page and a sign of whether the database is reachable | Built |
| `GET /api/health`: the version, and whether the read login (`CAGR_READ_DATABASE_URL`) connects | Built |
| Review page: the queue (waiting and history), each item's reason, numbers and chart | Built |
| Approve and reject on the page, recorded at once through the platform's review module | Built |

## The Review page

It lists what spec 0002's rules couldn't decide: rows of `ref.price_adjustment` and
`ref.listing_change` with `status = 'pending'`. History lists the ones a person decided.

| API | Returns |
|---|---|
| `GET /api/review/items?status=pending` (or `decided`) | The queue: title, the one-sentence reason, sessions waiting |
| `GET /api/review/items/adjustment/{id}` | An adjustment's factors, closes, ISINs, NSE's announcement and the chart's closes |
| `GET /api/review/items/change/{id}` | A listing change's payload and evidence, and the listings involved |
| `GET /api/listings/search?q=` | Listings by symbol, ISIN or name |
| `POST /api/review/items/{kind}/{id}/approve` | `{note, into_id}`: approve; `into_id` only for a possible index rename |
| `POST /api/review/items/{kind}/{id}/reject` | `{note}`, required: reject |

**Deciding.** Approve and reject call `cagr_data.reference.review`, the same functions as the
command line, in one transaction on the `cagr_reviewer` login (migration `0008_reviewer`). That
login can only record a decision and carry out an approved merge. The decision is recorded as
`CAGR_REVIEWER`. Without `CAGR_REVIEWER` and `CAGR_REVIEW_DATABASE_URL`, the page only reads. A
refusal, such as deciding an item you proposed (D16) or one already decided, is shown in words.

| Item | Approve does |
|---|---|
| Price adjustment | Applies it with its factor (the proposed one, else expected, else observed) |
| Possible merge | Merges the new listing into the one it may be, moving its symbol and ISIN periods |
| Possible index rename | Merges the new index name into the old index you pick |

- **The reason sentence** is written in `cagr_observatory/review/reasons.py`, from what spec 0002
  stored. Nothing is worked out again.
- **The chart** shows closes 20 sessions either side of the ex-date, on a log scale, unadjusted
  (dashed) and adjusted by the proposed factor (solid). The closes still come from the raw
  bhavcopy through the listing's dated symbols, in its listing series and never T0, not yet from
  `clean.bar_1d_unadjusted`. Switching is one function, `queries.closes_around`.
- An item waiting more than 3 sessions is marked, as the `review_waiting` check warns.
- Keys `j` and `k` move through the queue, `a` approves and `r` rejects (or moves to the note,
  which a rejection needs). After a decision the next item opens.

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

Settings come from the environment or the repository's `.env`, which the server reads at start,
whichever folder it starts from. Restart it after changing them.

| Setting | Default | Meaning |
|---|---|---|
| `CAGR_READ_DATABASE_URL` | none | The read-only login the API reads through (the toolkit's `cagr.db`) |
| `CAGR_REVIEW_DATABASE_URL` | none | The reviewer's login (`cagr_reviewer`), for decisions |
| `CAGR_REVIEWER` | none | Your name, recorded on each decision |
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
