# GENOAR RAG — frontend

The web UI: dashboard, search, and sample detail. Next.js 16 (App Router) and
React, talking to the backend in `../backend` over `/api`.

**Start at [`../README.md`](../README.md).** It covers running the whole thing
under Docker Compose, which is how the deployments run and the shortest route to
a working page, and describes the pages and the API this calls. What follows is
only for working on this package on its own.

## Scripts

| Command | Does |
|---|---|
| `npm run dev` | Development server on port 3000 |
| `npm run build` | Production build |
| `npm start` | Serve a build made by `npm run build` |
| `npm run lint` | ESLint |
| `npm test` | Jest, over `src/__tests__` |
| `npm run verify:browser` | Drives a real browser over the history/back-button paths |

Running `npm run dev` on its own needs a backend to talk to: `next.config.mjs`
rewrites `/api` and `/health` to `API_URL`, which defaults to localhost. Set it
to wherever the backend is listening.

## Notes

`API_URL` and `NEXT_PUBLIC_DASHBOARD_DATASET` are read when the site is
**built**, not when it starts — Next writes rewrite destinations into the build
and `NEXT_PUBLIC_*` values into the bundle. Setting either on a running
container changes nothing. `Dockerfile` takes both as build arguments for that
reason; see the comments there.

Fonts are Inter and Roboto Mono, fetched from Google Fonts by `<link>` in
`src/app/layout.tsx`. Material Symbols comes from the same place with
`display=block`, which the comment beside it explains.
