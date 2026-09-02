# Live anomaly stream (moving deck)

The left deck is a ticker, not a one-shot list. Instead of pulling all 57
anomalies at once (each costs an EasyRCA + Adtributor + PVM workup), the
dashboard polls a few at a time and prepends each as a card, newest on top.

## Backend &mdash; `GET /api/anomalies/stream?after=<n>&limit=<k>`

- `api_server.py` &rarr; `_handle_anomalies_stream`.
- Deterministic order: the **inverse** of `_handle_anomalies_list`'s materiality
  rank (`scenario_key LIKE 'gen-%'` first, then severity ascending, then
  `ABS(z_score)` ascending), so raw statistical detections stream in first and
  the fully worked-up curated scenarios / most significant movements arrive last
  and land on top.
- `LIMIT ? OFFSET ?` &mdash; only `limit` rows (1&ndash;6, default 3) get the RCA
  work per poll.
- Every item goes through the **same** pipeline as every other anomaly route:
  `_row_to_anomaly_dict` &rarr; `_apply_persona(role)` &rarr;
  `_apply_entitlements(role)`. Role comes from `X-User-Role` via `_resolve_role`.
  No new unmasked path &mdash; stream masking is byte-identical to the list and
  detail endpoints.
- Response: `{ items, after, nextAfter, total, done }`. `done` is
  `nextAfter >= total`.

## Frontend &mdash; `js/stream.js`

- `startAnomalyStream()` replaces the one-shot `loadAnomalyListFromBackend()` at
  boot **when the backend is reachable**; otherwise `js/app.js` falls back to the
  static `ANOMALY_DATASET` render.
- `_streamTick()` polls every 2&nbsp;s while `done` is false, then every 30&nbsp;s
  as a heartbeat. Each item is run through `apiClient.normalizeAnomalyForUI` (same
  as before) and `scenarioCardHtml(key, anom)` (shared with `renderSidebarCards`),
  then slid in at the top of `.sidebar-deck-scroll` (`.sc-arriving` &rarr;
  `.sc-fresh` in `css/layout.css`).
- The first arrival is auto-selected so the dashboard is never empty.
- Deck header shows `LIVE n/total` while streaming, `n loaded` when drained
  (`.deck-live-badge`).
- Role switch (`setAppRole`, `js/actions.js`) calls `resetAnomalyStream()` &mdash;
  the deck rebuilds from the top under the new role's masking rather than doing a
  one-shot reload.
- With a search / period filter active, arrivals defer to the filtered
  `renderSidebarCards()` so they don't bypass the filter.
