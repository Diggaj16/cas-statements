# CAS Statements Analyzer

A functional dashboard and analytical tool for deep financial analysis. Designed for financial advisors, this tool ingests complex CAS statements and extracts clear, accurate performance metrics to present to clients. 

It exists to replace tedious Excel crunching with instant, mathematically rigorous Time-Weighted Returns (TWR), XIRR, and Capture Ratios based on official CAS PDF data. The engine is explicitly designed to solve edge cases where standard aggregators (like Investwell) fail, while allowing you to toggle calculation methodologies to achieve parity with them.

## Features

- **Automated CAS Parsing**: Replaces tedious manual Excel crunching.
- **Advanced Metrics**: Calculates mathematically rigorous Time-Weighted Returns, XIRR, and Capture Ratios.
- **True Portfolio XIRR**: Calculates an exact XIRR by mapping all historical cash flows directly to live, up-to-date NAVs fetched via AMFI APIs.
- **Family & PAN Breakdown**: Groups folios intelligently, preventing data cross-contamination when the same mutual fund scheme is held across different PANs within a family CAS.
- **Benchmark Audit**: Compare the Time-Weighted Returns (Capture Ratios) of any scheme against standard NSE benchmarks.
- **Unmatched Funds Resolver**: Manual price mapping for Alternative Investment Funds (AIFs) or missing schemes directly in the UI.
- **Institutional-Grade UI**: A clean, subdued, professional dashboard that prioritizes clarity, data density, and precision without clutter.

## Setup & Run

### Backend (Python)
Ensure you have Python installed. You can install the dependencies via:
```bash
pip install -r requirements.txt
```

Run the backend with:
```bash
$env:PYTHONPATH="."; uvicorn main:app --reload
```

### Frontend (React/Node)
Navigate to the `frontend` directory and install the necessary packages:
```bash
cd frontend
npm install
npm run dev
```

---

## 📐 XIRR Calculation Methodology & Formulas

Mutual fund statements are highly complex, containing internal transfers, tax deductions, missing NAVs, and reversals. 

### The XIRR Mathematical Equation
Extended Internal Rate of Return (XIRR) is the annualized discount rate $r$ that sets the Net Present Value (NPV) of all cash flows (including the final portfolio valuation) exactly to zero.

Our engine solves for $r$ using the SciPy implementation of the secant method on the following equation:

$$ \sum_{i=0}^{N} \frac{CF_i}{(1 + r)^{\frac{d_i - d_0}{365}}} = 0 $$

Where:
- $CF_i$ = The net cash flow on day $i$ (Negative for investments/outflows, Positive for withdrawals/dividends)
- $CF_N$ = The final **Current Valuation** of the portfolio (treated as a massive final Positive cash inflow)
- $d_i$ = The date of the $i$-th cash flow
- $d_0$ = The date of the very first transaction

### 1. Internal Switch Deduplication (Pooled XIRR)
At the **Portfolio** or **PAN Level**, a switch from Fund A to Fund B is merely money moving from one pocket to another. If treated naively, a ₹10,00,000 switch would create a massive outflow and inflow that heavily skews the weighted duration of capital.
- **Algorithm**: The engine scans the transaction ledger and pairs `SWITCH OUT` and `SWITCH IN` transactions that occur within a 7-day window ($|d_{in} - d_{out}| \leq 7$) and match in monetary amount ($\Delta \leq 2\%$).
- **Result**: These paired internal transfers are **excluded** ($CF_{switch} = 0$) from the top-level cash flow calculation.
- **Fund Level**: At the Category and Scheme level, these switches are correctly preserved as genuine inflows/outflows for that specific bucket.

### 2. The "Excl-Tax" Toggle (Investwell Parity)
Aggregators like Investwell compute a theoretical XIRR that ignores real-world friction. 
- **True XIRR (Default)**: Accounts for Stamp Duty, STT (Securities Transaction Tax), and TDS. These are treated as lost money (outflows that never purchased units or were withheld from proceeds), resulting in a mathematically pure but slightly lower XIRR that exactly matches the investor's pocket.
- **Excl-Tax XIRR**: Flipping the toggle on the dashboard drops these tax lines entirely ($CF_{tax} = 0$), creating perfect parity with Investwell's theoretical reporting.

### 3. Missing NAVs & AIF Handling
Standard aggregators completely fail or drop funds that aren't publicly listed (like Alternative Investment Funds or PMS).
- **Interpolated Valuation**: The engine explicitly captures the fallback valuation injected by the CAS statement ($Value_{cas} / Units_{cas}$).
- **Trend Smoothing**: For the historical trend chart, the engine linearly interpolates this fallback NAV backward to the original purchase dates. This prevents artificial drop-offs in the daily portfolio trendline while ensuring the AIF's valuation is perfectly accounted for across time.

### 4. Trend Downsampling & Performance
Calculating daily XIRR across thousands of days and transactions is computationally intensive (solving a polynomial equation for every day in history).
- **$O(1)$ Cash Flows**: The engine pre-groups raw cashflows into a single DataFrame.
- **Downsampling Algorithm**: It computes XIRR daily for the last 180 days, but downsamples older data (calculating only on Fridays, plus specific CAS boundaries). This delivers instant chart rendering without sacrificing the accuracy of the final live data point.
