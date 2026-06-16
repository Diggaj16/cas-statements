interface MetricCardProps {
  title: string;
  value: string;
  subtitle?: string;
  highlight?: boolean;
}

export default function MetricCard({ title, value, subtitle, highlight = false }: MetricCardProps) {
  return (
    <div className={`p-6 rounded-xl border relative overflow-hidden transition-all duration-300 ${
      highlight 
        ? 'bg-vine-indigo text-white border-vine-indigo bevel-emboss' 
        : 'glass-card'
    }`}>
      {/* Texture background for highlighted card */}
      {highlight && (
        <div className="absolute inset-0 opacity-10 pointer-events-none" style={{
          backgroundImage: 'radial-gradient(circle at 100% 100%, white 0%, transparent 60%)'
        }}></div>
      )}
      
      <div className="relative z-10">
        <h3 className={`text-sm font-medium mb-2 ${highlight ? 'text-white/80' : 'text-gray-400'}`}>
          {title}
        </h3>
        <div className="text-3xl font-bold font-heading">
          {value}
        </div>
        {subtitle && (
          <div className={`text-xs mt-2 ${highlight ? 'text-white/70' : 'text-gray-500'}`}>
            {subtitle}
          </div>
        )}
      </div>
    </div>
  );
}
