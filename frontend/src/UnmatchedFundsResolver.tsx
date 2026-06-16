import { useState, useEffect, useRef } from 'react';
import { AlertTriangle, Search, Check, ArrowRight } from 'lucide-react';

interface UnresolvedFund {
  scheme: string;
  isin?: string;
  pan?: string;
  category?: string;
}

interface SchemeResult {
  code: string;
  name: string;
}

interface Props {
  unresolvedFunds: UnresolvedFund[];
  onApply: (mapping: { scheme: string; isin?: string; code: string; name: string }[]) => void;
  isLoading: boolean;
}

// One search-box row per unmatched fund. Queries the backend mfapi proxy and
// lets the user pick the exact scheme (code + plan/option) for that fund.
function FundRow({
  fund,
  chosen,
  onChoose,
}: {
  fund: UnresolvedFund;
  chosen?: SchemeResult;
  onChoose: (r: SchemeResult) => void;
}) {
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<SchemeResult[]>([]);
  const [open, setOpen] = useState(false);
  const [searching, setSearching] = useState(false);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    if (query.trim().length < 3) {
      setResults([]);
      return;
    }
    debounceRef.current = setTimeout(async () => {
      setSearching(true);
      setOpen(true);
      try {
        const resp = await fetch(`http://localhost:8000/api/search-scheme?q=${encodeURIComponent(query)}`);
        const data = await resp.json();
        setResults(data.results || []);
      } catch {
        setResults([]);
      } finally {
        setSearching(false);
      }
    }, 350);
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, [query]);

  return (
    <div className="flex flex-col md:flex-row md:items-start justify-between gap-3 p-4 border border-white/5 rounded-lg bg-black/20">
      <div className="min-w-0 md:max-w-sm">
        <p className="font-medium text-sm truncate" title={fund.scheme}>{fund.scheme}</p>
        <p className="text-xs text-gray-500 mt-0.5">
          {fund.isin ? `ISIN ${fund.isin}` : 'No ISIN'}{fund.category ? ` · ${fund.category}` : ''}
        </p>
      </div>

      <div className="relative w-full md:w-80">
        {chosen ? (
          <div className="flex items-center justify-between gap-2 bg-vine-mint/10 border border-vine-mint/40 rounded-md px-3 py-2">
            <span className="text-xs text-vine-mint truncate" title={chosen.name}>
              <Check className="inline w-3.5 h-3.5 mr-1" />{chosen.name}
            </span>
            <button
              className="text-xs text-gray-400 hover:text-white shrink-0"
              onClick={() => { onChoose({ code: '', name: '' }); setQuery(''); }}
            >
              change
            </button>
          </div>
        ) : (
          <>
            <div className="flex items-center gap-2 bg-black/40 border border-gray-700 rounded-md px-3 py-2 focus-within:border-vine-indigo">
              <Search className="w-4 h-4 text-gray-500 shrink-0" />
              <input
                type="text"
                placeholder="Search scheme name (e.g. Parag Parikh Flexi Cap Direct Growth)"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                onFocus={() => query.length >= 3 && setOpen(true)}
                className="bg-transparent text-sm text-white w-full focus:outline-none"
              />
            </div>
            {open && query.length >= 3 && (
              <div className="absolute z-20 mt-1 w-full max-h-60 overflow-y-auto bg-[#1D1D1B] border border-gray-700 rounded-md shadow-xl">
                {searching && <div className="px-3 py-2 text-xs text-gray-500">Searching…</div>}
                {!searching && results.length === 0 && (
                  <div className="px-3 py-2 text-xs text-amber-500/80">
                    No mutual funds found. (Note: AIFs and PMS are not supported by AMFI)
                  </div>
                )}
                {results.map((r) => (
                  <button
                    key={r.code}
                    onClick={() => { onChoose(r); setOpen(false); }}
                    className="block w-full text-left px-3 py-2 text-xs text-gray-200 hover:bg-vine-indigo/20 border-b border-gray-800/50 last:border-0"
                    title={r.name}
                  >
                    {r.name} <span className="text-gray-500">({r.code})</span>
                  </button>
                ))}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}

export default function UnmatchedFundsResolver({ unresolvedFunds, onApply, isLoading }: Props) {
  // scheme name -> chosen result
  const [choices, setChoices] = useState<Record<string, SchemeResult>>({});

  const handleChoose = (scheme: string, r: SchemeResult) => {
    setChoices((prev) => {
      const next = { ...prev };
      if (r.code) next[scheme] = r;
      else delete next[scheme];
      return next;
    });
  };

  const handleApply = () => {
    const mapping = unresolvedFunds
      .filter((f) => choices[f.scheme]?.code)
      .map((f) => ({
        scheme: f.scheme,
        isin: f.isin,
        code: choices[f.scheme].code,
        name: choices[f.scheme].name,
      }));
    if (mapping.length) onApply(mapping);
  };

  const mappedCount = Object.keys(choices).length;

  return (
    <div className="rounded-lg border border-amber-500/40 bg-amber-500/5 p-5 mb-8">
      <div className="flex items-center gap-3 mb-2">
        <AlertTriangle className="text-amber-400 w-5 h-5" />
        <h2 className="text-lg font-bold text-amber-200">
          {unresolvedFunds.length} fund{unresolvedFunds.length > 1 ? 's' : ''} couldn't be matched to a scheme
        </h2>
      </div>
      <p className="text-sm text-amber-200/80 mb-5">
        These funds have no AMFI code or recognizable ISIN, so their NAV (and cost basis) can't be fetched —
        their XIRR will be off until matched. Search and pick the exact scheme for each, then re-analyze.
        Your choices are remembered for future uploads.
      </p>

      <div className="space-y-3 max-h-[28rem] overflow-y-auto pr-1 mb-5">
        {unresolvedFunds.map((fund, idx) => (
          <FundRow
            key={`${fund.scheme}-${idx}`}
            fund={fund}
            chosen={choices[fund.scheme]}
            onChoose={(r) => handleChoose(fund.scheme, r)}
          />
        ))}
      </div>

      <div className="flex justify-end">
        <button
          onClick={handleApply}
          disabled={isLoading || mappedCount === 0}
          className={`flex items-center gap-2 px-6 py-3 rounded-lg font-medium transition-all ${
            !isLoading && mappedCount > 0
              ? 'bg-vine-indigo text-white hover:bg-[#8a6ee8]'
              : 'bg-gray-800 text-gray-500 cursor-not-allowed'
          }`}
        >
          {isLoading ? 'Re-analyzing…' : `Apply ${mappedCount || ''} & Re-analyze`}
          {!isLoading && <ArrowRight className="w-4 h-4" />}
        </button>
      </div>
    </div>
  );
}
