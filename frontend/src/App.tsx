import { useState } from 'react';
import UploadZone from './UploadZone';
import Dashboard from './Dashboard';

function App() {
  const [appData, setAppData] = useState<any>(null);
  const [casFile, setCasFile] = useState<File | null>(null);
  const [casPassword, setCasPassword] = useState<string>('');
  const [isLoading, setIsLoading] = useState(false);

  const handleUpload = async (file: File, password: string) => {
    setIsLoading(true);
    setCasFile(file);
    setCasPassword(password);
    try {
      const formData = new FormData();
      formData.append('cas_file', file);
      formData.append('cas_password', password);

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
    } catch (error: any) {
      alert(`Error: ${error.message}`);
    } finally {
      setIsLoading(false);
    }
  };

  const handleReset = () => {
    setAppData(null);
    setCasFile(null);
    setCasPassword('');
  };

  return (
    <main className="min-h-screen bg-foundation-grey text-clarity-white">
      {!appData ? (
        <UploadZone onUpload={handleUpload} isLoading={isLoading} />
      ) : (
        <Dashboard data={appData} onReset={handleReset} casFile={casFile!} casPassword={casPassword} />
      )}
    </main>
  );
}

export default App;
