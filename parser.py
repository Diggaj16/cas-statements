import casparser
from io import BytesIO
import pandas as pd
from mf_api import normalize_amfi, resolve_amfi_from_isin

def parse_cas(file_obj, password):
    import json
    # file_obj is a BytesIO object from streamlit
    try:
        data = casparser.read_cas_pdf(file_obj, password)
        if hasattr(data, 'model_dump_json'):
            return json.loads(data.model_dump_json())
        elif hasattr(data, 'json'):
            return json.loads(data.json())
        return data
    except Exception as e:
        raise ValueError(f"Failed to parse CAS: {e}")

def safe_float(val):
    if val is None:
        return 0.0
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, str):
        val = val.replace(',', '')
        try:
            return float(val)
        except ValueError:
            return 0.0
    return 0.0

def extract_transactions(parsed_data):
    # Extract transactions into a flat pandas dataframe for easier processing
    transactions = []
    for folio in parsed_data.get('folios', []):
        pan = folio.get('PAN', 'Unknown PAN')
        if not pan or str(pan).strip().lower() == 'not available':
            pan = 'Unknown PAN'
            
        for scheme in folio.get('schemes', []):
            scheme_name = scheme.get('scheme', 'Unknown Scheme')
            # casparser hands back None (-> "None") for schemes its bundled map
            # can't resolve; normalize that to '' and recover via the ISIN it did
            # capture, so newer funds still get NAVs (and thus a real cost basis).
            amfi_code = normalize_amfi(scheme.get('amfi'))
            isin = str(scheme.get('isin') or '').strip()
            if not amfi_code and isin:
                amfi_code = resolve_amfi_from_isin(isin)
            category = scheme.get('type', 'Unknown Category')
            
            # Try to get statement period start
            stmt_period = parsed_data.get('statement_period', {})
            start_date_str = stmt_period.get('from', stmt_period.get('from_'))
            
            scheme_txns = scheme.get('transactions', [])
            for txn in scheme_txns:
                txn_type = txn.get('type')
                desc = str(txn.get('description', '')).upper()
                
                # Fallback classification for transactions that casparser couldn't identify
                if not txn_type or str(txn_type).upper() in ('UNKNOWN', 'MISC', 'NONE', ''):
                    if 'SWITCH IN' in desc or 'STP IN' in desc:
                        txn_type = 'SWITCH_IN'
                    elif 'SWITCH OUT' in desc or 'STP OUT' in desc:
                        txn_type = 'SWITCH_OUT'
                    elif 'PURCHASE' in desc or 'SIP' in desc or 'INVESTMENT' in desc:
                        txn_type = 'PURCHASE'
                    elif 'REDEMPTION' in desc or 'SWP' in desc or 'WITHDRAWAL' in desc:
                        txn_type = 'REDEMPTION'
                    elif 'DIVIDEND' in desc and 'REINVEST' in desc:
                        txn_type = 'DIVIDEND_REINVEST'
                    elif 'DIVIDEND' in desc:
                        txn_type = 'DIVIDEND_PAYOUT'

                transactions.append({
                    'Date': txn.get('date'),
                    'Amount': safe_float(txn.get('amount')),
                    'Units': safe_float(txn.get('units')),
                    'NAV': safe_float(txn.get('nav')),
                    'Description': txn.get('description'),
                    'Scheme': scheme_name,
                    'AMFI': amfi_code,
                    'ISIN': isin,
                    'Type': txn_type,
                    'PAN': pan,
                    'Category': category
                })

            open_units = safe_float(scheme.get('open', 0.0))
            if open_units > 0:
                earliest_date = start_date_str
                if scheme_txns and earliest_date is None:
                    earliest_date = scheme_txns[0].get('date')
                
                # If we still have no date, fallback to something reasonable, but usually CAS has from_ date
                
                transactions.append({
                    'Date': earliest_date,
                    'Amount': 0.0, # Placeholder, will calculate in app.py
                    'Units': open_units,
                    'NAV': 0.0,
                    'Description': 'Opening Balance',
                    'Scheme': scheme_name,
                    'AMFI': amfi_code,
                    'ISIN': isin,
                    'Type': 'OPENING_BALANCE',
                    'PAN': pan,
                    'Category': category
                })
    df = pd.DataFrame(transactions)
    if not df.empty:
        df['Date'] = pd.to_datetime(df['Date'])
    return df

def get_current_valuation(parsed_data):
    # Returns the total valuation, a dataframe of current holdings, and the latest valuation date
    holdings = []
    total_value = 0.0
    latest_date = None
    for folio in parsed_data.get('folios', []):
        pan = folio.get('PAN', 'Unknown PAN')
        if not pan or str(pan).strip().lower() == 'not available':
            pan = 'Unknown PAN'
            
        for scheme in folio.get('schemes', []):
            scheme_name = scheme.get('scheme', 'Unknown Scheme')
            category = scheme.get('type', 'Unknown Category')
            valuation = scheme.get('valuation', {})
            value = safe_float(valuation.get('value', 0.0))
            total_value += value
            
            val_date = valuation.get('date')
            if val_date:
                try:
                    dt = pd.to_datetime(val_date).date()
                    if latest_date is None or dt > latest_date:
                        latest_date = dt
                except:
                    pass
                    
            units = safe_float(scheme.get('close', scheme.get('close_calculated', 0.0)))
            if units == 0.0 and value > 0 and safe_float(valuation.get('nav', 0.0)) > 0:
                units = value / safe_float(valuation.get('nav'))
                
            holdings.append({
                'Scheme': scheme_name,
                'Units': units,
                'NAV': safe_float(valuation.get('nav', 0.0)),
                'Value': value,
                'Date': val_date,
                'PAN': pan,
                'Category': category
            })
    return total_value, pd.DataFrame(holdings), latest_date
