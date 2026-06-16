import { useState } from 'react';
import { Target, ArrowRight } from 'lucide-react';

interface BenchmarkMapperProps {
  funds: any[];
  onRunAudit: (mapping: Record<string, string>) => void;
  isLoading: boolean;
}

const COMMON_BENCHMARKS = [
  "NIFTY 50",
  "NIFTY 100",
  "NIFTY 500",
  "NIFTY MIDCAP 150",
  "NIFTY SMALLCAP 250",
  "NIFTY LARGEMIDCAP 250"
];

export default function BenchmarkMapper({ funds, onRunAudit, isLoading }: BenchmarkMapperProps) {
  const [mapping, setMapping] = useState<Record<string, string>>({});

  const handleChange = (scheme: string, benchmark: string) => {
    setMapping(prev => ({
      ...prev,
      [scheme]: benchmark
    }));
  };

  const handleRun = () => {
    // Only pass funds that actually have a mapping
    const cleanMapping = Object.fromEntries(
      Object.entries(mapping).filter(([_, val]) => val.trim() !== '')
    );
    onRunAudit(cleanMapping);
  };

  return (
    <div className="glass-card p-6 md:p-8 mt-10">
      <div className="flex items-center gap-3 mb-6">
        <Target className="text-vine-peach w-6 h-6" />
        <h2 className="text-xl font-bold">Dynamic Benchmark Mapping</h2>
      </div>
      <p className="text-gray-400 mb-6 text-sm">
        Map your mutual funds to NSE indices (e.g., NIFTY 50). We will dynamically fetch the historical TRI data from BharatFinTrack and compute Up/Down Capture Ratios for each fund.
      </p>

      <div className="space-y-4 max-h-96 overflow-y-auto pr-2 mb-6">
        {funds.map((fund, idx) => (
          <div key={idx} className="flex flex-col md:flex-row md:items-center justify-between gap-4 p-4 border border-white/5 rounded-lg bg-black/20">
            <span className="font-medium text-sm truncate max-w-sm" title={fund.Scheme}>{fund.Scheme}</span>
            <input 
              type="text" 
              list="benchmarks"
              placeholder="e.g. NIFTY 50"
              value={mapping[fund.Scheme] || ''}
              onChange={(e) => handleChange(fund.Scheme, e.target.value)}
              className="bg-black/40 border border-gray-700 rounded-md px-3 py-2 text-sm text-white focus:outline-none focus:border-vine-peach focus:ring-1 focus:ring-vine-peach w-full md:w-64"
            />
          </div>
        ))}
        
        <datalist id="benchmarks">
          {COMMON_BENCHMARKS.map(b => (
            <option key={b} value={b} />
          ))}
        </datalist>
      </div>

      <div className="flex justify-end">
        <button 
          onClick={handleRun}
          disabled={isLoading || Object.keys(mapping).length === 0}
          className={`flex items-center gap-2 px-6 py-3 rounded-lg font-medium transition-all ${
            !isLoading && Object.keys(mapping).length > 0
              ? 'bg-vine-peach text-black hover:bg-[#e0a47f] bevel-emboss'
              : 'bg-gray-800 text-gray-500 cursor-not-allowed'
          }`}
        >
          {isLoading ? 'Fetching from NSE...' : 'Run Benchmark Audit'}
          {!isLoading && <ArrowRight className="w-4 h-4" />}
        </button>
      </div>
    </div>
  );
}
