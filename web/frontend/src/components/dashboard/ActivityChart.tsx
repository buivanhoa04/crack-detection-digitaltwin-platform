'use client';

import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from 'recharts';

export default function ActivityChart({ data = [] }: { data?: any[] }) {
  const chartData = data.length > 0 ? data : [
    { date: 'T2', scans: 0, cracks: 0 },
    { date: 'T3', scans: 0, cracks: 0 },
    { date: 'T4', scans: 0, cracks: 0 },
    { date: 'T5', scans: 0, cracks: 0 },
    { date: 'T6', scans: 0, cracks: 0 },
    { date: 'T7', scans: 0, cracks: 0 },
    { date: 'CN', scans: 0, cracks: 0 },
  ];

  return (
    <div className="glass-card p-6">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h3 className="text-sm font-bold text-slate-800">Hoạt động Phân tích</h3>
          <p className="text-xs text-slate-400 mt-0.5 font-bold">7 ngày gần nhất</p>
        </div>
        <div className="flex items-center gap-4">
          <div className="flex items-center gap-1.5">
            <div className="w-2.5 h-2.5 rounded-full bg-blue-500 shadow-[0_0_8px_rgba(59,130,246,0.3)]" />
            <span className="text-[10px] text-slate-400 font-bold uppercase">Quét</span>
          </div>
          <div className="flex items-center gap-1.5">
            <div className="w-2.5 h-2.5 rounded-full bg-purple-500 shadow-[0_0_8px_rgba(139,92,246,0.3)]" />
            <span className="text-[10px] text-slate-400 font-bold uppercase">Vết nứt</span>
          </div>
        </div>
      </div>

      <div className="h-64">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={chartData}>
            <defs>
              <linearGradient id="colorScans" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor="#3b82f6" stopOpacity={0.2} />
                <stop offset="95%" stopColor="#3b82f6" stopOpacity={0} />
              </linearGradient>
              <linearGradient id="colorCracks" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor="#8b5cf6" stopOpacity={0.2} />
                <stop offset="95%" stopColor="#8b5cf6" stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" vertical={false} />
            <XAxis
              dataKey="date"
              stroke="#94a3b8"
              fontSize={10}
              tickLine={false}
              axisLine={false}
              dy={10}
            />
            <YAxis
              stroke="#94a3b8"
              fontSize={10}
              tickLine={false}
              axisLine={false}
            />
            <Tooltip
              contentStyle={{
                backgroundColor: 'rgba(255, 255, 255, 0.95)',
                border: '1px solid rgba(148,163,184,0.1)',
                borderRadius: '16px',
                padding: '12px',
                boxShadow: '0 10px 40px rgba(0,0,0,0.05)',
                backdropFilter: 'blur(10px)',
              }}
              labelStyle={{ color: '#64748b', fontSize: 11, fontWeight: 'bold', marginBottom: '4px' }}
              itemStyle={{ fontSize: 11, fontWeight: 'bold' }}
            />
            <Area
              type="monotone"
              dataKey="scans"
              stroke="#3b82f6"
              strokeWidth={2}
              fill="url(#colorScans)"
              name="Lượt quét"
            />
            <Area
              type="monotone"
              dataKey="cracks"
              stroke="#8b5cf6"
              strokeWidth={2}
              fill="url(#colorCracks)"
              name="Vết nứt"
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
