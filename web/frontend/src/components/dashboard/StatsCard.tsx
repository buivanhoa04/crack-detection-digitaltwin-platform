'use client';

import { ReactNode } from 'react';
import { TrendingUp, TrendingDown, Minus } from 'lucide-react';

interface StatsCardProps {
  title: string;
  value: string | number;
  change?: number;
  changeLabel?: string;
  icon: ReactNode;
  iconBg: string;
  delay?: number;
}

export default function StatsCard({
  title,
  value,
  change,
  changeLabel,
  icon,
  iconBg,
  delay = 0,
}: StatsCardProps) {
  const trend =
    change === undefined ? null : change > 0 ? 'up' : change < 0 ? 'down' : 'flat';

  return (
    <div
      className="stats-card group"
      style={{ animationDelay: `${delay}ms` }}
    >
      <div className="relative z-10">
        <div className={`icon-wrapper ${iconBg}`}>
          {icon}
        </div>
        <p className="text-xs font-bold text-slate-400 mb-1 pointer-events-none">{title}</p>
        <p className="text-xl font-black text-slate-900 tracking-tight">{value}</p>
        {change !== undefined ? (
          <div className="flex items-center gap-1.5 mt-2">
            {trend === 'up' && <TrendingUp className="w-3.5 h-3.5 text-emerald-400" />}
            {trend === 'down' && <TrendingDown className="w-3.5 h-3.5 text-red-400" />}
            {trend === 'flat' && <Minus className="w-3.5 h-3.5 text-slate-500" />}
            <span
              className={`text-xs font-semibold ${
                trend === 'up'
                  ? 'text-emerald-400'
                  : trend === 'down'
                  ? 'text-red-400'
                  : 'text-slate-500'
              }`}
            >
              {change > 0 ? '+' : ''}
              {change}%
            </span>
            {changeLabel && (
              <span className="text-[10px] text-slate-500 font-medium">{changeLabel}</span>
            )}
          </div>
        ) : changeLabel ? (
          <div className="flex items-center gap-1.5 mt-2">
            <span className="text-[10px] text-slate-400 font-medium">{changeLabel}</span>
          </div>
        ) : null}
      </div>

      {/* Hover glow effect */}
      <div className="absolute inset-0 rounded-2xl opacity-0 group-hover:opacity-100 transition-opacity duration-500 pointer-events-none"
        style={{
          background: `radial-gradient(circle at 50% 50%, ${iconBg.includes('blue') ? 'rgba(59,130,246,0.05)' : iconBg.includes('purple') ? 'rgba(139,92,246,0.05)' : iconBg.includes('emerald') ? 'rgba(16,185,129,0.05)' : 'rgba(245,158,11,0.05)'}, transparent 70%)`,
        }}
      />
    </div>
  );
}
