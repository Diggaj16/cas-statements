import { useState, useMemo } from 'react';
import * as XLSX from 'xlsx';
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from 'recharts';
import MetricCard from './MetricCard';
import BenchmarkMapper from './BenchmarkMapper';
import UnmatchedFundsResolver from './UnmatchedFundsResolver';
import { Download, Table as TableIcon, FileSpreadsheet } from 'lucide-react';

interface DashboardProps {
  data: any;
  onReset: () => void;
  casFile: File;
  casPassword: string;
  onReanalyze: (mapping: { scheme: string; isin?: string; code: string; name: string }[]) => void;
  isReanalyzing: boolean;
}

export default function Dashboard({ data, onReset, casFile, casPassword, onReanalyze, isReanalyzing }: DashboardProps) {
  const { liveTotalValue, liveXirr, casTotalValue, casXirr, totalInvested, totalWithdrawals, netInvested, absoluteReturn, simpleCagr, fundWise, trend, trendLoading, trendExcludedFunds, reconWarnings, holdings, transactions, casDate, dataWarnings, unresolvedFunds } = data;

  const [activeTab, setActiveTab] = useState<'overview' | 'family' | 'cagr' | 'snapshot' | 'data'>('overview');
  const [selectedPan, setSelectedPan] = useState<string>('all');
  const [showExclTax, setShowExclTax] = useState(false);
  const [isAuditing, setIsAuditing] = useState(false);
  const [captureRatios, setCaptureRatios] = useState<Record<string, any> | null>(null);
  const [isDownloadingCas, setIsDownloadingCas] = useState(false);
  const [trendZoom, setTrendZoom] = useState<'all' | '6m' | '3m' | '1m' | '1y' | '2y'>('all');
  const [selectedHistDate, setSelectedHistDate] = useState<string>(casDate || '');

  // Daily trend points (portfolio XIRR + value per day)
  const mergedTrend = useMemo(() => trend ?? [], [trend]);

  // Apply zoom filter
  const filteredTrend = useMemo(() => {
    if (trendZoom === 'all') return mergedTrend;
    const daysMap: Record<string, number> = { '1m': 30, '3m': 90, '6m': 180, '1y': 365, '2y': 730 };
    const days = daysMap[trendZoom] ?? 30;
    const cutoff = new Date(Date.now() - days * 86400000);
    return mergedTrend.filter((t: any) => new Date(t.date) >= cutoff);
  }, [mergedTrend, trendZoom]);

  // For the chart only — in "all" view, skip the leading unstable period
  // where XIRR is inflated by a short holding period. The full mergedTrend
  // is still used for Value-by-Date lookups.
  const XIRR_STABLE_PCT = 200;
  const chartDisplayTrend = useMemo(() => {
    if (trendZoom !== 'all') return filteredTrend;
    const firstStable = filteredTrend.findIndex(
      (t: any) => t.xirr != null && t.xirr <= XIRR_STABLE_PCT
    );
    return firstStable > 0 ? filteredTrend.slice(firstStable) : filteredTrend;
  }, [filteredTrend, trendZoom]);

  // Find the closest available data point for a given date string
  const getSnapshotForDate = (dateStr: string) => {
    if (!mergedTrend.length || !dateStr) return null;
    const target = new Date(dateStr).getTime();
    return mergedTrend.reduce((best: any, t: any) => {
      const diff = Math.abs(new Date(t.date).getTime() - target);
      const bestDiff = Math.abs(new Date(best.date).getTime() - target);
      return diff < bestDiff ? t : best;
    }, mergedTrend[0]);
  };

  const histSnapshot = useMemo(() => getSnapshotForDate(selectedHistDate), [selectedHistDate, mergedTrend]);
  const todaySnapshot = mergedTrend[mergedTrend.length - 1] ?? null;

  const handleDownloadXirrTrend = () => {
    if (!casDate || !mergedTrend.length) return;
    // From 5 days before the CAS statement date to today
    const cutoff = new Date(new Date(casDate).getTime() - 5 * 86400000);
    const rows = mergedTrend
      .filter((t: any) => new Date(t.date) >= cutoff)
      .map((t: any) => ({
        'Date': t.date,
        'True Portfolio XIRR (%)': t.xirr != null ? parseFloat(t.xirr.toFixed(4)) : '',
        'Portfolio Value (₹)': t.portfolioValue != null ? parseFloat(t.portfolioValue.toFixed(2)) : '',
      }));
    const ws = XLSX.utils.json_to_sheet(rows);
    // Auto-width columns
    const colWidths = [{ wch: 12 }, { wch: 24 }, { wch: 22 }];
    ws['!cols'] = colWidths;
    const wb = XLSX.utils.book_new();
    XLSX.utils.book_append_sheet(wb, ws, 'XIRR Trend');
    XLSX.writeFile(wb, `XIRR_Trend_${casDate}_to_today.xlsx`);
  };

  const formatCurrency = (val: number) => 
    new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR' }).format(val);

  const handleRunAudit = async (mapping: Record<string, string>) => {
    setIsAuditing(true);
    try {
      const formData = new FormData();
      formData.append('cas_file', casFile);
      formData.append('cas_password', casPassword);
      formData.append('benchmark_mapping', JSON.stringify(mapping));

      const response = await fetch('http://localhost:8000/api/benchmark-audit', {
        method: 'POST',
        body: formData,
      });

      if (!response.ok) {
        throw new Error('Failed to run benchmark audit');
      }

      const result = await response.json();
      setCaptureRatios(result.captureRatios);
      alert("Benchmark Audit Complete! Check the Fund Breakdown table for your Capture Ratios.");
    } catch (err: any) {
      alert(`Error: ${err.message}`);
    } finally {
      setIsAuditing(false);
    }
  };

  const handleDownloadCAS = async () => {
    setIsDownloadingCas(true);
    try {
      const formData = new FormData();
      formData.append('cas_file', casFile);
      formData.append('cas_password', casPassword);

      const response = await fetch('http://localhost:8000/api/export-cas', {
        method: 'POST',
        body: formData,
      });

      if (!response.ok) throw new Error("Export failed");
      
      const blob = await response.blob();
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = "CAS_XIRR_Cash_Flows.xlsx";
      document.body.appendChild(a);
      a.click();
      a.remove();
    } catch (err: any) {
      alert("Download failed");
    } finally {
      setIsDownloadingCas(false);
    }
  };

  const getDisplayMetrics = () => {
    if (selectedPan === 'all' || !data.familyBreakdown || !data.familyBreakdown[selectedPan]) {
      return {
        liveTotalValue, 
        liveXirr: showExclTax ? data.exclTax?.liveXirr : liveXirr, 
        casTotalValue, 
        casXirr: showExclTax ? data.exclTax?.casXirr : casXirr,
        totalInvested, totalWithdrawals, netInvested, absoluteReturn,
        totalSchemes: fundWise.length
      };
    }
    const panData = data.familyBreakdown[selectedPan];
    return {
      liveTotalValue: panData.Valuation,
      liveXirr: showExclTax ? panData.TrueXIRRExclTax : panData.TrueXIRR,
      casTotalValue: panData.CASValuation,
      casXirr: showExclTax ? panData.CASXIRRExclTax : panData.CASXIRR,
      totalInvested: panData.TotalInvested,
      totalWithdrawals: panData.TotalWithdrawals,
      netInvested: panData.NetInvested,
      absoluteReturn: panData.AbsoluteReturn,
      totalSchemes: panData.TotalSchemes
    };
  };

  const displayMetrics = getDisplayMetrics();

  return (
    <div className="min-h-screen bg-foundation-grey text-clarity-white p-6 md:p-10 font-body">
      <div className="max-w-7xl mx-auto space-y-10">
        
        {/* Header */}
        <div className="flex flex-col md:flex-row justify-between items-start md:items-center gap-4">
          <div>
            <h1 className="text-3xl font-bold font-heading mb-1 text-vine-indigo">Portfolio Oversight</h1>
            <p className="text-gray-400">Track your investments and financial health</p>
          </div>
          <div className="flex items-center gap-4">
            {data.familyBreakdown && Object.keys(data.familyBreakdown).length > 0 && (
              <select 
                className="bg-gray-800 border border-gray-600 rounded-lg px-4 py-2 text-sm text-white focus:outline-none focus:border-vine-indigo"
                value={selectedPan}
                onChange={(e) => setSelectedPan(e.target.value)}
              >
                <option value="all">Entire Portfolio</option>
                {Object.keys(data.familyBreakdown).map(pan => (
                  <option key={pan} value={pan}>Investor: {pan}</option>
                ))}
              </select>
            )}
            <div className="flex gap-3">
              <button 
                onClick={handleDownloadCAS}
                disabled={isDownloadingCas}
                className="flex items-center gap-2 px-4 py-2 bg-white/10 hover:bg-white/20 border border-white/10 rounded-lg transition-colors text-sm font-medium"
              >
                <Download className="w-4 h-4" /> {isDownloadingCas ? 'Generating...' : 'Export CAS'}
              </button>
              <button 
                onClick={onReset}
                className="px-6 py-2 border border-gray-600 rounded-lg hover:bg-gray-800 transition-colors text-sm"
              >
                Upload New
              </button>
            </div>
          </div>
        </div>

        {/* Unmatched funds — search & map them, then re-analyze. */}
        {unresolvedFunds && unresolvedFunds.length > 0 && (
          <UnmatchedFundsResolver
            unresolvedFunds={unresolvedFunds}
            onApply={onReanalyze}
            isLoading={isReanalyzing}
          />
        )}

        {/* Data quality warnings — funds whose cost basis couldn't be resolved
            are exactly the ones that drift from Investwell. */}
        {dataWarnings && dataWarnings.length > 0 && (
          <div className="mb-8 rounded-lg border border-amber-500/40 bg-amber-500/10 p-4">
            <p className="text-sm font-semibold text-amber-300 mb-2">
              ⚠ {dataWarnings.length} fund{dataWarnings.length > 1 ? 's' : ''} could not be fully priced — their XIRR may not match Investwell:
            </p>
            <ul className="list-disc pl-5 space-y-1">
              {dataWarnings.map((w: string, i: number) => (
                <li key={i} className="text-xs text-amber-200/90">{w}</li>
              ))}
            </ul>
          </div>
        )}

        {/* Top Level Metrics */}
        <div className="flex justify-between items-end mb-4">
          <h2 className="text-xl font-bold font-heading text-vine-indigo">Headline Metrics</h2>
          {data.exclTax && (
            <label className="flex items-center gap-2 cursor-pointer bg-white/5 px-3 py-1.5 rounded-lg border border-white/10 hover:bg-white/10 transition-colors">
              <span className={`text-xs font-medium ${!showExclTax ? 'text-vine-mint' : 'text-gray-400'}`}>True XIRR</span>
              <div className="relative inline-block w-8 h-4 bg-gray-700 rounded-full">
                <input 
                  type="checkbox" 
                  className="peer sr-only" 
                  checked={showExclTax}
                  onChange={(e) => setShowExclTax(e.target.checked)}
                />
                <span className="absolute left-0.5 top-0.5 w-3 h-3 bg-white rounded-full transition-all peer-checked:translate-x-4 peer-checked:bg-vine-peach"></span>
              </div>
              <span className={`text-xs font-medium ${showExclTax ? 'text-vine-peach' : 'text-gray-400'}`}>Excl-Tax</span>
            </label>
          )}
        </div>
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6 mb-8">
          <MetricCard
            title="Live Valuation"
            value={displayMetrics.liveTotalValue ? formatCurrency(displayMetrics.liveTotalValue) : 'N/A'}
            subtitle="Based on today's NAV"
            highlight={true}
          />
          <MetricCard
            title="True Portfolio XIRR"
            value={displayMetrics.liveXirr != null ? `${displayMetrics.liveXirr.toFixed(2)}%` : 'N/A'}
            subtitle="Pooled cashflows, switches excluded"
          />
          <MetricCard
            title="CAS PDF Valuation"
            value={displayMetrics.casTotalValue ? formatCurrency(displayMetrics.casTotalValue) : 'N/A'}
            subtitle="As per statement date"
          />
          <MetricCard
            title="CAS PDF XIRR"
            value={displayMetrics.casXirr != null ? `${displayMetrics.casXirr.toFixed(2)}%` : 'N/A'}
            subtitle="Pooled cashflows as of statement date"
          />
        </div>

        {/* Additional Metrics Row */}
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
          <MetricCard 
            title="Total Additions" 
            value={displayMetrics.totalInvested ? formatCurrency(displayMetrics.totalInvested) : 'N/A'} 
            subtitle="Gross capital invested"
          />
          <MetricCard 
            title="Total Withdrawals" 
            value={displayMetrics.totalWithdrawals ? formatCurrency(displayMetrics.totalWithdrawals) : '₹0.00'} 
            subtitle="Capital redeemed"
          />
          <MetricCard 
            title="Net Invested (Out-of-Pocket)" 
            value={displayMetrics.netInvested ? formatCurrency(displayMetrics.netInvested) : 'N/A'} 
            subtitle={displayMetrics.absoluteReturn !== undefined ? `Absolute Return: ${displayMetrics.absoluteReturn.toFixed(2)}%` : 'Additions minus Withdrawals'}
          />
          <MetricCard 
            title="Total Schemes" 
            value={displayMetrics.totalSchemes ? displayMetrics.totalSchemes.toString() : '0'} 
            subtitle="Active mutual funds held"
          />
        </div>


        <div className="flex border-b border-gray-700 mt-10 overflow-x-auto">
          <button 
            className={`px-6 py-3 font-medium text-sm whitespace-nowrap transition-colors ${activeTab === 'overview' ? 'border-b-2 border-vine-indigo text-vine-indigo' : 'text-gray-400 hover:text-white'}`}
            onClick={() => setActiveTab('overview')}
          >
            Overview & Analysis
          </button>
          <button 
            className={`px-6 py-3 font-medium text-sm whitespace-nowrap transition-colors flex items-center gap-2 ${activeTab === 'family' ? 'border-b-2 border-vine-indigo text-vine-indigo' : 'text-gray-400 hover:text-white'}`}
            onClick={() => setActiveTab('family')}
          >
            Family & Category Breakdown
          </button>
          <button 
            className={`px-6 py-3 font-medium text-sm whitespace-nowrap transition-colors flex items-center gap-2 ${activeTab === 'cagr' ? 'border-b-2 border-vine-indigo text-vine-indigo' : 'text-gray-400 hover:text-white'}`}
            onClick={() => setActiveTab('cagr')}
          >
            CAGR & Returns
          </button>
          <button
            className={`px-6 py-3 font-medium text-sm whitespace-nowrap transition-colors ${activeTab === 'snapshot' ? 'border-b-2 border-vine-indigo text-vine-indigo' : 'text-gray-400 hover:text-white'}`}
            onClick={() => setActiveTab('snapshot')}
          >
            Value by Date
          </button>
          <button
            className={`px-6 py-3 font-medium text-sm whitespace-nowrap transition-colors flex items-center gap-2 ${activeTab === 'data' ? 'border-b-2 border-vine-indigo text-vine-indigo' : 'text-gray-400 hover:text-white'}`}
            onClick={() => setActiveTab('data')}
          >
            <TableIcon className="w-4 h-4" /> Holdings & Transactions
          </button>
        </div>

        {activeTab === 'overview' && (
          <>
            {/* Trend Chart — always visible; inner content handles loading/empty */}
            <div className="glass-card p-6 mb-8 mt-6">
                <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 mb-4">
                  <h2 className="text-xl font-bold">Portfolio XIRR Trend</h2>
                  <div className="flex items-center gap-3 flex-wrap">
                    {/* Zoom pills */}
                    <div className="flex gap-1 bg-white/5 rounded-lg p-1">
                      {(['1m', '3m', '6m', '1y', '2y', 'all'] as const).map(z => (
                        <button
                          key={z}
                          onClick={() => setTrendZoom(z)}
                          className={`px-3 py-1 rounded text-xs font-medium transition-colors ${trendZoom === z ? 'bg-vine-indigo text-white' : 'text-gray-400 hover:text-white'}`}
                        >
                          {z === 'all' ? 'All' : z.toUpperCase()}
                        </button>
                      ))}
                    </div>
                    {/* Excel download */}
                    <button
                      onClick={handleDownloadXirrTrend}
                      disabled={!casDate}
                      className="flex items-center gap-2 px-3 py-1.5 bg-green-900/40 hover:bg-green-800/60 border border-green-700/50 rounded-lg text-xs font-medium text-green-300 transition-colors disabled:opacity-40"
                      title={casDate ? `Download XIRR values from 5 days before ${casDate} to today` : 'CAS date unavailable'}
                    >
                      <FileSpreadsheet className="w-3.5 h-3.5" /> Download XIRR Trend
                    </button>
                  </div>
                </div>
                {casDate && (
                  <p className="text-xs text-gray-500 mb-3">
                    Excel export covers: 5 days before CAS date ({casDate}) → today
                  </p>
                )}
                {trendZoom === 'all' && chartDisplayTrend.length < filteredTrend.length && !trendLoading && (
                  <p className="text-xs text-gray-500 mb-3">
                    Early period hidden — XIRR was inflated ({'>'}200%) while the portfolio was newly started.
                    Showing from <strong>{chartDisplayTrend[0]?.date}</strong>.
                  </p>
                )}
                {trendExcludedFunds && trendExcludedFunds.length > 0 && !trendLoading && (
                  <p className="text-xs text-amber-500/80 mb-3">
                    Excluded from trend: {trendExcludedFunds.join(', ')} (no NAV history).
                  </p>
                )}
                {reconWarnings && reconWarnings.length > 0 && !trendLoading && (
                  <div className="mb-3 rounded text-amber-500/80 bg-amber-500/10 p-3 text-xs">
                    <p className="font-semibold mb-1">⚠ Unit Reconciliation Drift Detected</p>
                    <ul className="list-disc pl-5">
                      {reconWarnings.map((w: string, i: number) => <li key={i}>{w}</li>)}
                    </ul>
                  </div>
                )}
                <div className="h-[400px]">
                  {trendLoading ? (
                    <div className="h-full flex items-center justify-center text-gray-400 text-sm">
                      <span className="animate-pulse">Computing trend...</span>
                    </div>
                  ) : filteredTrend.length === 0 ? (
                    <div className="h-full flex items-center justify-center text-gray-600 text-sm">
                      Upload a CAS to see the XIRR trend chart.
                    </div>
                  ) : (
                  <ResponsiveContainer width="100%" height="100%">
                    <LineChart
                      data={chartDisplayTrend}
                      margin={{ top: 5, right: 30, left: 20, bottom: 5 }}
                    >
                      <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
                      <XAxis dataKey="date" stroke="#9CA3AF" tickFormatter={(val) => new Date(val).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })} />
                      <YAxis stroke="#9CA3AF" tickFormatter={(value) => `${value}%`} />
                      <Tooltip
                        contentStyle={{ backgroundColor: '#1D1D1B', borderColor: '#333', borderRadius: '8px' }}
                        itemStyle={{ color: '#B1F0DB' }}
                        formatter={(value: any) => [`${Number(value).toFixed(2)}%`, 'True Portfolio XIRR']}
                      />
                      <Line type="monotone" dataKey="xirr" stroke="#9B81F5" strokeWidth={3} dot={false} activeDot={{ r: 6, fill: '#B1F0DB', stroke: '#1D1D1B', strokeWidth: 2 }} name="xirr" />
                    </LineChart>
                  </ResponsiveContainer>
                  )}
                </div>
            </div>

            {/* Fund Breakdown */}
            <div className="glass-card p-6 md:p-8 overflow-x-auto">
              <h2 className="text-xl font-bold mb-6">Fund Breakdown</h2>
              <table className="w-full text-left border-collapse">
                <thead>
                  <tr className="border-b border-gray-700 text-gray-400 text-sm">
                    <th className="pb-4 font-medium">Scheme Name</th>
                    <th className="pb-4 font-medium text-right">Live Value</th>
                    <th className="pb-4 font-medium text-right">Live XIRR</th>
                    <th className="pb-4 font-medium text-right text-vine-peach">Live XIRR (excl. tax)</th>
                    <th className="pb-4 font-medium text-right">CAS XIRR</th>
                    {captureRatios && (
                      <>
                        <th className="pb-4 font-medium text-right text-vine-peach">Up Capture</th>
                        <th className="pb-4 font-medium text-right text-vine-peach">Down Capture</th>
                      </>
                    )}
                  </tr>
                </thead>
                <tbody className="text-sm">
                  {fundWise.map((fund: any, idx: number) => {
                    const cap = captureRatios ? captureRatios[fund.Scheme] : null;
                    return (
                      <tr key={idx} className="border-b border-gray-800/50 hover:bg-white/5 transition-colors">
                        <td className="py-4 font-medium max-w-xs truncate" title={fund.Scheme}>{fund.Scheme}</td>
                        <td className="py-4 text-right">{formatCurrency(fund.LiveValuation)}</td>
                        <td className="py-4 text-right text-vine-mint">{fund.LiveXIRR != null ? `${fund.LiveXIRR.toFixed(2)}%` : '-'}</td>
                        <td className="py-4 text-right text-vine-peach">{fund.LiveXIRRExclTax != null ? `${fund.LiveXIRRExclTax.toFixed(2)}%` : '-'}</td>
                        <td className="py-4 text-right text-gray-400">{fund.CASXIRR != null ? `${fund.CASXIRR.toFixed(2)}%` : '-'}</td>
                        {captureRatios && (
                          <>
                            <td className="py-4 text-right">{cap && cap.upCapture != null ? `${cap.upCapture.toFixed(2)}%` : '-'}</td>
                            <td className="py-4 text-right">{cap && cap.downCapture != null ? `${cap.downCapture.toFixed(2)}%` : '-'}</td>
                          </>
                        )}
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>

            <BenchmarkMapper funds={fundWise} onRunAudit={handleRunAudit} isLoading={isAuditing} />
          </>
        )}

        {activeTab === 'family' && data.familyBreakdown && (
          <div className="grid grid-cols-1 xl:grid-cols-2 gap-8 mt-8">
            {Object.entries(data.familyBreakdown).map(([pan, panData]: [string, any], idx) => (
              <div key={idx} className="glass-card p-6 md:p-8 flex flex-col gap-6">
                <div className="border-b border-gray-700 pb-4">
                  <h2 className="text-2xl font-bold text-vine-peach mb-2">Investor: {pan}</h2>
                  <div className="grid grid-cols-2 gap-4 mt-4">
                    <div>
                      <p className="text-sm text-gray-400">Total Valuation</p>
                      <p className="text-lg font-bold">{formatCurrency(panData.Valuation)}</p>
                    </div>
                    <div>
                      <p className="text-sm text-gray-400">True XIRR</p>
                      <p className="text-lg font-bold text-vine-mint">{panData.TrueXIRR != null ? `${panData.TrueXIRR.toFixed(2)}%` : 'N/A'}</p>
                    </div>
                  </div>
                </div>

                <div className="flex flex-col gap-6">
                  {Object.entries(panData.Categories).map(([category, catData]: [string, any], cIdx) => (
                    <div key={cIdx} className="bg-white/5 rounded-lg p-4 border border-white/10">
                      <div className="flex justify-between items-center mb-4 border-b border-gray-700/50 pb-2">
                        <h3 className="text-lg font-bold text-gray-200">{category}</h3>
                        <div className="flex gap-4 text-sm">
                          <span className="text-gray-400">Value: <span className="text-white font-medium">{formatCurrency(catData.Valuation)}</span></span>
                          <span className="text-vine-mint">True: <span className="font-bold">{catData.TrueXIRR != null ? `${catData.TrueXIRR.toFixed(2)}%` : '-'}</span></span>
                        </div>
                      </div>
                      <table className="w-full text-left text-sm border-collapse">
                        <thead>
                          <tr className="text-gray-500 border-b border-gray-800/50">
                            <th className="pb-2 font-medium">Scheme</th>
                            <th className="pb-2 font-medium text-right">Value</th>
                            <th className="pb-2 font-medium text-right">XIRR</th>
                          </tr>
                        </thead>
                        <tbody>
                          {catData.Funds.map((f: any, fIdx: number) => (
                            <tr key={fIdx} className="border-b border-gray-800/30">
                              <td className="py-2 text-gray-300 max-w-[200px] truncate" title={f.Scheme}>{f.Scheme}</td>
                              <td className="py-2 text-right">{formatCurrency(f.LiveValuation)}</td>
                              <td className="py-2 text-right text-gray-300">{f.LiveXIRR != null ? `${f.LiveXIRR.toFixed(2)}%` : '-'}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}

        {activeTab === 'cagr' && (
          <div className="glass-card p-6 md:p-8 mt-6">
            <h2 className="text-xl font-bold mb-2">CAGR & Return Metrics</h2>
            <p className="text-sm text-gray-400 mb-6">
              <span className="text-vine-mint font-medium">Simple CAGR</span> = (Value ÷ Net Invested)<sup>1/years</sup> − 1 &nbsp;·&nbsp; ignores timing of investments.<br/>
              <span className="text-vine-indigo font-medium">XIRR</span> = time-adjusted annualised return &nbsp;·&nbsp; penalises late SIPs, rewards early ones. These will differ.
            </p>

            {/* Total Summary Row */}
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-8">
              <div className="bg-white/5 border border-white/10 rounded-lg p-4">
                <div className="text-sm text-gray-400 mb-1">Total Net Invested</div>
                <div className="text-lg font-bold">{displayMetrics.netInvested ? formatCurrency(displayMetrics.netInvested) : 'N/A'}</div>
              </div>
              <div className="bg-white/5 border border-white/10 rounded-lg p-4">
                <div className="text-sm text-gray-400 mb-1">Total Current Value</div>
                <div className="text-lg font-bold">{displayMetrics.liveTotalValue ? formatCurrency(displayMetrics.liveTotalValue) : 'N/A'}</div>
              </div>
              <div className="bg-white/5 border border-vine-mint/30 rounded-lg p-4 bg-vine-mint/5 relative overflow-hidden">
                <div className="absolute right-0 top-0 w-16 h-16 bg-vine-mint/10 rounded-bl-full -mr-8 -mt-8"></div>
                <div className="text-sm font-medium text-vine-mint/80 mb-1">Simple CAGR</div>
                <div className="text-2xl font-bold text-vine-mint">{simpleCagr != null ? `${simpleCagr.toFixed(2)}%` : 'N/A'}</div>
                <div className="text-xs text-gray-500 mt-1">Timing-agnostic</div>
              </div>
              <div className="bg-white/5 border border-vine-indigo/30 rounded-lg p-4 bg-vine-indigo/5 relative overflow-hidden">
                <div className="absolute right-0 top-0 w-16 h-16 bg-vine-indigo/10 rounded-bl-full -mr-8 -mt-8"></div>
                <div className="text-sm font-medium text-vine-indigo/80 mb-1">XIRR (Personalised)</div>
                <div className="text-2xl font-bold text-vine-indigo">{displayMetrics.liveXirr != null ? `${displayMetrics.liveXirr.toFixed(2)}%` : 'N/A'}</div>
                <div className="text-xs text-gray-500 mt-1">Accounts for when you invested</div>
              </div>
            </div>

            <div className="overflow-x-auto">
              <table className="w-full text-left border-collapse">
                <thead>
                  <tr className="border-b border-gray-700 text-gray-400 text-sm">
                    <th className="pb-4 font-medium">Scheme Name</th>
                    <th className="pb-4 font-medium text-right">Net Invested</th>
                    <th className="pb-4 font-medium text-right">Current Value</th>
                    <th className="pb-4 font-medium text-right text-vine-peach">Absolute Return %</th>
                    <th className="pb-4 font-medium text-right text-vine-mint">CAGR / XIRR %</th>
                  </tr>
                </thead>
                <tbody className="text-sm">
                  {fundWise.map((fund: any, idx: number) => (
                    <tr key={idx} className="border-b border-gray-800/50 hover:bg-white/5 transition-colors">
                      <td className="py-4 font-medium max-w-xs truncate" title={fund.Scheme}>
                        {fund.Scheme}
                        <span className="block text-xs text-gray-500 mt-1">{fund.Category} | {fund.PAN}</span>
                      </td>
                      <td className="py-4 text-right">{formatCurrency(fund.NetInvested)}</td>
                      <td className="py-4 text-right">{formatCurrency(fund.LiveValuation)}</td>
                      <td className="py-4 text-right font-medium text-vine-peach">
                        {fund.AbsoluteReturn !== undefined && fund.AbsoluteReturn !== null ? `${fund.AbsoluteReturn.toFixed(2)}%` : '-'}
                      </td>
                      <td className="py-4 text-right font-bold text-vine-mint">
                        {fund.LiveXIRR != null ? `${fund.LiveXIRR.toFixed(2)}%` : '-'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
        {activeTab === 'snapshot' && (
          <div className="mt-8 space-y-6">
            {/* Date picker */}
            <div className="glass-card p-6">
              <h2 className="text-xl font-bold mb-1">Portfolio Value by Date</h2>
              <p className="text-sm text-gray-400 mb-5">
                Pick any date — we'll show the portfolio's net value and XIRR as of that day using current holdings × historical NAV.
              </p>
              <div className="flex flex-col sm:flex-row items-start sm:items-center gap-4">
                <div className="flex flex-col gap-1">
                  <label className="text-xs text-gray-400 font-medium uppercase tracking-wider">Select Date</label>
                  <input
                    type="date"
                    value={selectedHistDate}
                    min={mergedTrend[0]?.date ?? ''}
                    max={todaySnapshot?.date ?? ''}
                    onChange={e => setSelectedHistDate(e.target.value)}
                    className="bg-gray-800 border border-gray-600 rounded-lg px-4 py-2 text-white text-sm focus:outline-none focus:border-vine-indigo [color-scheme:dark]"
                  />
                </div>
                {histSnapshot && selectedHistDate !== histSnapshot.date && !trendLoading && (
                  <p className="text-xs text-yellow-400 mt-4 sm:mt-5">
                    No data for {selectedHistDate} (weekend/holiday) — showing nearest trading day: <strong>{histSnapshot.date}</strong>
                  </p>
                )}
              </div>
            </div>

            {trendLoading ? (
              <div className="glass-card p-10 flex justify-center items-center">
                <span className="animate-pulse text-gray-400">Computing trend...</span>
              </div>
            ) : histSnapshot && (
              <>
                {/* Main snapshot metrics */}
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-5">
                  <div className="glass-card p-6">
                    <p className="text-sm text-gray-400 mb-1">Portfolio Value</p>
                    <p className="text-2xl font-bold text-vine-indigo">
                      {histSnapshot.portfolioValue != null ? formatCurrency(histSnapshot.portfolioValue) : 'N/A'}
                    </p>
                    {todaySnapshot?.portfolioValue != null && histSnapshot.portfolioValue != null && (
                      <p className={`text-sm mt-2 font-medium ${histSnapshot.portfolioValue <= todaySnapshot.portfolioValue ? 'text-vine-mint' : 'text-red-400'}`}>
                        {histSnapshot.portfolioValue <= todaySnapshot.portfolioValue ? '▲' : '▼'}&nbsp;
                        {formatCurrency(Math.abs(todaySnapshot.portfolioValue - histSnapshot.portfolioValue))} vs today
                      </p>
                    )}
                  </div>

                  <div className="glass-card p-6">
                    <p className="text-sm text-gray-400 mb-1">True Portfolio XIRR</p>
                    <p className="text-2xl font-bold text-vine-mint">
                      {histSnapshot.xirr != null ? `${histSnapshot.xirr.toFixed(2)}%` : 'N/A'}
                    </p>
                    {todaySnapshot?.xirr != null && histSnapshot.xirr != null && (
                      <p className={`text-sm mt-2 font-medium ${histSnapshot.xirr <= todaySnapshot.xirr ? 'text-vine-mint' : 'text-red-400'}`}>
                        {histSnapshot.xirr <= todaySnapshot.xirr ? '▲' : '▼'}&nbsp;
                        {Math.abs(todaySnapshot.xirr - histSnapshot.xirr).toFixed(2)}% vs today
                      </p>
                    )}
                  </div>
                </div>

                {/* Compare vs CAS date */}
                {casDate && (
                  <div className="glass-card p-6">
                    <h3 className="font-semibold mb-4 text-gray-200">Compare with CAS Statement Date ({casDate})</h3>
                    {(() => {
                      const casSnap = getSnapshotForDate(casDate);
                      if (!casSnap) return <p className="text-gray-500 text-sm">No data for CAS date.</p>;
                      return (
                        <div className="overflow-x-auto">
                          <table className="w-full text-sm border-collapse">
                            <thead>
                              <tr className="border-b border-gray-700 text-gray-400">
                                <th className="pb-3 text-left font-medium">Metric</th>
                                <th className="pb-3 text-right font-medium">CAS Date ({casSnap.date})</th>
                                <th className="pb-3 text-right font-medium text-vine-indigo">Selected ({histSnapshot.date})</th>
                                <th className="pb-3 text-right font-medium">Change</th>
                              </tr>
                            </thead>
                            <tbody>
                              {[
                                {
                                  label: 'Portfolio Value',
                                  cas: casSnap.portfolioValue,
                                  sel: histSnapshot.portfolioValue,
                                  fmt: (v: number) => formatCurrency(v),
                                  diff: (a: number, b: number) => formatCurrency(b - a),
                                },
                                {
                                  label: 'True XIRR (%)',
                                  cas: casSnap.xirr,
                                  sel: histSnapshot.xirr,
                                  fmt: (v: number) => `${v.toFixed(2)}%`,
                                  diff: (a: number, b: number) => `${(b - a).toFixed(2)}%`,
                                },
                              ].map((row, i) => {
                                const delta = row.cas != null && row.sel != null ? row.sel - row.cas : null;
                                return (
                                  <tr key={i} className="border-b border-gray-800/50">
                                    <td className="py-3 text-gray-300">{row.label}</td>
                                    <td className="py-3 text-right text-gray-400">{row.cas != null ? row.fmt(row.cas) : '-'}</td>
                                    <td className="py-3 text-right font-semibold text-vine-indigo">{row.sel != null ? row.fmt(row.sel) : '-'}</td>
                                    <td className={`py-3 text-right font-medium ${delta == null ? '' : delta >= 0 ? 'text-vine-mint' : 'text-red-400'}`}>
                                      {delta != null ? `${delta >= 0 ? '+' : ''}${row.diff(row.cas!, row.sel!)}` : '-'}
                                    </td>
                                  </tr>
                                );
                              })}
                            </tbody>
                          </table>
                        </div>
                      );
                    })()}
                  </div>
                )}
              </>
            )}

            {!histSnapshot && !trendLoading && (
              <div className="glass-card p-10 text-center text-gray-500">
                No historical data available for the selected date.
              </div>
            )}
          </div>
        )}

        {activeTab === 'data' && (
          <div className="space-y-8 mt-6">
            <div className="glass-card p-6 overflow-x-auto">
              <h2 className="text-xl font-bold mb-4">Current Holdings</h2>
              <table className="w-full text-left text-sm border-collapse">
                <thead>
                  <tr className="border-b border-gray-700 text-gray-400">
                    <th className="pb-3">Scheme</th>
                    <th className="pb-3">PAN</th>
                    <th className="pb-3">Units</th>
                    <th className="pb-3">NAV</th>
                    <th className="pb-3">Value</th>
                  </tr>
                </thead>
                <tbody>
                  {holdings.map((h: any, i: number) => (
                    <tr key={i} className="border-b border-gray-800/50">
                      <td className="py-3">{h.Scheme}</td>
                      <td className="py-3">{h.PAN}</td>
                      <td className="py-3">{h.Units.toFixed(3)}</td>
                      <td className="py-3">{h.NAV.toFixed(4)}</td>
                      <td className="py-3">{formatCurrency(h.Value)}</td>
                    </tr>
                  ))}
                </tbody>
                <tfoot className="font-bold text-vine-peach border-t border-gray-600">
                  <tr>
                    <td className="py-4" colSpan={4}>Total CAS Valuation</td>
                    <td className="py-4">{formatCurrency(casTotalValue)}</td>
                  </tr>
                </tfoot>
              </table>
            </div>

            <div className="glass-card p-6 overflow-x-auto">
              <h2 className="text-xl font-bold mb-4">Raw Transactions</h2>
              <table className="w-full text-left text-sm border-collapse">
                <thead>
                  <tr className="border-b border-gray-700 text-gray-400">
                    <th className="pb-3">Date</th>
                    <th className="pb-3">Type</th>
                    <th className="pb-3">Scheme</th>
                    <th className="pb-3">PAN</th>
                    <th className="pb-3">Amount</th>
                    <th className="pb-3">Units</th>
                  </tr>
                </thead>
                <tbody>
                  {transactions.slice(0, 200).map((t: any, i: number) => (
                    <tr key={i} className="border-b border-gray-800/50">
                      <td className="py-3 whitespace-nowrap">{t.Date}</td>
                      <td className="py-3">{t.Type}</td>
                      <td className="py-3 truncate max-w-[200px]" title={t.Scheme}>{t.Scheme}</td>
                      <td className="py-3">{t.PAN}</td>
                      <td className="py-3">{t.Amount ? formatCurrency(t.Amount) : '-'}</td>
                      <td className="py-3">{t.Units ? t.Units.toFixed(3) : '-'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {transactions.length > 200 && (
                <div className="text-center text-gray-500 mt-4 text-xs">
                  Showing first 200 transactions. Export CAS Excel to view all.
                </div>
              )}
            </div>
          </div>
        )}

      </div>
    </div>
  );
}
