# Improvements Plan (post trend-fix)

## Hard invariant (protects the Investwell-matching XIRR)

The headline XIRR cards come **only** from `calculate_xirr(transactions_df, valuation, date, ...)`.
None of the work below may modify these functions or their inputs:

- `metrics.calculate_xirr`, `classify_cashflow`, `_match_internal_switch_pairs`,
  `compute_invested_withdrawn`, `prepare_base_cashflows`, `calculate_xirr_fast`
- the valuation inputs `total_value` (CAS statement) and `live_total_value` (holdings × live NAV)

**Proof gate (run before & after all changes):** POST the CAS to `/api/analyze`, save
`liveXirr`, `casXirr`, `exclTax.liveXirr`, `exclTax.casXirr`, and every `fundWise[].LiveXIRR`.
After the changes, re-run and assert the diff is exactly 0. If anything moved, stop.

---

## Group A — zero numeric impact (UI / text / tests / cleanup)

### A1. Excl-tax (Investwell-parity) toggle on main cards
- `Dashboard.tsx`: add a small toggle ("True XIRR" ↔ "Excl-tax"). When excl-tax is on, the
  "True Portfolio XIRR" and "CAS PDF XIRR" cards read `exclTax.liveXirr` / `exclTax.casXirr`
  (and per-PAN equivalents if available) instead of `liveXirr` / `casXirr`.
- Pure display switch — both values already exist in the response. No backend change.

### A2. Regression tests for the cashflow/XIRR engine
- New `tests/test_engine.py` (pytest). Cover, with small hand-built DataFrames:
  - `classify_cashflow`: purchase/redemption/switch/reinvest(None)/stamp-duty/STT signs;
    `include_switches` and `include_taxes` flags.
  - `_match_internal_switch_pairs`: paired legs cancel, orphaned leg kept.
  - `compute_invested_withdrawn`: sign-safe vs casparser-negative redemptions.
  - `compute_daily_portfolio_value`: a buy-more-later case and a partial-sell case
    (assert the pre-buy/pre-sell day uses the right units), and final day == current holdings.
- Tests **encode current behavior** — they reinforce the freeze, they don't change it.
- Delete or fold the ad-hoc `test_load.py` / `test_bug.py` / `test_excel.py` /
  `test_parser.py` / `test_metrics.py` scripts into the real suite.

### A3. Honest privacy copy + nicer errors
- `UploadZone.tsx`: change "your data is processed locally and never stored" to reflect
  reality — the PDF is processed in a temp file (deleted after), and only **fund identifiers**
  (scheme→code mappings, public NAVs) are disk-cached, never amounts/holdings.
- `App.tsx`: replace the raw `alert(...)` on analyze failure with an inline error state on the
  upload card (wrong password / parse failure).

### A4. Dead-code removal (verify-unused first)
- Confirm the UI renders nothing from `legacyXirr` / `fundWise[].LegacyXIRR`. If unused:
  drop the `calculate_xirr_legacy` call in `/api/analyze` and the field, and remove
  `calculate_xirr_legacy`, `calculate_xirr_from_scratch`, `xirr_from_scratch` from `metrics.py`.
- Keep `calculate_xirr` and `calculate_xirr_fast` (the live paths) untouched.

### A5. Stop re-parsing the PDF in `/api/trend`
- `/api/trend` currently re-uploads + re-parses + re-fixes opening balances. Lowest-risk
  refactor: extract the shared "parse → extract_transactions → resolve NAVs → fix opening
  balances" steps into one helper used by both endpoints, so behavior is identical and the
  logic lives once. (Same data in → same numbers out; trend math unchanged.)
- Same pattern available for `/api/benchmark-audit` if convenient.

---

## Group B — touches the trend line only (never the headline cards)

### B1. Reconciliation guard (read-only / flag)
- After building the trend, compute each scheme's **final** cumulative units and compare to
  `holdings_df` current units. If `|diff| / units > ~1%` for any scheme, add it to a
  `reconWarnings[]` in the `/api/trend` response and show an amber note in the chart area
  ("Trend may be approximate for X — units don't reconcile to current holdings").
- **No mutation.** This only surfaces the drift I currently ask you to eyeball; it cannot
  change any number. (A future opt-in could scale units to reconcile, but not in this pass.)

### B2. Trend downsampling for speed
- Today the server solves XIRR for **every day** since inception (~3,650 solves for a 10y
  portfolio). Reduce the date grid: **daily** for the last ~6 months and a ±10-day window
  around `casDate`; **weekly** before that.
- The XIRR value at each retained day is computed by the **same** `calculate_xirr_fast` — only
  the *number of points* changes, not their values. Headline cards: untouched.
- Tradeoff: for old dates, Value-by-Date snaps to the nearest computed day (within ~3–4 days)
  — the existing "nearest trading day" note already covers this UX. Recent dates stay exact.

---

## Suggested order

1. A2 (tests) — establishes the golden snapshot that guards everything else.
2. B1 (reconciliation flag) + B2 (downsampling) — the real robustness/speed wins.
3. A1 (excl-tax toggle) — quick, useful.
4. A3 / A4 / A5 — cleanup.

## Final verification

- Golden-snapshot diff = 0 on all headline + fundwise XIRR values (the proof gate above).
- `pytest` green.
- Upload CAS: cards instant, chart streams in; early XIRR not inflated; last trend point ≈
  Live Valuation; any unit drift shows a recon note; old-date Value-by-Date still resolves.
