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
from metrics import calculate_xirr, calculate_xirr_legacy, calculate_xirr_fast, prepare_base_cashflows, build_portfolio_history, calculate_capture_ratios, compute_invested_withdrawn
from mf_api import fetch_historical_nav
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

@app.post("/api/analyze")
async def analyze_cas(
    cas_file: UploadFile = File(...),
    cas_password: str = Form(...)
):
    cas_tmp_path = None
    try:
        # 1. Parse CAS
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            content = await cas_file.read()
            tmp.write(content)
            cas_tmp_path = tmp.name

        parsed_data = parse_cas(cas_tmp_path, cas_password)
        transactions_df = extract_transactions(parsed_data)
        
        # 2. Pre-fetch NAVs
        import concurrent.futures
        amfi_codes = set(transactions_df['AMFI'].dropna().unique())
        valid_amfis = [str(a).strip() for a in amfi_codes if str(a).strip()]
        
        if valid_amfis:
            def preload_nav(amfi):
                fetch_historical_nav(amfi)
            with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
                executor.map(preload_nav, valid_amfis)

        # Schemes whose cost basis / valuation can't be trusted because no NAV
        # could be resolved — these are exactly the funds that drift from
        # Investwell, so we surface them to the user instead of failing silently.
        data_warnings = []

        # 3. Fix opening balances
        opening_mask = transactions_df['Type'] == 'OPENING_BALANCE'
        if opening_mask.any():
            for idx, row in transactions_df[opening_mask].iterrows():
                amfi = row['AMFI']
                open_date = row['Date']
                units = row['Units']
                if amfi and str(amfi).strip() and pd.notna(open_date):
                    nav_df = fetch_historical_nav(amfi)
                    if nav_df is not None and not nav_df.empty:
                        try:
                            dt = pd.to_datetime(open_date)
                            navs_until = nav_df.loc[:dt]['nav']
                            # If NAV history starts after the opening date, fall back to
                            # the earliest available NAV. Leaving Amount at 0 would drop
                            # the opening outflow from XIRR while its units still count
                            # in the valuation, wildly inflating returns.
                            closest_nav = navs_until.iloc[-1] if not navs_until.empty else nav_df['nav'].iloc[0]
                            transactions_df.at[idx, 'Amount'] = float(units * closest_nav)
                            transactions_df.at[idx, 'NAV'] = float(closest_nav)
                        except Exception as e:
                            logger.warning(f"Failed to fix opening balance for {amfi}: {e}")
                    else:
                        logger.warning(f"No NAV data for {amfi}; opening balance has no cost and XIRR may be overstated")
                        data_warnings.append(
                            f"{row['Scheme']}: no NAV found (AMFI '{amfi}'), opening-balance cost is missing — XIRR for this fund is overstated."
                        )
                elif pd.notna(open_date):
                    # Opening units carried forward but the fund has no resolvable
                    # AMFI/ISIN at all — its cost basis stays 0 and skews XIRR.
                    data_warnings.append(
                        f"{row['Scheme']}: no AMFI code (could not resolve from ISIN), opening-balance cost is missing — XIRR for this fund is overstated."
                    )

        total_value, holdings_df, latest_val_date = get_current_valuation(parsed_data)

        # 4. Live NAV calculation
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

        # 5. Core XIRR — pooled portfolio level, so switches are internal transfers
        live_xirr_val, _ = calculate_xirr(transactions_df, live_total_value, live_date, include_switches=False)
        cas_xirr_val, _ = calculate_xirr(transactions_df, total_value, latest_val_date, include_switches=False)
        legacy_xirr_val = calculate_xirr_legacy(transactions_df, total_value, latest_val_date)

        # Investwell-style parity set: stamp duty/STT/TDS lines excluded
        live_xirr_notax, _ = calculate_xirr(transactions_df, live_total_value, live_date, include_switches=False, include_taxes=False)
        cas_xirr_notax, _ = calculate_xirr(transactions_df, total_value, latest_val_date, include_switches=False, include_taxes=False)

        fund_xirrs = []
        valid_funds = []
        
        # We will build a structured dictionary for the new Family & Category tab
        # Structure: { PAN: { "Valuation": 0, "TrueXIRR": 0, "WeightedXIRR": 0, "Categories": { CATEGORY: { "Valuation": 0, "TrueXIRR": 0, "WeightedXIRR": 0, "Funds": [] } } } }
        family_breakdown = {}
        
        for scheme, scheme_txns in transactions_df.groupby('Scheme'):
            val_row = holdings_df[holdings_df['Scheme'] == scheme]
            live_scheme_val = val_row['Live Value'].sum() if not val_row.empty and 'Live Value' in val_row.columns else 0.0
            cas_scheme_val = val_row['Value'].sum() if not val_row.empty else 0.0
            
            pan = scheme_txns['PAN'].iloc[0] if 'PAN' in scheme_txns.columns else 'Unknown PAN'
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
            legacy_val = calculate_xirr_legacy(scheme_txns, cas_scheme_val, latest_val_date)
            live_x_notax = calculate_xirr(scheme_txns, live_scheme_val, live_date, include_taxes=False)[0]
            cas_x_notax = calculate_xirr(scheme_txns, cas_scheme_val, latest_val_date, include_taxes=False)[0]

            fund_data = {
                "Scheme": scheme,
                "AMFI": scheme_amfi_map.get(scheme) or None,
                "LiveValuation": live_scheme_val,
                "LiveXIRR": live_x_val * 100 if live_x_val is not None else None,
                "CASValuation": cas_scheme_val,
                "CASXIRR": cas_x_val * 100 if cas_x_val is not None else None,
                "LegacyXIRR": legacy_val * 100 if legacy_val is not None else None,
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

            # PAN CAS XIRR
            pan_cas_xirr, _ = calculate_xirr(pan_txns, cas_pan_val, latest_val_date, include_switches=False)
            pan_data["CASXIRR"] = pan_cas_xirr * 100 if pan_cas_xirr is not None else None

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

        # 6. Daily XIRR trend — portfolio only, parallelized
        trend = []
        if not holdings_df.empty:
            first_txn_date = pd.to_datetime(transactions_df['Date'].min()).date()
            date_range = pd.date_range(start=first_txn_date, end=live_date, freq='D')

            df_navs = pd.DataFrame(index=date_range)
            for amfi in valid_amfis:
                nav_df = fetch_historical_nav(amfi)
                if nav_df is not None and not nav_df.empty:
                    df_navs[amfi] = nav_df['nav'].reindex(date_range, method='ffill').bfill()

            daily_port_val = pd.Series(0.0, index=date_range)
            for _, row in holdings_df.iterrows():
                amfi = scheme_amfi_map.get(row['Scheme'])
                units = row['Units']
                if amfi and amfi in df_navs.columns:
                    daily_port_val += df_navs[amfi] * units
                else:
                    daily_port_val += row.get('Value', 0.0)

            base_cf_df = prepare_base_cashflows(transactions_df, include_switches=False)
            dates_list = list(date_range)
            vals_list = list(daily_port_val.values)

            def _xirr_for_day(args):
                d, val = args
                d_obj = d.date()
                x = calculate_xirr_fast(base_cf_df, float(val), d_obj)
                if x is None or not math.isfinite(x):
                    return None
                xirr_pct = x * 100
                # Annualized XIRR explodes on near-zero-duration early days; drop the noise.
                if not math.isfinite(xirr_pct) or abs(xirr_pct) > 1000:
                    return None
                return {"date": d_obj.strftime("%Y-%m-%d"), "xirr": xirr_pct, "portfolioValue": float(val)}

            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
                results = list(ex.map(_xirr_for_day, zip(dates_list, vals_list)))
            trend = sorted([r for r in results if r is not None], key=lambda r: r["date"])

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
            "legacyXirr": legacy_xirr_val * 100 if legacy_xirr_val is not None else None,
            "liveWeightedXirr": live_weighted_xirr_val * 100 if live_weighted_xirr_val is not None else None,
            "casWeightedXirr": cas_weighted_xirr_val * 100 if cas_weighted_xirr_val is not None else None,
            "weightedXirr": cas_weighted_xirr_val * 100 if cas_weighted_xirr_val is not None else None,
            "exclTax": {
                "liveXirr": live_xirr_notax * 100 if live_xirr_notax is not None else None,
                "casXirr": cas_xirr_notax * 100 if cas_xirr_notax is not None else None,
                "liveWeightedXirr": live_weighted_notax * 100 if live_weighted_notax is not None else None,
                "casWeightedXirr": cas_weighted_notax * 100 if cas_weighted_notax is not None else None,
            },
            "totalInvested": total_invested,
            "totalWithdrawals": total_withdrawals,
            "netInvested": net_invested,
            "absoluteReturn": absolute_return,
            "simpleCagr": simple_cagr,
            "fundWise": fund_xirrs,
            "familyBreakdown": family_breakdown,
            "dataWarnings": data_warnings,
            "trend": trend,
            "weightedTrend": weighted_trend,
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

        parsed_data = parse_cas(cas_tmp_path, cas_password)
        transactions_df = extract_transactions(parsed_data)
        _, holdings_df, latest_val_date = get_current_valuation(parsed_data)

        import concurrent.futures

        amfi_codes = set(transactions_df['AMFI'].dropna().unique())
        valid_amfis = [str(a).strip() for a in amfi_codes if str(a).strip()]

        # NAVs are already cached from /api/analyze — this is fast
        if valid_amfis:
            with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
                ex.map(fetch_historical_nav, valid_amfis)

        # Fix opening balance amounts so base cashflows are correct
        opening_mask = transactions_df['Type'] == 'OPENING_BALANCE'
        if opening_mask.any():
            for idx, row in transactions_df[opening_mask].iterrows():
                amfi = row['AMFI']
                open_date = row['Date']
                units = row['Units']
                if amfi and str(amfi).strip() and pd.notna(open_date):
                    nav_df = fetch_historical_nav(amfi)
                    if nav_df is not None and not nav_df.empty:
                        try:
                            dt = pd.to_datetime(open_date)
                            navs_until = nav_df.loc[:dt]['nav']
                            closest_nav = navs_until.iloc[-1] if not navs_until.empty else nav_df['nav'].iloc[0]
                            transactions_df.at[idx, 'Amount'] = float(units * closest_nav)
                        except Exception as e:
                            logger.warning(f"Trend: opening balance fix failed for {amfi}: {e}")

        live_date = datetime.now().date()
        scheme_amfi_map = (
            transactions_df[['Scheme', 'AMFI']]
            .drop_duplicates()
            .set_index('Scheme')['AMFI']
            .to_dict()
        )

        # Build date range from first transaction to today
        first_txn_date = pd.to_datetime(transactions_df['Date'].min()).date()
        date_range = pd.date_range(start=first_txn_date, end=live_date, freq='D')

        # Build NAV grid (forward-filled for weekends/holidays)
        df_navs = pd.DataFrame(index=date_range)
        for amfi in valid_amfis:
            nav_df = fetch_historical_nav(amfi)
            if nav_df is not None and not nav_df.empty:
                df_navs[amfi] = nav_df['nav'].reindex(date_range, method='ffill').bfill()

        # Daily portfolio value using current holdings × historical NAV
        daily_port_val = pd.Series(0.0, index=date_range)
        for _, row in holdings_df.iterrows():
            amfi = scheme_amfi_map.get(row['Scheme'])
            units = row['Units']
            if amfi and amfi in df_navs.columns:
                daily_port_val += df_navs[amfi] * units
            else:
                daily_port_val += row.get('Value', 0.0)

        base_cf_df = prepare_base_cashflows(transactions_df, include_switches=False)

        dates_list = list(date_range)
        vals_list = list(daily_port_val.values)

        def _xirr_for_day(args):
            d, val = args
            d_obj = d.date()
            x = calculate_xirr_fast(base_cf_df, float(val), d_obj)
            if x is None or not math.isfinite(x):
                return None
            xirr_pct = x * 100
            if not math.isfinite(xirr_pct) or abs(xirr_pct) > 1000:
                return None
            return {
                "date": d_obj.strftime("%Y-%m-%d"),
                "xirr": xirr_pct,
                "portfolioValue": float(val),
            }

        trend = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
            for result in ex.map(_xirr_for_day, zip(dates_list, vals_list)):
                if result is not None:
                    trend.append(result)

        # Sort by date (parallel results arrive in order, but be safe)
        trend.sort(key=lambda r: r["date"])

        return sanitize_json({"trend": trend, "weightedTrend": []})

    except Exception as e:
        logger.error(f"Error computing trend: {traceback.format_exc()}")
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        if cas_tmp_path and os.path.exists(cas_tmp_path):
            try: os.remove(cas_tmp_path)
            except: pass
