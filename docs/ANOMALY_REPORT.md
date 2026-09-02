# Anomaly Report download

The **Download Report** button in the Root Cause Synthesis card (Section 05,
next to *Copy Briefing*) exports a short standalone HTML file about the
currently selected anomaly.

- `js/report.js` &rarr; `downloadAnomalyReport()`
- Reads `ANOMALY_DATASET[APP_STATE.activeAnomalyKey]` (already role-masked and
  merged with live backend data by `normalizeAnomalyForUI`) &mdash; no fetch.
- Output: `anomaly-report-<scenario>-<date>.html`, ~4 KB, one page.

Contents: header (SKU / region / date / status / confidence), headline +
summary, then

| Section | Source |
|---|---|
| Detection confidence | inline SVG bar, `anom.confidence` |
| Price&ndash;Volume&ndash;Mix decomposition | inline SVG diverging bars, `anom.pvm.{volume,price,mix,other}.val` |
| Root cause &mdash; upstream variable (EasyRCA) | top 3 of `anom.rootCause.rootCauses`, else the `reason` |
| Attribution &mdash; responsible slice (Adtributor) | top 3 of `anom.attribution.candidates`, else the `reason` |
| Root cause synthesis | `anom.synthesis.{title,body}` |
| Recommended action | `anom.recommendedAction`, or the abstention reason |

Everything is escaped; masked/`RESTRICTED` values pass through as the server
sent them. Delivered via a `Blob` + `<a download>` &mdash; no libraries.
