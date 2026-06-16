# CAS Analyzer & True Portfolio XIRR Engine

A robust, full-stack application that parses Consolidated Account Statements (CAS) from CAMS/KFintech and calculates your exact, true portfolio XIRR. The engine is explicitly designed to solve edge cases where standard aggregators (like Investwell) fail, while allowing you to toggle calculation methodologies to achieve parity with them.

## Key Features

- **True Portfolio XIRR**: Calculates an exact XIRR by mapping all historical cash flows directly to live, up-to-date NAVs fetched via AMFI APIs.
- **Family & PAN Breakdown**: Groups folios intelligently, preventing data cross-contamination when the same mutual fund scheme is held across different PANs within a family CAS.
- **Benchmark Audit**: Compare the Time-Weighted Returns (Capture Ratios) of any scheme against standard NSE benchmarks.
- **Unmatched Funds Resolver**: Manual price mapping for Alternative Investment Funds (AIFs) or missing schemes directly in the UI.

---

## 📐 XIRR Calculation Methodology

Mutual fund statements are highly complex, containing internal transfers, tax deductions, missing NAVs, and reversals. The analyzer uses the following strict cash flow methodology:

### 1. Internal Switch Deduplication (Pooled XIRR)
At the **Portfolio** or **PAN Level**, a switch from Fund A to Fund B is merely money moving from one pocket to another. 
- The engine scans the transaction ledger and pairs `SWITCH OUT` and `SWITCH IN` transactions that occur within a 7-day window and match in monetary amount. 
- These paired internal transfers are **excluded** from the top-level cash flow calculation, preventing massive artificial cash flow spikes that distort your XIRR.
- However, at the **Category** and **Scheme Level**, these switches are correctly preserved as genuine inflows/outflows for that specific bucket.

### 2. The "Excl-Tax" Toggle (Investwell Parity)
Aggregators like Investwell compute a theoretical XIRR that ignores real-world friction. 
- **True XIRR (Default)**: Accounts for Stamp Duty, STT (Securities Transaction Tax), and TDS. These are treated as lost money (outflows that never purchased units or were withheld from proceeds), resulting in a mathematically pure but slightly lower XIRR.
- **Excl-Tax XIRR**: Flipping the toggle on the dashboard drops these tax lines entirely, creating perfect parity with Investwell's reporting.

### 3. Missing NAVs & AIF Handling
Standard aggregators completely fail or drop funds that aren't publicly listed (like AIFs or PMS).
- The engine uses the explicit valuation injected by the CAS statement as a fallback. 
- For the historical trend chart, it linearly interpolates this fallback NAV backward to your original purchase dates. This prevents artificial drop-offs in the trendline and ensures the AIF's valuation is perfectly accounted for across time.

### 4. Trend Downsampling & Performance
Calculating daily XIRR across thousands of days and transactions is computationally intensive. 
- The engine groups raw cashflows into an $O(1)$ iterable DataFrame.
- It calculates XIRR daily for the last 180 days, but downsamples older data (Fridays only, plus specific CAS boundaries) to deliver instant chart rendering without sacrificing the accuracy of the final data point.

## Tech Stack
- **Backend**: Python (FastAPI, Pandas, scipy.optimize for XIRR, casparser)
- **Frontend**: React (TypeScript, Vite, Tailwind CSS, Recharts for trending)
