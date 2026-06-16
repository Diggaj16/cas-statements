import pandas as pd
import io

csv_content = """NGEN Markets Index Data:,,,
,,,
Date,NIFTY 100,Nifty 50,,
01-01-2023,100,50,,
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

def load_benchmark(f):
    header_idx = 0
    if f.name.endswith(('.xls', '.xlsx')):
        pass
    else:
        lines = f.getvalue().decode('utf-8', errors='ignore').splitlines()
        for i, line in enumerate(lines[:20]):
            if 'date' in line.lower():
                header_idx = i
                break
    
    f.seek(0)
    # in streamlit, pd.read_csv(f) takes the file-like object
    df = pd.read_csv(f.io, header=header_idx)
    df = df.loc[:, ~df.columns.str.contains('^Unnamed')]
    df = df.dropna(how='all')
    return df

bench_df = load_benchmark(f)
print(bench_df.columns)
