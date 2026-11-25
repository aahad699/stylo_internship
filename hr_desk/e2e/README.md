# HR Desk end-to-end tests

Playwright suite that drives the running app at `http://localhost:3000` against the local database on port 5433.

## Run

1. Start HR Desk: `..\Start-HRDesk.ps1 -Detach`. The browser origin must be `http://localhost:3000`, because the API rejects writes from any other origin.
2. Install once: `npm install` and `npx playwright install chromium`.
3. Run everything: `npx playwright test`, or one project: `npm run test:ui`, `npm run test:access`, `npm run test:matrix`.
4. Summarize: `npm run report` writes `report/matrix-summary.md`. `node lib/visibility-table.mjs` prints who can open which policy.

A full run takes about 6 minutes and calls OpenAI for every question.

## What setup changes in the database

Global setup creates or resets one `e2e.<designation>@stylo.example` account per designation, plus `e2e.admin@stylo.example`, and signs each one in. It clears `answer_cache` so every run measures the current code. Tests clear `request_budget` (except per-account sign-in counters) so the rate limiter does not mask answers. Use this suite only on a local database.

## Projects

- `ui`: sign-up, sign-in, admin gating, and asking in the browser.
- `access`: admin routes, sessions, chat ownership, and a sweep of 6 policies × 35 designations.
- `matrix`: `cases/*.json`. Each case checks the outcome, an expected fact, and forbidden content. Verdicts land in `report/matrix/<id>.json`.
