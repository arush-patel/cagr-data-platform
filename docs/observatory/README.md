# Observatory

**In short:** the platform's web app. A Python API (`observatory/api`, package `cagr_observatory`)
serves JSON under `/api` and the React UI (`observatory/web`) everywhere else. It runs on the
laptop, on `localhost` only. The plan is [spec 0005](../specs/0005-observatory-review-page.md).

## What is built

| Part | Status |
|---|---|
| App shell, with a link to each page and a sign of whether the database is reachable | Built |
| `GET /api/health`: the version, and whether the read login (`CAGR_READ_DATABASE_URL`) connects | Built |
| Review page | Empty for now. The queue comes with spec 0002's review tables |

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

## Rules

- The UI only calls the API. It never talks to the database.
- The API reads through the toolkit. The toolkit never imports the Observatory (a test checks it).
