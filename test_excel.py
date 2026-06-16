import pandas as pd
import io

# Create a dummy excel file
df_dummy = pd.DataFrame({
    'col1': ['NGEN Markets Index Data:', '', 'Date', '04-Jan-2000'],
    'col2': ['', '', 'NIFTY 100', 1638.7],
    'col3': ['', '', 'Nifty 50', 1466.42]
})
df_dummy.to_excel('test_excel.xlsx', index=False, header=False)

# Test read_excel with header=None
df_raw = pd.read_excel('test_excel.xlsx', header=None)
print("df_raw with header=None:")
print(df_raw.head())

header_idx = 0
for i, row in df_raw.head(20).iterrows():
    row_str = row.astype(str).str.lower()
    if row_str.str.contains('date').any():
        header_idx = i
        break

print(f"\nheader_idx found: {header_idx}")

df = pd.read_excel('test_excel.xlsx', header=header_idx)
print("\ndf with header=header_idx:")
print(df.head())
print("\ncolumns:")
print(df.columns)
