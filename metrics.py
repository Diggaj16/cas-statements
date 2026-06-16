import pandas as pd
import numpy as np
from pyxirr import xirr
from datetime import datetime

# Transaction type keyword groups (casparser type names + common variants)
INVEST_KEYS = ['PURCHASE', 'SWITCH_IN', 'STP_IN', 'OPENING_BALANCE', 'SIP']
WITHDRAW_KEYS = ['REDEMPTION', 'SWITCH_OUT', 'SWP', 'DIVIDEND_PAYOUT', 'STP_OUT']
TAX_KEYS = ['STT', 'TDS', 'TAX', 'FEE', 'CHARGE', 'BROKERAGE']
SWITCH_KEYS = ['SWITCH_IN', 'SWITCH_OUT', 'STP_IN', 'STP_OUT']


def classify_cashflow(txn_type, txn_desc, amt, units, include_switches=True, perspective='investor', include_taxes=True):
    """
    Maps a transaction to a signed external cash flow, or None if it is not
    an external flow.

    Investor perspective (XIRR convention, matches Excel/portfolio trackers):
      money paid in  -> negative, money received -> positive.
    Portfolio perspective (TWR): money entering the portfolio -> positive.

    Standard methodology:
    - Dividend reinvestments are internal compounding, never a cash flow.
    - Switches are flows at fund level; at portfolio level (include_switches=False)
      they are internal transfers and excluded.
    - CAS purchase rows are net of stamp duty (verified: 9999.50 purchase +
      0.50 stamp = 10000 debit), so stamp duty is real money out of pocket.
      It never enters the portfolio though, so it is excluded for TWR.
    """
    if amt is None or pd.isna(amt) or amt == 0:
        return None

    t = str(txn_type).upper()
    d = str(txn_desc).upper()

    if 'REINVEST' in t or 'REINVEST' in d:
        return None
    if 'SEGREGAT' in t or 'SEGREGAT' in d:
        return None

    # NOTE: switch exclusion at portfolio level is handled by the callers via
    # _match_internal_switch_pairs — only PAIRED legs are internal transfers.
    # An orphaned leg (counterpart typed as PURCHASE, or in a folio outside
    # this CAS) is a real flow and must be kept, else XIRR skews.
    if any(k in t for k in SWITCH_KEYS) and not include_switches:
        return None

    is_tax = ('STAMP' in t or 'DUTY' in t or 'STAMP' in d or 'DUTY' in d
              or any(k in t for k in TAX_KEYS))
    if is_tax:
        # Taxes/charges are paid by the investor but never enter the portfolio
        # value. include_taxes=False drops them entirely — the Investwell-style
        # convention (their feed carries only net transaction amounts).
        if perspective == 'portfolio' or not include_taxes:
            return None
        return -abs(amt)

    if 'REVERSAL' in t:
        # A reversal cancels its original transaction: purchase reversal
        # (units < 0) refunds money to the investor.
        cf = abs(amt) if (units is not None and not pd.isna(units) and units < 0) else -abs(amt)
    elif any(k in t for k in INVEST_KEYS):
        cf = -abs(amt)
    elif any(k in t for k in WITHDRAW_KEYS):
        cf = abs(amt)
    else:
        # Fallback by unit sign; rows with no units and no recognized type
        # (MISC, address updates, etc.) are not external cash flows.
        if units is not None and not pd.isna(units) and units > 0:
            cf = -abs(amt)
        elif units is not None and not pd.isna(units) and units < 0:
            cf = abs(amt)
        else:
            return None

    return -cf if perspective == 'portfolio' else cf


