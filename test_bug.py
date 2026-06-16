import pandas as pd
import io

csv_content = """NGEN Markets Index Data:
Date,NIFTY 100,Nifty 50
04-Jan-2000,1638.7,1466.42
"""

class DummyFile:
    def __init__(self, content, name):
        self.content = content.encode('utf-8')
        self.name = name
        self.io = io.BytesIO(self.content)
    def getvalue(self):
        return self.content
    def seek(self, pos):
        self.io.seek(pos)
    def read(self, *args):
        return self.io.read(*args)

f = DummyFile(csv_content, "test.csv")

# Simulate the app's reading behavior exactly
lines = f.getvalue().decode('utf-8', errors='ignore').splitlines()
header_idx = 0
for i, line in enumerate(lines[:20]):
    if 'date' in line.lower():
        header_idx = i
        break

print(f"CSV header_idx: {header_idx}")
f.seek(0)
df = pd.read_csv(f.io, header=header_idx)
print("CSV Columns:", df.columns.tolist())

# Now what if the file is Excel?
df_dummy = pd.DataFrame([
    ['NGEN Markets Index Data:', None, None],
    ['Date', 'NIFTY 100', 'Nifty 50'],
    ['04-Jan-2000', 1638.7, 1466.42]
])
df_dummy.to_excel('test_excel.xlsx', index=False, header=False)

df_raw = pd.read_excel('test_excel.xlsx', header=None)
header_idx = 0
for i, row in df_raw.head(20).iterrows():
    row_str = row.astype(str).str.lower()
    if row_str.str.contains('date').any():
        header_idx = i
        break

print(f"Excel header_idx: {header_idx}")
df2 = pd.read_excel('test_excel.xlsx', header=header_idx)
print("Excel Columns:", df2.columns.tolist())

