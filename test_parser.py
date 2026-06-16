import json
from parser import extract_transactions, get_current_valuation

data = {
    "folios": [
        {
            "folio": "123",
            "schemes": [
                {
                    "scheme": "Test Scheme",
                    "amfi": "12345",
                    "close": 100.5,
                    "close_calculated": 100.5,
                    "valuation": {
                        "date": "2023-01-01",
                        "nav": 10.0,
                        "value": 1005.0
                    }
                }
            ]
        }
    ]
}

total_value, holdings_df, latest_date = get_current_valuation(data)
print("Total Value:", total_value)
print("Holdings DF:")
print(holdings_df)

print("Units column type:", holdings_df['Units'].dtype)