def compute_invested_withdrawn(transactions_df, include_switches=False):
    """
    Out-of-pocket additions and withdrawals. Reinvestments are internal and
    excluded; stamp duty counts as invested; STT/TDS reduce redemption/dividend
    proceeds. By default switch legs that PAIR UP within the statement are
    excluded (internal at portfolio/PAN level) while orphaned legs are kept;
    pass include_switches=True at scheme/category level where every switch is
    a real flow in or out of the group. Sign-safe: works whether the CAS
    reports redemption amounts as negative (casparser) or positive.
    Returns (invested, withdrawn).
    """
    invested = 0.0
    withdrawn = 0.0
    if transactions_df is None or transactions_df.empty:
        return invested, withdrawn

    paired = set() if include_switches else _match_internal_switch_pairs(transactions_df)
    for idx, row in transactions_df.iterrows():
        if idx in paired:
            continue
        cf = classify_cashflow(row.get('Type'), row.get('Description', ''),
                               row.get('Amount'), row.get('Units'),
                               include_switches=True)
        if cf is None:
            continue
        t = str(row.get('Type', '')).upper()
        if cf < 0:
            if 'STT' in t or 'TDS' in t:
                # withheld from proceeds: reduces what was received
                withdrawn += cf
            else:
                invested += -cf
        else:
            withdrawn += cf
    return invested, max(withdrawn, 0.0)


def _match_internal_switch_pairs(transactions_df, window_days=7, tolerance=0.02):
    """
    Identify switch legs that pair up INSIDE this statement: a SWITCH_OUT and a
    SWITCH_IN within `window_days` of each other whose amounts agree within
    `tolerance`. Only such pairs are internal transfers that cancel at
    portfolio level. Orphaned legs (counterpart typed as PURCHASE by the RTA,
    or sitting in a folio outside this CAS) are REAL flows and must be kept —
    dropping them one-sided skews XIRR. Returns the set of paired row indices.
    """
    outs = []
    ins = []
    for idx, row in transactions_df.iterrows():
        t = str(row.get('Type', '')).upper()
        if not any(k in t for k in SWITCH_KEYS):
            continue
        amt = row.get('Amount')
        if amt is None or pd.isna(amt) or amt == 0:
            continue
        leg = (idx, pd.to_datetime(row['Date']), abs(float(amt)))
        if 'OUT' in t:
            outs.append(leg)
        else:
            ins.append(leg)

    matched = set()
    used_ins = set()
    for o_idx, o_date, o_amt in outs:
        best = None
        for j, (i_idx, i_date, i_amt) in enumerate(ins):
            if j in used_ins:
                continue
            day_gap = abs((i_date - o_date).days)
            if day_gap > window_days:
                continue
            if o_amt == 0 or abs(i_amt - o_amt) / o_amt > tolerance:
                continue
            if best is None or day_gap < best[0]:
                best = (day_gap, j, i_idx)
        if best is not None:
            used_ins.add(best[1])
            matched.add(o_idx)
            matched.add(best[2])
    return matched


def _build_cashflows(transactions_df, include_switches=True, with_records=False, include_taxes=True):
    cash_flows = []
    cf_records = []
    paired = set() if include_switches else _match_internal_switch_pairs(transactions_df)
    for idx, row in transactions_df.iterrows():
        if idx in paired:
            continue
        cf = classify_cashflow(row.get('Type'), row.get('Description', ''),
                               row.get('Amount'), row.get('Units'),
                               include_switches=True, include_taxes=include_taxes)
        if cf is None:
            continue
        cash_flows.append((row['Date'].date(), cf))
        if with_records:
            cf_records.append({'Date': row['Date'].date(), 'Amount': cf,
                               'Scheme': row.get('Scheme'), 'Type': row.get('Type'),
                               'Description': row.get('Description', '')})
    return cash_flows, cf_records


def calculate_xirr(transactions_df, current_valuation, valuation_date=None, include_switches=True, include_taxes=True):
    """
    XIRR over the given transactions plus current valuation as the final inflow.
    Use include_switches=False for pooled portfolio/PAN level calculations:
    switch legs that pair up (out+in, same amount, within days) cancel and are
    excluded, while orphaned legs remain as real flows. Use True for
    fund/category level where every switch is a flow.
    include_taxes=False drops stamp duty/STT/TDS lines (Investwell convention).
    """
    cash_flows, cf_records = _build_cashflows(transactions_df, include_switches, with_records=True,
                                              include_taxes=include_taxes)

    if current_valuation > 0:
        today = valuation_date if valuation_date else datetime.now().date()
        cash_flows.append((today, current_valuation))
        cf_records.append({'Date': today, 'Amount': current_valuation,
                           'Scheme': 'ALL (Current Valuation)', 'Type': 'VALUATION',
                           'Description': 'Final Portfolio Valuation'})

    cf_df = pd.DataFrame(cash_flows, columns=['Date', 'Amount'])
    cf_records_df = pd.DataFrame(cf_records)
    if cf_df.empty:
        return None, cf_records_df
    cf_df = cf_df.groupby('Date')['Amount'].sum().reset_index()

    try:
        ans = xirr(cf_df['Date'], cf_df['Amount'])
        return ans, cf_records_df
    except Exception:
        return None, cf_records_df


