import { AlertTriangle, Info, AlertCircle, Clock } from 'lucide-react';
import { useCrack } from '@/hooks/useCrack';
import { useRouter } from 'next/navigation';

const typeConfig = {
  critical: {
    icon: AlertCircle,
    iconColor: 'text-red-600',
    bg: 'bg-red-50',
    border: 'border-red-100',
    dot: 'red',
  },
  warning: {
    icon: AlertTriangle,
    iconColor: 'text-amber-600',
    bg: 'bg-amber-50',
    border: 'border-amber-100',
    dot: 'yellow',
  },
  info: {
    icon: Info,
    iconColor: 'text-blue-600',
    bg: 'bg-blue-50',
    border: 'border-blue-100',
    dot: 'green',
  },
};

export default function RecentAlerts() {
  const { alerts } = useCrack();
  const router = useRouter();

  if (!alerts || alerts.length === 0) {
    return (
      <div className="glass-card p-6 flex flex-col items-center justify-center text-center py-12">
        <div className="w-12 h-12 rounded-full bg-slate-50 flex items-center justify-center mb-3">
          <Clock className="w-6 h-6 text-slate-300" />
        </div>
        <p className="text-xs font-bold text-slate-400 uppercase tracking-widest">Không có thông báo mới</p>
      </div>
    );
  }

  return (
    <div className="glass-card p-6">
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-sm font-bold text-slate-800">Cảnh báo Gần đây</h3>
        <button 
          onClick={() => router.push('/incidents-map')}
          className="text-[10px] text-blue-600 hover:text-blue-700 font-bold transition-colors"
        >
          Xem tất cả
        </button>
      </div>

      <div className="space-y-3">
        {alerts.map((alert) => {
          const config = typeConfig[alert.type as keyof typeof typeConfig] || typeConfig.info;
          const Icon = config.icon;

          return (
            <div
              key={alert.id}
              onClick={() => {
                if (alert.task_id) {
                  router.push(`/crack-detection?task_id=${alert.task_id}`);
                } else {
                  const incId = (alert as any).incident_id || alert.id;
                  router.push(incId ? `/incidents-map?incident_id=${incId}` : '/incidents-map');
                }
              }}
              className={`flex items-start gap-3 p-3 rounded-xl ${config.bg} border ${config.border} transition-all duration-200 hover:scale-[1.01] cursor-pointer shadow-sm hover:shadow-md`}
            >
              <Icon className={`w-4 h-4 ${config.iconColor} shrink-0 mt-0.5`} />
              <div className="flex-1 min-w-0">
                <p className="text-xs font-bold text-slate-800 truncate">
                  {alert.title}
                </p>
                <p className="text-[10px] text-slate-500 mt-0.5 line-clamp-2 font-medium">
                  {alert.message}
                </p>
              </div>
              <div className="flex items-center gap-1 shrink-0">
                <Clock className="w-3 h-3 text-slate-400" />
                <span className="text-[9px] text-slate-400 whitespace-nowrap font-bold">
                  {alert.timestamp ? (() => {
                    const date = new Date(alert.timestamp);
                    const utcDate = alert.timestamp.includes('Z') || alert.timestamp.includes('+') 
                      ? date 
                      : new Date(alert.timestamp + 'Z');
                    return utcDate.toLocaleTimeString('vi-VN', { hour: '2-digit', minute: '2-digit', timeZone: 'Asia/Ho_Chi_Minh' });
                  })() : 'Vừa xong'}
                </span>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
