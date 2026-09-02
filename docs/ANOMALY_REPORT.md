# Anomaly Report download

The **Download Report** button in the Root Cause Synthesis card (Section 05,
next to *Copy Briefing*) exports a short **PDF** about the currently selected
anomaly.

- `js/report.js` &rarr; `downloadAnomalyReport()`
- Reads `ANOMALY_DATASET[APP_STATE.activeAnomalyKey]` (already role-masked and
  merged with live backend data by `normalizeAnomalyForUI`) &mdash; no fetch.
- Rendered client-side with **jsPDF** (`jspdf@2.5.2`, loaded from jsDelivr in
  `dashboard.html`). One page, ~12&nbsp;KB, selectable vector text.
- Output: `anomaly-report-<scenario>-<date>.pdf`.
- If jsPDF fails to load (offline / CDN blocked) it falls back to a standalone
  HTML file with the same content.

Contents: header (SKU / region / date / status / confidence), headline +
summary, then

| Section | Source |
|---|---|
| Detection confidence | vector bar, `anom.confidence` |
| Price&ndash;Volume&ndash;Mix decomposition | diverging vector bars, `anom.pvm.{volume,price,mix,other}.val` |
| Root cause &mdash; upstream variable (EasyRCA) | top 3 of `anom.rootCause.rootCauses`, else the `reason` |
| Attribution &mdash; responsible slice (Adtributor) | top 3 of `anom.attribution.candidates`, else the `reason` |
| Root cause synthesis | `anom.synthesis.{title,body}` |
| Recommended action | `anom.recommendedAction`, or the abstention reason |

Masked / `RESTRICTED` values pass through exactly as the server sent them.