def build_portfolio_history(transactions_df, historical_navs_dict):
    """
    Returns the monthly Time-Weighted Returns (TWR) of the portfolio and debug dataframes.
    """
    if transactions_df.empty:
        return pd.Series(dtype=float), None, None, None

    start_date = transactions_df['Date'].min()
    end_date = datetime.now()
    date_range = pd.date_range(start=start_date, end=end_date, freq='D')

    scheme_values = {}
    scheme_returns_df = {}

    for amfi, scheme_txns in transactions_df.groupby('AMFI'):
        if not amfi or amfi not in historical_navs_dict or historical_navs_dict[amfi] is None:
            continue

        nav_df = historical_navs_dict[amfi]

        daily_units_change = scheme_txns.groupby('Date')['Units'].sum()
        daily_units = daily_units_change.reindex(date_range, fill_value=0.0).cumsum()

        # method='ffill' ensures initial dates before first API data point don't become NaN
        scheme_navs = nav_df['nav'].reindex(date_range, method='ffill').bfill()

        scheme_daily_value = daily_units * scheme_navs
        scheme_daily_value = scheme_daily_value.fillna(0.0)

        scheme_values[amfi] = scheme_daily_value

        # Daily net external cash flows from the PORTFOLIO's perspective
        daily_cfs = pd.Series(0.0, index=date_range)
        for _, row in scheme_txns.iterrows():
            cf = classify_cashflow(row.get('Type'), row.get('Description', ''),
                                   row.get('Amount'), row.get('Units'),
                                   include_switches=True, perspective='portfolio')
            if cf is None:
                continue
            dt = pd.to_datetime(row['Date'])
            if dt in daily_cfs.index:
                daily_cfs[dt] += cf

        prev_value = scheme_daily_value.shift(1).fillna(0.0)

        # TWR Formula: r_t = (V_t - V_{t-1} - CF_t) / V_{t-1}
        # Calculates true economic return accounting for cash flows (like dividends/fees)
        daily_ret = (scheme_daily_value - prev_value - daily_cfs) / prev_value
        daily_ret = daily_ret.replace([np.inf, -np.inf], 0.0).fillna(0.0)

        scheme_returns_df[amfi] = daily_ret

    if not scheme_values:
        return pd.Series(dtype=float), None, None, None

    values_df = pd.DataFrame(scheme_values)
    # Daily true return of each scheme
    daily_scheme_returns = pd.DataFrame(scheme_returns_df)

    # We need to weight the returns by the BEGINNING of day value
    values_yesterday = values_df.shift(1).fillna(0.0)
    total_value_yesterday = values_yesterday.sum(axis=1)

    weights_yesterday = values_yesterday.div(total_value_yesterday, axis=0).fillna(0.0)

    portfolio_daily_returns = (weights_yesterday * daily_scheme_returns).sum(axis=1)

    wealth_index = (1 + portfolio_daily_returns).cumprod()
    wealth_index.iloc[0] = 1.0  # start at 1

    monthly_wealth = wealth_index.resample('ME').last()
    monthly_returns = monthly_wealth.pct_change().dropna()

    # debug dataframes
    debug_values = values_df.copy()
    debug_values['Total Portfolio Value'] = values_df.sum(axis=1)

    debug_twr = pd.DataFrame({
        'Total Value Yesterday': total_value_yesterday,
        'Portfolio Daily Return': portfolio_daily_returns,
        'Wealth Index': wealth_index
    })

    return monthly_returns, debug_values, daily_scheme_returns, debug_twr


