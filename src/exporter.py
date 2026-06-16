import pandas as pd
import io

def generate_cas_excel(cf_records_df, transactions_df, holdings_df, cas_xirr_val, latest_val_date, calculate_xirr):
    """
    Generates the detailed multi-sheet Excel file containing mathematical cash flows 
    and raw transactions used for XIRR calculation.
    """
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
        # 1. Prepare and sort DataFrames ascending by Date
        cf_sorted = cf_records_df.sort_values(by='Date')
        txn_sorted = transactions_df.sort_values(by='Date')
        
        # 2. Write master sheets
        master_cf_out = cf_sorted.copy()
        master_cf_out.loc[len(master_cf_out)] = pd.Series(dtype='object') # Blank row
        if cas_xirr_val is not None:
            master_cf_out.loc[len(master_cf_out)] = {'Date': 'OVERALL XIRR', 'Amount': f"{cas_xirr_val*100:.2f}%", 'Scheme': '', 'Type': '', 'Description': ''}
        
        master_cf_out.to_excel(writer, sheet_name='All Cash Flows', index=False)
        txn_sorted.to_excel(writer, sheet_name='All Raw Transactions', index=False)
        
        # 3. Create individual sheets per Scheme for XIRR Cash Flows
        if not holdings_df.empty:
            for _, row in holdings_df.iterrows():
                scheme = row['Scheme']
                scheme_val = row.get('Value', 0.0)
                
                fund_txns = transactions_df[transactions_df['Scheme'] == scheme].copy()
                fund_xirr, fund_cf_df = calculate_xirr(fund_txns, scheme_val, latest_val_date)
                
                if fund_cf_df is not None and not fund_cf_df.empty:
                    fund_cf_out = fund_cf_df.sort_values(by='Date').copy()
                    fund_cf_out.loc[len(fund_cf_out)] = pd.Series(dtype='object')
                    if fund_xirr is not None:
                        fund_cf_out.loc[len(fund_cf_out)] = {'Date': 'FUND XIRR', 'Amount': f"{fund_xirr*100:.2f}%", 'Scheme': scheme, 'Type': '', 'Description': ''}
                    
                    safe_name = str(scheme).replace(':', '').replace('\\', '').replace('/', '').replace('?', '').replace('*', '').replace('[', '').replace(']', '').strip()
                    cf_sheet_name = f"CF - {safe_name[:26]}"
                    fund_cf_out.to_excel(writer, sheet_name=cf_sheet_name, index=False)
            
        # 4. Create individual sheets per Scheme for Raw Transactions
        for scheme, group in txn_sorted.groupby('Scheme'):
            safe_name = str(scheme).replace(':', '').replace('\\', '').replace('/', '').replace('?', '').replace('*', '').replace('[', '').replace(']', '').strip()
            txn_sheet_name = f"Txn - {safe_name[:25]}"
            group.to_excel(writer, sheet_name=txn_sheet_name, index=False)
            
    return buffer

def generate_audit_excel(cf_records_df, debug_values, daily_scheme_returns, debug_twr, combined_returns):
    """
    Generates the audit Excel file containing debug DataFrames from Time-Weighted Return
    and Capture Ratio calculations.
    """
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
        cf_records_df.to_excel(writer, sheet_name='XIRR Cash Flows', index=False)
        debug_values.to_excel(writer, sheet_name='Daily Portfolio Valuations')
        daily_scheme_returns.to_excel(writer, sheet_name='Daily Scheme Returns')
        debug_twr.to_excel(writer, sheet_name='TWR Math & Wealth Index')
        combined_returns.to_excel(writer, sheet_name='Final Monthly Returns')
    return buffer
