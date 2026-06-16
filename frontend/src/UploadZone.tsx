import { useState } from 'react';
import { Upload, FileText, ArrowRight, ShieldCheck } from 'lucide-react';

interface UploadZoneProps {
  onUpload: (file: File, password: string) => void;
  isLoading: boolean;
  error?: string | null;
}

export default function UploadZone({ onUpload, isLoading, error }: UploadZoneProps) {
  const [file, setFile] = useState<File | null>(null);
  const [password, setPassword] = useState('');
  const [clearingCache, setClearingCache] = useState(false);
  const [cacheMessage, setCacheMessage] = useState('');

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files.length > 0) {
      setFile(e.target.files[0]);
    }
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (file && password) {
      onUpload(file, password);
    }
  };

  const handleClearCache = async () => {
    setClearingCache(true);
    try {
      const response = await fetch('http://localhost:8000/api/clear-cache', { method: 'POST' });
      if (response.ok) {
        setCacheMessage('Cache cleared! Upload will fetch fresh NAVs.');
        setTimeout(() => setCacheMessage(''), 4000);
      }
    } catch (err) {
      setCacheMessage('Failed to clear cache.');
      setTimeout(() => setCacheMessage(''), 4000);
    } finally {
      setClearingCache(false);
    }
  };

  return (
    <div className="flex flex-col items-center justify-center min-h-screen p-6">
      <div className="text-center mb-12">
        <h1 className="text-5xl font-bold mb-4 tracking-tight">
          <span className="text-vine-indigo">Growth</span>vine
        </h1>
        <p className="text-xl text-gray-400 font-body">Own Your Financial Future</p>
      </div>

      <div className="glass-card w-full max-w-xl p-8 shadow-2xl relative overflow-hidden">
        {/* Decorative fingerprint-like circles in background */}
        <div className="absolute top-0 right-0 w-64 h-64 border-[1px] border-white/5 rounded-full -translate-y-1/2 translate-x-1/4 pointer-events-none"></div>
        <div className="absolute top-0 right-0 w-48 h-48 border-[1px] border-white/5 rounded-full -translate-y-1/3 translate-x-1/3 pointer-events-none"></div>

        <h2 className="text-2xl font-bold mb-6 text-center">Upload CAS Statement</h2>
        
        {error && (
          <div className="mb-6 rounded-lg bg-red-500/10 border border-red-500/50 p-4 text-center">
            <p className="text-red-400 text-sm font-medium">{error}</p>
          </div>
        )}

        <form onSubmit={handleSubmit} className="space-y-6 relative z-10">
          <div className="border-2 border-dashed border-gray-600 rounded-lg p-8 text-center hover:border-vine-indigo transition-colors cursor-pointer bg-black/20">
            <input 
              type="file" 
              accept=".pdf" 
              onChange={handleFileChange} 
              className="hidden" 
              id="cas-upload" 
            />
            <label htmlFor="cas-upload" className="cursor-pointer flex flex-col items-center">
              <div className="bg-vine-indigo/10 p-4 rounded-full mb-4">
                {file ? <FileText className="w-8 h-8 text-vine-indigo" /> : <Upload className="w-8 h-8 text-vine-indigo" />}
              </div>
              <span className="text-lg font-medium">{file ? file.name : 'Click to upload your CAS PDF'}</span>
              <span className="text-sm text-gray-500 mt-2">Only CAMS/KFintech PDFs are supported</span>
            </label>
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-400 mb-2">CAS Password (Usually your PAN)</label>
            <input 
              type="password" 
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="Enter password..."
              className="w-full bg-black/40 border border-gray-700 rounded-lg px-4 py-3 text-white focus:outline-none focus:border-vine-indigo focus:ring-1 focus:ring-vine-indigo transition-all"
            />
          </div>

          <button 
            type="submit" 
            disabled={!file || !password || isLoading}
            className={`w-full flex items-center justify-center gap-2 py-4 rounded-lg font-semibold text-lg transition-all ${
              file && password && !isLoading
                ? 'bg-vine-indigo text-white hover:bg-[#856be3] bevel-emboss'
                : 'bg-gray-800 text-gray-500 cursor-not-allowed'
            }`}
          >
            {isLoading ? (
              <span className="animate-pulse">Analyzing Portfolio...</span>
            ) : (
              <>
                Analyze Portfolio <ArrowRight className="w-5 h-5" />
              </>
            )}
          </button>
        </form>

        <div className="mt-6 flex flex-col items-center justify-center gap-2 text-sm text-gray-500">
          <div className="flex items-center gap-2">
            <ShieldCheck className="w-4 h-4" />
            <span>Your PDF is processed locally and discarded. Only fund identifiers and NAVs are cached.</span>
          </div>
          <button 
            type="button" 
            onClick={handleClearCache}
            disabled={clearingCache}
            className="text-vine-indigo hover:text-white transition-colors mt-2 underline decoration-vine-indigo/30 underline-offset-4"
          >
            {clearingCache ? 'Clearing...' : 'Force Refresh AMFI NAV Data (Clear Cache)'}
          </button>
          {cacheMessage && <span className="text-vine-mint font-medium animate-pulse">{cacheMessage}</span>}
        </div>
      </div>
    </div>
  );
}