def calculate_capture_ratios(portfolio_returns, benchmark_returns):
    # Both should be pandas Series with datetime index
    df = pd.concat([portfolio_returns.rename('Portfolio'), benchmark_returns.rename('Benchmark')], axis=1).dropna()

    if df.empty:
        return None, None, df

    up_market = df[df['Benchmark'] > 0]
    down_market = df[df['Benchmark'] <= 0]

    def geo_return(returns):
        return np.prod(1 + returns) - 1

    up_port_ret = geo_return(up_market['Portfolio'])
    up_bench_ret = geo_return(up_market['Benchmark'])

    down_port_ret = geo_return(down_market['Portfolio'])
    down_bench_ret = geo_return(down_market['Benchmark'])

    up_capture = (up_port_ret / up_bench_ret) * 100 if up_bench_ret != 0 else 0
    down_capture = (down_port_ret / down_bench_ret) * 100 if down_bench_ret != 0 else 0

    return up_capture, down_capture, df





def prepare_base_cashflows(transactions_df, include_switches=True):
    """
    Pre-processes a transactions dataframe to extract and group daily cash flows.
    This avoids O(N) iteration overhead when calculating XIRR repeatedly for the same history.
    """
    cash_flows, _ = _build_cashflows(transactions_df, include_switches)

    cf_df = pd.DataFrame(cash_flows, columns=['Date', 'Amount'])
    if not cf_df.empty:
        cf_df = cf_df.groupby('Date')['Amount'].sum().reset_index()
    return cf_df


def calculate_xirr_fast(base_cf_df, current_valuation, valuation_date):
    """
    Computes XIRR in O(1) DataFrame operations using pre-grouped cash flows.
    Extremely fast for looping. Only cash flows on or before valuation_date are
    used, so the valuation and the flows describe the same point in time.
    """
    if base_cf_df.empty:
        return None

    final_df = base_cf_df[base_cf_df['Date'] <= valuation_date]
    if final_df.empty:
        return None

    if current_valuation > 0:
        val_df = pd.DataFrame([{'Date': valuation_date, 'Amount': current_valuation}])
        final_df = pd.concat([final_df, val_df], ignore_index=True)
        final_df = final_df.groupby('Date')['Amount'].sum().reset_index()

    try:
        return xirr(final_df['Date'], final_df['Amount'])
    except Exception:
        return None


def compute_daily_portfolio_value(transactions_df, navs_dict, date_range, scheme_amfi_map, holdings_df=None):
    """Daily portfolio value = sum over schemes of (net cumulative units held that
    day) × (NAV that day). Units cumsum includes EVERY unit-changing row
    (purchase/reinvest/redemption/switch/opening balance) so the final day
    reconciles to current holdings. Schemes with no NAV are skipped (caller flags)."""
    daily_val = pd.Series(0.0, index=date_range)
    skipped = []
    
    # Pre-compute final statement values for unmatched funds
    stmt_values = {}
    if holdings_df is not None and not holdings_df.empty:
        for _, row in holdings_df.iterrows():
            if row['Value'] > 0 and row['Units'] > 0:
                stmt_values[row['Scheme']] = row['Value'] / row['Units']

    for scheme, txns in transactions_df.groupby('Scheme'):
        amfi = scheme_amfi_map.get(scheme)
        nav = navs_dict.get(amfi) if amfi else None
        
        daily_units = (txns.groupby('Date')['Units'].sum()
                          .reindex(date_range, fill_value=0.0).cumsum())
                          
        if nav is None or nav.empty:
            skipped.append(scheme)
            navs = pd.Series(np.nan, index=date_range)
            
            # 1. Fill known transaction NAVs
            if 'NAV' in txns.columns:
                known_navs = txns.dropna(subset=['NAV']).groupby('Date')['NAV'].last()
                for d, val in known_navs.items():
                    if d in navs.index:
                        navs[d] = val
            
            # 2. Pin the final date NAV to the statement's implied NAV
            if scheme in stmt_values:
                navs.iloc[-1] = stmt_values[scheme]
            
            # 3. Interpolate the missing daily NAVs smoothly
            navs = navs.interpolate(method='linear').ffill().bfill().fillna(0.0)
        else:
            navs = nav['nav'].reindex(date_range, method='ffill').bfill()
            
        daily_val += (daily_units * navs).fillna(0.0)
    return daily_val, skipped
