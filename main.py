from fastapi import FastAPI, UploadFile, Form, File, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
import pandas as pd
import tempfile
import logging
from datetime import datetime
import os
import io
import traceback
import json

from parser import parse_cas, extract_transactions, get_current_valuation
from metrics import calculate_xirr, calculate_xirr_fast, prepare_base_cashflows, build_portfolio_history, calculate_capture_ratios, compute_invested_withdrawn, compute_daily_portfolio_value
from mf_api import fetch_historical_nav, get_manual_map, save_manual_override, cache as nav_cache
from src.exporter import generate_cas_excel, generate_audit_excel

try:
    from BharatFinTrack import NSETRI
except ImportError:
    NSETRI = None

import math

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def sanitize_json(obj):
    """Recursively replace inf/-inf/NaN with None so the payload is JSON-compliant.

    pandas/numpy and XIRR annualization can produce non-finite floats (e.g. a
    near-zero-duration holding annualizes to an astronomically large rate). FastAPI's
    JSON encoder raises ValueError on inf/NaN, which surfaces in the browser as a
    misleading 'NetworkError'. This makes the whole response safe regardless of source.
    """
    if isinstance(obj, dict):
        return {k: sanitize_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [sanitize_json(v) for v in obj]
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    # numpy scalar types (np.float64 is a float subclass, but np.float32 is not)
    try:
        import numpy as np
        if isinstance(obj, np.floating):
            f = float(obj)
            return f if math.isfinite(f) else None
        if isinstance(obj, np.integer):
            return int(obj)
    except Exception:
        pass
    return obj


app = FastAPI(title="Growthvine CAS API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/api/version")
async def version():
    """Marker for verifying which code revision the running server has loaded."""
    return {"methodology": "standard-xirr-v2"}

@app.get("/api/search-scheme")
async def search_scheme(q: str):
    """Proxy mfapi's scheme search so the manual-mapping UI can let the user pick
    the exact scheme (code + full name with plan/option) for an unmatched fund.
    """
    q = (q or "").strip()
    if len(q) < 3:
        return {"results": []}
    cache_key = f"__scheme_search__{q.lower()}"
    cached = nav_cache.get(cache_key)
    if cached is not None:
        return {"results": cached}
    try:
        import requests
        resp = requests.get("https://api.mfapi.in/mf/search", params={"q": q}, timeout=10)
        resp.raise_for_status()
        results = [
            {"code": str(item.get("schemeCode")), "name": item.get("schemeName")}
            for item in resp.json()
            if item.get("schemeCode")
        ]
        nav_cache.set(cache_key, results, expire=86400)
        return {"results": results}
    except Exception as e:
        logger.warning(f"Scheme search failed for '{q}': {e}")
        return {"results": []}

def process_cas_and_valuation(cas_tmp_path, cas_password, manual_mapping=None):
    parsed_data = parse_cas(cas_tmp_path, cas_password)

    if manual_mapping:
        try:
            for entry in json.loads(manual_mapping):
                save_manual_override(entry.get('scheme'), entry.get('isin'), entry.get('code'))
        except Exception as e:
            logger.warning(f"Could not apply manual_mapping: {e}")
    transactions_df = extract_transactions(parsed_data, manual_map=get_manual_map())

    import concurrent.futures
    amfi_codes = set(transactions_df['AMFI'].dropna().unique())
    valid_amfis = [str(a).strip() for a in amfi_codes if str(a).strip()]
    
    if valid_amfis:
        def preload_nav(amfi):
            fetch_historical_nav(amfi)
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            executor.map(preload_nav, valid_amfis)

    total_value, holdings_df, latest_val_date = get_current_valuation(parsed_data)

    data_warnings = []
    unresolved_funds = []
    seen_unresolved = set()
    for _, r in transactions_df.iterrows():
        if str(r.get('AMFI') or '').strip():
            continue
        scheme = r.get('Scheme')
        if scheme in seen_unresolved:
            continue
        seen_unresolved.add(scheme)
        unresolved_funds.append({
            "scheme": scheme,
            "isin": str(r.get('ISIN') or '').strip(),
            "pan": r.get('PAN'),
            "category": r.get('Category'),
        })

    txn_nav_df = transactions_df[(transactions_df['Type'] != 'OPENING_BALANCE') & (transactions_df['NAV'] > 0)]
    earliest_txn_nav = {}
    if not txn_nav_df.empty:
        for scheme, grp in txn_nav_df.sort_values('Date').groupby('Scheme'):
            earliest_txn_nav[scheme] = float(grp['NAV'].iloc[0])
    cas_val_nav = {}
    if not holdings_df.empty:
        for _, h in holdings_df.iterrows():
            if h.get('NAV'):
                cas_val_nav[h['Scheme']] = float(h['NAV'])

    opening_mask = transactions_df['Type'] == 'OPENING_BALANCE'
    if opening_mask.any():
        for idx, row in transactions_df[opening_mask].iterrows():
            amfi = row['AMFI']
            open_date = row['Date']
            units = row['Units']
            scheme = row['Scheme']
            nav_df = fetch_historical_nav(amfi) if (amfi and str(amfi).strip()) else None
            if nav_df is not None and not nav_df.empty and pd.notna(open_date):
                try:
                    dt = pd.to_datetime(open_date)
                    navs_until = nav_df.loc[:dt]['nav']
                    closest_nav = navs_until.iloc[-1] if not navs_until.empty else nav_df['nav'].iloc[0]
                    transactions_df.at[idx, 'Amount'] = float(units * closest_nav)
                    transactions_df.at[idx, 'NAV'] = float(closest_nav)
                except Exception as e:
                    logger.warning(f"Failed to fix opening balance for {amfi}: {e}")
            else:
                fallback_nav = earliest_txn_nav.get(scheme) or cas_val_nav.get(scheme)
                if fallback_nav and units:
                    transactions_df.at[idx, 'Amount'] = float(units * fallback_nav)
                    transactions_df.at[idx, 'NAV'] = float(fallback_nav)
                    data_warnings.append(
                        f"{scheme}: no NAV history — opening cost estimated from a nearby NAV. Map this fund for an exact figure."
                    )
                else:
                    logger.warning(f"No NAV data for {scheme}; opening balance has no cost and XIRR may be overstated")
                    data_warnings.append(
                        f"{scheme}: could not be priced (no AMFI/NAV) — opening-balance cost is missing, so its XIRR is overstated. Map this fund to fix it."
                    )

    live_total_value = 0.0
    live_date = datetime.now().date()
    scheme_amfi_map = transactions_df[['Scheme', 'AMFI']].drop_duplicates().set_index('Scheme')['AMFI'].to_dict()
    
    if not holdings_df.empty:
        for i, row in holdings_df.iterrows():
            scheme = row['Scheme']
            units = row['Units']
            amfi = scheme_amfi_map.get(scheme)
            
            live_nav = row['NAV']
            if amfi and str(amfi).strip():
                nav_df = fetch_historical_nav(amfi)
                if nav_df is not None and not nav_df.empty:
                    live_nav = nav_df['nav'].iloc[-1]
            
            val = units * live_nav
            if val == 0.0 and row['Value'] > 0:
                val = row['Value']
                
            live_total_value += val
            holdings_df.at[i, 'Live NAV'] = live_nav
            holdings_df.at[i, 'Live Value'] = val

    return (transactions_df, holdings_df, total_value, latest_val_date, 
            live_total_value, live_date, data_warnings, unresolved_funds, scheme_amfi_map, valid_amfis)

@app.post("/api/clear-cache")
async def clear_cache():
    nav_cache.clear()
    return {"message": "Cache cleared successfully"}

@app.post("/api/analyze")
async def analyze_cas(
    cas_file: UploadFile = File(...),
    cas_password: str = Form(...),
    manual_mapping: str = Form(None)
):
    cas_tmp_path = None
    try:
        # 1. Parse CAS
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            content = await cas_file.read()
            tmp.write(content)
            cas_tmp_path = tmp.name

        (transactions_df, holdings_df, total_value, latest_val_date,
         live_total_value, live_date, data_warnings, unresolved_funds,
         scheme_amfi_map, valid_amfis) = process_cas_and_valuation(cas_tmp_path, cas_password, manual_mapping)

        # 5. Core XIRR — pooled portfolio level, so switches are internal transfers
        live_xirr_val, _ = calculate_xirr(transactions_df, live_total_value, live_date, include_switches=False)
        cas_xirr_val, _ = calculate_xirr(transactions_df, total_value, latest_val_date, include_switches=False)

        # Investwell-style parity set: stamp duty/STT/TDS lines excluded
        live_xirr_notax, _ = calculate_xirr(transactions_df, live_total_value, live_date, include_switches=False, include_taxes=False)
        cas_xirr_notax, _ = calculate_xirr(transactions_df, total_value, latest_val_date, include_switches=False, include_taxes=False)

        fund_xirrs = []
        valid_funds = []
        
        # We will build a structured dictionary for the new Family & Category tab
        # Structure: { PAN: { "Valuation": 0, "TrueXIRR": 0, "Categories": { CATEGORY: { "Valuation": 0, "TrueXIRR": 0, "Funds": [] } } } }
        family_breakdown = {}
        
        for (pan, scheme), scheme_txns in transactions_df.groupby(['PAN', 'Scheme']):
            # Filter holdings_df by both Scheme and PAN
            val_row = holdings_df[(holdings_df['Scheme'] == scheme) & (holdings_df['PAN'] == pan)] if 'PAN' in holdings_df.columns else holdings_df[holdings_df['Scheme'] == scheme]
            live_scheme_val = val_row['Live Value'].sum() if not val_row.empty and 'Live Value' in val_row.columns else 0.0
            cas_scheme_val = val_row['Value'].sum() if not val_row.empty else 0.0
            
            category = scheme_txns['Category'].iloc[0] if 'Category' in scheme_txns.columns else 'Unknown Category'

            # Scheme level invested and withdrawals: switches are real flows here
            scheme_invested, scheme_withdrawals = compute_invested_withdrawn(scheme_txns, include_switches=True)
            scheme_net_invested = scheme_invested - scheme_withdrawals
            scheme_abs_return = 0.0
            if scheme_net_invested > 0 and live_scheme_val > 0:
                scheme_abs_return = ((live_scheme_val - scheme_net_invested) / scheme_net_invested) * 100
            
            if pan not in family_breakdown:
                family_breakdown[pan] = {"Valuation": 0.0, "Categories": {}}
            if category not in family_breakdown[pan]["Categories"]:
                family_breakdown[pan]["Categories"][category] = {"Valuation": 0.0, "Funds": []}
            
            if live_scheme_val == 0.0 and cas_scheme_val > 0:
                live_scheme_val = cas_scheme_val
                
            live_x_val = calculate_xirr(scheme_txns, live_scheme_val, live_date)[0]
            cas_x_val = calculate_xirr(scheme_txns, cas_scheme_val, latest_val_date)[0]
            live_x_notax = calculate_xirr(scheme_txns, live_scheme_val, live_date, include_taxes=False)[0]
            cas_x_notax = calculate_xirr(scheme_txns, cas_scheme_val, latest_val_date, include_taxes=False)[0]

            fund_data = {
                "Scheme": scheme,
                "AMFI": scheme_amfi_map.get(scheme) or None,
                "LiveValuation": live_scheme_val,
                "LiveXIRR": live_x_val * 100 if live_x_val is not None else None,
                "CASValuation": cas_scheme_val,
                "CASXIRR": cas_x_val * 100 if cas_x_val is not None else None,
                "LiveXIRRExclTax": live_x_notax * 100 if live_x_notax is not None else None,
                "CASXIRRExclTax": cas_x_notax * 100 if cas_x_notax is not None else None,
                "PAN": pan,
                "Category": category,
                "NetInvested": scheme_net_invested,
                "AbsoluteReturn": scheme_abs_return
            }
            fund_xirrs.append(fund_data)
            family_breakdown[pan]["Categories"][category]["Funds"].append(fund_data)
            
            if cas_scheme_val > 0 and cas_x_val is not None:
                valid_funds.append({"CASValuation": cas_scheme_val, "CASXIRR": cas_x_val})

        # Calculate True XIRR at Category and PAN levels
        for pan, pan_data in family_breakdown.items():
            pan_txns = transactions_df[transactions_df['PAN'] == pan] if 'PAN' in transactions_df.columns else pd.DataFrame()
            pan_val = 0.0
            cas_pan_val = 0.0
            
            for category, cat_data in pan_data["Categories"].items():
                cat_txns = pan_txns[pan_txns['Category'] == category] if 'Category' in pan_txns.columns else pd.DataFrame()
                cat_val = sum(f["LiveValuation"] for f in cat_data["Funds"] if f["LiveValuation"])
                cas_cat_val = sum(f["CASValuation"] for f in cat_data["Funds"] if f["CASValuation"])
                cat_data["Valuation"] = cat_val
                cat_data["CASValuation"] = cas_cat_val
                pan_val += cat_val
                cas_pan_val += cas_cat_val
                
                # Category True XIRR — switches kept: a cross-category switch is
                # a real flow in/out of this category
                cat_true_xirr, _ = calculate_xirr(cat_txns, cat_val, live_date)
                cat_data["TrueXIRR"] = cat_true_xirr * 100 if cat_true_xirr is not None else None

            pan_data["Valuation"] = pan_val
            pan_data["CASValuation"] = cas_pan_val
            
            # PAN True XIRR — pooled, switches are internal within a PAN
            pan_true_xirr, _ = calculate_xirr(pan_txns, pan_val, live_date, include_switches=False)
            pan_data["TrueXIRR"] = pan_true_xirr * 100 if pan_true_xirr is not None else None

            pan_true_xirr_notax, _ = calculate_xirr(pan_txns, pan_val, live_date, include_switches=False, include_taxes=False)
            pan_data["TrueXIRRExclTax"] = pan_true_xirr_notax * 100 if pan_true_xirr_notax is not None else None

            # PAN CAS XIRR
            pan_cas_xirr, _ = calculate_xirr(pan_txns, cas_pan_val, latest_val_date, include_switches=False)
            pan_data["CASXIRR"] = pan_cas_xirr * 100 if pan_cas_xirr is not None else None

            pan_cas_xirr_notax, _ = calculate_xirr(pan_txns, cas_pan_val, latest_val_date, include_switches=False, include_taxes=False)
            pan_data["CASXIRRExclTax"] = pan_cas_xirr_notax * 100 if pan_cas_xirr_notax is not None else None

            # Additions and Withdrawals for PAN — out-of-pocket, switches internal
            pan_invested, pan_withdrawals = compute_invested_withdrawn(pan_txns)

            pan_data["TotalInvested"] = pan_invested
            pan_data["TotalWithdrawals"] = pan_withdrawals
            pan_data["NetInvested"] = pan_invested - pan_withdrawals
            pan_data["TotalSchemes"] = len(set(pan_txns['Scheme'])) if 'Scheme' in pan_txns.columns else 0
            
            pan_abs_return = 0.0
            if pan_data["NetInvested"] > 0:
                pan_abs_return = ((pan_val - pan_data["NetInvested"]) / pan_data["NetInvested"]) * 100
            pan_data["AbsoluteReturn"] = pan_abs_return

        # 6. Trend calculation moved to /api/trend endpoint

        # 8. Total Additions & Withdrawals — out-of-pocket, switches internal
        total_invested, total_withdrawals = compute_invested_withdrawn(transactions_df)
        net_invested = total_invested - total_withdrawals

        absolute_return = 0.0
        if net_invested > 0:
            absolute_return = ((live_total_value - net_invested) / net_invested) * 100

        # Simple CAGR: (CurrentValue / NetInvested)^(1/years) - 1
        # Uses first transaction date as start, today as end.
        # Different from XIRR — ignores WHEN money was invested.
        simple_cagr = None
        try:
            first_date = pd.to_datetime(transactions_df['Date'].min())
            holding_years = (pd.Timestamp(live_date) - first_date).days / 365.25
            if net_invested > 0 and live_total_value > 0 and holding_years > 0:
                simple_cagr = ((live_total_value / net_invested) ** (1 / holding_years) - 1) * 100
        except Exception:
            pass

        transactions_df['Date'] = transactions_df['Date'].dt.strftime("%Y-%m-%d")

        return sanitize_json({
            "status": "success",
            "casDate": latest_val_date.strftime("%Y-%m-%d") if latest_val_date else None,
            "liveTotalValue": live_total_value,
            "liveXirr": live_xirr_val * 100 if live_xirr_val is not None else None,
            "casTotalValue": total_value,
            "casXirr": cas_xirr_val * 100 if cas_xirr_val is not None else None,
            "exclTax": {
                "liveXirr": live_xirr_notax * 100 if live_xirr_notax is not None else None,
                "casXirr": cas_xirr_notax * 100 if cas_xirr_notax is not None else None,
            },
            "totalInvested": total_invested,
            "totalWithdrawals": total_withdrawals,
            "netInvested": net_invested,
            "absoluteReturn": absolute_return,
            "simpleCagr": simple_cagr,
            "fundWise": fund_xirrs,
            "familyBreakdown": family_breakdown,
            "dataWarnings": data_warnings,
            "unresolvedFunds": unresolved_funds,
            "transactions": transactions_df.fillna("").to_dict(orient="records"),
            "holdings": holdings_df.fillna("").to_dict(orient="records")
        })
    except Exception as e:
        logger.error(f"Error parsing CAS: {traceback.format_exc()}")
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        if cas_tmp_path and os.path.exists(cas_tmp_path):
            try: os.remove(cas_tmp_path)
            except: pass

@app.post("/api/export-cas")
async def export_cas(
    cas_file: UploadFile = File(...),
    cas_password: str = Form(...)
):
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            content = await cas_file.read()
            tmp.write(content)
            tmp_path = tmp.name

        parsed_data = parse_cas(tmp_path, cas_password)
        transactions_df = extract_transactions(parsed_data)
        total_value, holdings_df, latest_val_date = get_current_valuation(parsed_data)
        cas_xirr_val, cf_records_df = calculate_xirr(transactions_df, total_value, latest_val_date, include_switches=False)

        buffer = generate_cas_excel(cf_records_df, transactions_df, holdings_df, cas_xirr_val, latest_val_date, calculate_xirr)
        buffer.seek(0)
        
        return StreamingResponse(
            buffer, 
            media_type="application/vnd.ms-excel",
            headers={"Content-Disposition": "attachment; filename=CAS_XIRR_Cash_Flows.xlsx"}
        )
    except Exception as e:
        logger.error(f"Error exporting CAS: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try: os.remove(tmp_path)
            except: pass

@app.post("/api/benchmark-audit")
async def benchmark_audit(
    cas_file: UploadFile = File(...),
    cas_password: str = Form(...),
    benchmark_mapping: str = Form(...) # JSON string e.g. {"Scheme A": "NIFTY 50"}
):
    cas_tmp_path = None
    try:
        if not NSETRI:
            raise Exception("BharatFinTrack package not installed.")
            
        mapping = json.loads(benchmark_mapping)
        
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            content = await cas_file.read()
            tmp.write(content)
            cas_tmp_path = tmp.name

        parsed_data = parse_cas(cas_tmp_path, cas_password)
        transactions_df = extract_transactions(parsed_data)
        
        # Pre-fetch NAVs
        import concurrent.futures
        amfi_codes = set(transactions_df['AMFI'].dropna().unique())
        valid_amfis = [str(a).strip() for a in amfi_codes if str(a).strip()]
        
        if valid_amfis:
            def preload_nav(amfi):
                fetch_historical_nav(amfi)
            with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
                executor.map(preload_nav, valid_amfis)

        unique_benchmarks = set(mapping.values())
        unique_benchmarks = [b for b in unique_benchmarks if b and str(b).strip()]
        
        nse_tri = NSETRI()
        start_date = "01-Jan-2001"
        end_date = datetime.now().strftime("%d-%b-%Y")
        
        benchmark_dfs = {}
        for b_name in unique_benchmarks:
            try:
                df_b = nse_tri.download_daily_data(b_name, start_date, end_date)
                if df_b is not None and not df_b.empty:
                    df_b['Date'] = pd.to_datetime(df_b['Date'])
                    df_b.set_index('Date', inplace=True)
                    df_b['Close'] = pd.to_numeric(df_b['Close'], errors='coerce')
                    df_b = df_b.sort_index()
                    benchmark_dfs[b_name] = df_b
            except Exception as e:
                logger.warning(f"Failed to fetch benchmark {b_name}: {e}")

        results = {}
        for scheme, scheme_txns in transactions_df.groupby('Scheme'):
            b_name = mapping.get(scheme)
            if not b_name or b_name not in benchmark_dfs:
                continue
                
            bench_df = benchmark_dfs[b_name]
            
            amfi = scheme_txns['AMFI'].iloc[0]
            if not amfi or not str(amfi).strip():
                continue
                
            navs_dict = {str(amfi).strip(): fetch_historical_nav(str(amfi).strip())}
            
            portfolio_returns, _, _, _ = build_portfolio_history(scheme_txns, navs_dict)
            if portfolio_returns.empty:
                continue
                
            bench_monthly_prices = bench_df['Close'].resample('ME').last().dropna()
            bench_monthly_returns = bench_monthly_prices.pct_change().dropna()
            
            up_cap, down_cap, _ = calculate_capture_ratios(portfolio_returns, bench_monthly_returns)
            
            results[scheme] = {
                "benchmark": b_name,
                "upCapture": up_cap,
                "downCapture": down_cap
            }

        return {
            "status": "success",
            "captureRatios": results
        }
    except Exception as e:
        logger.error(f"Error benchmark audit: {traceback.format_exc()}")
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        if cas_tmp_path and os.path.exists(cas_tmp_path):
            try: os.remove(cas_tmp_path)
            except: pass


@app.post("/api/trend")
async def get_trend(
    cas_file: UploadFile = File(...),
    cas_password: str = Form(...)
):
    """Compute daily XIRR trend separately so /api/analyze returns fast."""
    cas_tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            content = await cas_file.read()
            tmp.write(content)
            cas_tmp_path = tmp.name

        (transactions_df, holdings_df, total_value, latest_val_date,
         live_total_value, live_date, data_warnings, unresolved_funds,
         scheme_amfi_map, valid_amfis) = process_cas_and_valuation(cas_tmp_path, cas_password)

        navs_dict = {}
        for amfi in valid_amfis:
            nav_df = fetch_historical_nav(amfi)
            if nav_df is not None and not nav_df.empty:
                navs_dict[amfi] = nav_df

        # Build date range from first transaction to today
        first_txn_date = pd.to_datetime(transactions_df['Date'].min()).date()
        date_range = pd.date_range(start=first_txn_date, end=live_date, freq='D')

        daily_port_val, skipped_funds = compute_daily_portfolio_value(
            transactions_df, navs_dict, date_range, scheme_amfi_map, holdings_df
        )

        # B1. Reconciliation Guard
        recon_warnings = []
        final_tx_units = transactions_df.groupby('Scheme')['Units'].sum()
        if not holdings_df.empty:
            statement_units = holdings_df.groupby('Scheme')['Units'].sum()
            for scheme, stmt_u in statement_units.items():
                cum_units = final_tx_units.get(scheme, 0.0)
                if stmt_u > 0:
                    diff = abs(cum_units - stmt_u)
                    if (diff / stmt_u) > 0.01:
                        recon_warnings.append(f"{scheme}: Computed units ({cum_units:.3f}) drift from statement units ({stmt_u:.3f})")

        base_cf_df = prepare_base_cashflows(transactions_df, include_switches=False)

        dates_list = list(date_range)
        vals_list = list(daily_port_val.values)

        # B2. Trend Downsampling
        from datetime import timedelta
        cutoff_date = live_date - timedelta(days=180)
        cas_date_obj = latest_val_date if latest_val_date else live_date
        cas_window_start = cas_date_obj - timedelta(days=10)
        cas_window_end = cas_date_obj + timedelta(days=10)

        downsampled_dates = []
        downsampled_vals = []
        for d, val in zip(dates_list, vals_list):
            d_date = d.date()
            if d_date >= cutoff_date:
                downsampled_dates.append(d)
                downsampled_vals.append(val)
            elif cas_window_start <= d_date <= cas_window_end:
                downsampled_dates.append(d)
                downsampled_vals.append(val)
            elif d.weekday() == 4: # Friday
                downsampled_dates.append(d)
                downsampled_vals.append(val)

        def _xirr_for_day(args):
            d, val = args
            d_obj = d.date()
            x = calculate_xirr_fast(base_cf_df, float(val), d_obj)
            if x is None or not math.isfinite(x):
                return None
            xirr_pct = x * 100
            if not math.isfinite(xirr_pct) or abs(xirr_pct) > 300:
                return None
            return {
                "date": d_obj.strftime("%Y-%m-%d"),
                "xirr": xirr_pct,
                "portfolioValue": float(val),
            }

        trend = []
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
            for result in ex.map(_xirr_for_day, zip(downsampled_dates, downsampled_vals)):
                if result is not None:
                    trend.append(result)

        # Sort by date (parallel results arrive in order, but be safe)
        trend.sort(key=lambda r: r["date"])

        return sanitize_json({"trend": trend, "trendExcludedFunds": skipped_funds, "reconWarnings": recon_warnings})

    except Exception as e:
        logger.error(f"Error computing trend: {traceback.format_exc()}")
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        if cas_tmp_path and os.path.exists(cas_tmp_path):
            try: os.remove(cas_tmp_path)
            except: pass
