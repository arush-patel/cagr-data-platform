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
| Review page: a type filter, sort and search over the queue; a view and a guide per kind of item | Built |
| Approve and reject on the page, recorded at once through the platform's review module | Built |

## The Review page

It lists what spec 0002's rules couldn't decide: rows of `ref.price_adjustment` and
`ref.listing_change` with `status = 'pending'`. History lists the ones a person decided.

**The layout.** The queue is on the left and the open item on the right. Each side scrolls on its
own, so scrolling a long queue leaves the item where it is. Above the queue are a **type** filter
(each type with its count), a **sort** (newest or oldest date first, waiting longest, symbol, or
for History the latest decision) and a **symbol or name** search. All of these live in the URL,
so a link opens the same view. The queue draws 200 rows at a time.

### The kinds of item

The rules can raise only these, so the same kinds come back every night. Each item carries its
`category`, which the type filter uses. While an item waits, its category comes from the note the
rules left (`cagr_data.reference.adjustments.decide`). A decision replaces that note with the
reviewer's, so a decided item's category is worked out from its data instead.

| Category | Type in the filter | Raised when |
|---|---|---|
| `unexplained_jump` | Jump nothing explains | The close moved below ×0.75 or above ×1.33 and NSE announced nothing |
| `clean_ratio_jump` | Jump near a split ratio | The same, but within 5% of a clean ratio (1/2, 1/10, 5 and so on) |
| `action_disagrees` | Close disagrees with announcement | A split, bonus or consolidation predicts a factor the close misses by more than 20% |
| `missing_ratio` | Announcement lacks a ratio | A split, bonus, consolidation or rights issue with no ratio read from it |
| `unparsed` | Unreadable announcement | An announcement on the date couldn't be parsed |
| `demerger` | Demerger | Always: the factor can only come from prices |
| `dividend_too_big` | Dividend at or above the close | A dividend as large as the previous close, most likely misread |
| `possible_merge` | Possible merge | A symbol came back with a new ISIN after more than 20 silent sessions (rule 5.2.2) |
| `possible_rename` | Possible index rename | A new index name appeared as others stopped (rule 5.5) |

### What an item shows

Every item shows, top to bottom:

1. Its type, its title, and one sentence on why the rules stopped.
2. The numbers for its kind. An adjustment shows the move on the ex-date, the previous trade
   and how many sessions passed without one, the ISIN (flagged when it changed), NSE's
   announcement, and the factor approving applies. A listing change shows the listings involved,
   the payload and the evidence.
3. **How to decide** an item of this kind, in a sentence or two.
4. One chart.
5. What approve and reject each write, then the buttons.

The words come from `cagr_observatory/review/reasons.py`, from what spec 0002 stored. Nothing is
worked out again.

**The chart** (`components/LineChart.tsx`) is drawn in SVG on a log scale, so a tenfold and a
halving read alike. Its x axis counts the sessions with a bar, not calendar days, so a long halt
doesn't squash it; a note on the chart says how long the halt was. Hovering shows each line's
value.

| Item | The chart shows |
|---|---|
| Price adjustment | The listing's own last 40 closes before the ex-date and first 40 from it (`queries.closes_around`, from spec 0003's `clean.bar_1d_unadjusted`). The dashed line is the price as traded; the solid line is the history after approving. A right factor leaves no step at the ex-date |
| Possible merge or rename | Each listing's closes, 40 traded sessions either side of the date. A rename or a re-listed security carries on at the level the old one stopped at |

An item waiting more than 3 sessions is marked, as the `review_waiting` check warns. Keys `j`
and `k` move through the queue as sorted and filtered, `a` approves and `r` rejects. A rejection
needs a note saying why: Reject (the button or `r`) with an empty note moves to the note box and
asks for one. After a decision the next item opens, with the decision just made named at its
top, so a wrong button is noticed at once. The buttons and keys wait 0.8 s after an item opens,
and a held key doesn't repeat, so a double click can't decide the next item too.

### API

| API | Returns |
|---|---|
| `GET /api/review/items?status=pending` (or `decided`) | The queue: category, title, the one-sentence reason, sessions waiting |
| `GET /api/review/items/adjustment/{id}` | An adjustment's factors, closes, ISINs, previous trade, NSE's announcement, the guide and effects, and the chart's closes |
| `GET /api/review/items/change/{id}` | A listing change's payload and evidence, the listings involved and each one's closes, the guide and effects |
| `GET /api/listings/search?q=` | Listings by symbol, ISIN or name |
| `POST /api/review/items/{kind}/{id}/approve` | `{note, into_id}`: approve; `into_id` only for a possible index rename |
| `POST /api/review/items/{kind}/{id}/reject` | `{note}`, required: reject |

### Deciding

Approve and reject call `cagr_data.reference.review`, the same functions as the command line, in
one transaction on the `cagr_reviewer` login (migration `0008_reviewer`). That login can only
record a decision and carry out an approved merge. The decision is recorded as `CAGR_REVIEWER`.
Without `CAGR_REVIEWER` and `CAGR_REVIEW_DATABASE_URL`, the page only reads. A refusal, such as
deciding an item you proposed (D16) or one already decided, is shown in words.

| Item | Approve does |
|---|---|
| Price adjustment | Applies it with its factor (the proposed one, else the announced one, else the observed move) |
| Possible merge | Merges the new listing into the one it may be, moving its symbol and ISIN periods |
| Possible index rename | Merges the new index name into the old index you pick |

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
