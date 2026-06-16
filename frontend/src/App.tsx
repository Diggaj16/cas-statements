import { useState } from 'react';
import UploadZone from './UploadZone';
import Dashboard from './Dashboard';

function App() {
  const [appData, setAppData] = useState<any>(null);
  const [casFile, setCasFile] = useState<File | null>(null);
  const [casPassword, setCasPassword] = useState<string>('');
  const [isLoading, setIsLoading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);

  const runAnalyze = async (
    file: File,
    password: string,
    manualMapping?: { scheme: string; isin?: string; code: string; name: string }[],
  ) => {
    setIsLoading(true);
    setUploadError(null);
    try {
      const formData = new FormData();
      formData.append('cas_file', file);
      formData.append('cas_password', password);
      if (manualMapping && manualMapping.length) {
        formData.append('manual_mapping', JSON.stringify(manualMapping));
      }

      const response = await fetch('http://localhost:8000/api/analyze', {
        method: 'POST',
        body: formData,
      });

      if (!response.ok) {
        const err = await response.json();
        throw new Error(err.detail || 'Failed to analyze CAS');
      }

      const result = await response.json();
      setAppData(result);

      // Fire background trend fetch so dashboard loads instantly
      fetchTrend(file, password);
    } catch (error: any) {
      setUploadError(error.message || 'An unexpected error occurred');
    } finally {
      setIsLoading(false);
    }
  };

  const fetchTrend = async (file: File, password: string) => {
    setAppData((prev: any) => prev ? { ...prev, trendLoading: true } : prev);
    try {
      const formData = new FormData();
      formData.append('cas_file', file);
      formData.append('cas_password', password);
      
      const response = await fetch('http://localhost:8000/api/trend', {
        method: 'POST',
        body: formData,
      });
      
      if (!response.ok) throw new Error('Failed to fetch trend');
      
      const result = await response.json();
      setAppData((prev: any) => prev ? {
        ...prev,
        trend: result.trend,
        trendExcludedFunds: result.trendExcludedFunds,
        reconWarnings: result.reconWarnings,
        trendLoading: false
      } : prev);
    } catch (e) {
      console.error(e);
      setAppData((prev: any) => prev ? { ...prev, trendLoading: false } : prev);
    }
  };

  const handleUpload = async (file: File, password: string) => {
    setCasFile(file);
    setCasPassword(password);
    await runAnalyze(file, password);
  };

  // Re-run analysis after the user maps previously-unmatched funds.
  const handleReanalyze = async (
    mapping: { scheme: string; isin?: string; code: string; name: string }[],
  ) => {
    if (!casFile) return;
    await runAnalyze(casFile, casPassword, mapping);
  };

  const handleReset = () => {
    setAppData(null);
    setCasFile(null);
    setCasPassword('');
  };

  return (
    <main className="min-h-screen bg-foundation-grey text-clarity-white">
      {!appData ? (
        <UploadZone onUpload={handleUpload} isLoading={isLoading} error={uploadError} />
      ) : (
        <Dashboard data={appData} onReset={handleReset} casFile={casFile!} casPassword={casPassword} onReanalyze={handleReanalyze} isReanalyzing={isLoading} />
      )}
    </main>
  );
}

export default App;
