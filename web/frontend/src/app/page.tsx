'use client';

import { useEffect, useState } from 'react';
import {
  ScanSearch,
  Bot,
  Box,
  Zap,
  Activity,
  ShieldCheck,
  Database,
  HardDrive,
  LayoutDashboard,
  Clock
} from 'lucide-react';
import StatsCard from '@/components/dashboard/StatsCard';
import ActivityChart from '@/components/dashboard/ActivityChart';
import RecentAlerts from '@/components/dashboard/RecentAlerts';
import { healthAPI } from '@/lib/api';
import { useCrack } from '@/hooks/useCrack';

// Simple inner component for service status
function ServiceStatusCard({ name, status, latency, icon }: { name: string, status: string, latency: string, icon: React.ReactNode }) {
    return (
        <div className="flex items-center gap-3 p-4 rounded-xl bg-white border border-slate-100 shadow-sm hover:border-blue-200 transition-all">
            <div className={`w-8 h-8 rounded-lg flex items-center justify-center ${status === 'online' ? 'bg-emerald-50 text-emerald-600' : 'bg-red-50 text-red-600'}`}>
                {icon}
            </div>
            <div className="flex-1 min-w-0">
                <p className="text-[10px] font-bold text-slate-400 uppercase tracking-wider">{name}</p>
                <div className="flex items-center gap-2">
                    <span className={`text-xs font-black capitalize ${status === 'online' ? 'text-emerald-600' : 'text-red-600'}`}>{status}</span>
                    <span className="text-[10px] text-slate-300 font-medium">({latency})</span>
                </div>
            </div>
            <div className={`w-1.5 h-1.5 rounded-full ${status === 'online' ? 'bg-emerald-500 animate-pulse' : 'bg-red-500'}`} />
        </div>
    );
}

export default function DashboardPage() {
  const { stats, chartData } = useCrack();
  const [services, setServices] = useState<any[]>([]);
  const [systemStatus, setSystemStatus] = useState<string>('online');

  useEffect(() => {
    const fetchHealth = async () => {
      try {
        const { data } = await healthAPI.getSystemHealth();
        if (data) {
            if (data.services) {
                setServices(data.services);
            }
            if (data.status === 'healthy') {
                setSystemStatus('online');
            } else if (data.status === 'degraded') {
                setSystemStatus('degraded');
            } else {
                setSystemStatus('offline');
            }
        }
      } catch (e) {
        console.error("Health check failed", e);
        setSystemStatus('offline');
      }
    };
    fetchHealth();
    const interval = setInterval(fetchHealth, 30000);
    return () => clearInterval(interval);
  }, []);

  return (
    <div className="space-y-6 animate-fade-in">
      {/* Header Section */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-black text-slate-800 tracking-tight">Tổng quan Hệ thống</h1>
          <p className="text-xs text-slate-500 font-medium">Theo dõi tình trạng vận hành và phân tích AI thời gian thực</p>
        </div>
        <div className="flex items-center gap-3">
          <div className={`flex items-center gap-2 px-3 py-1.5 rounded-lg border ${
            systemStatus === 'online' ? 'bg-emerald-50 border-emerald-100 text-emerald-600' :
            systemStatus === 'degraded' ? 'bg-amber-50 border-amber-100 text-amber-600' :
            'bg-rose-50 border-rose-100 text-rose-600'
          }`}>
            <div className={`w-2 h-2 rounded-full animate-pulse ${
              systemStatus === 'online' ? 'bg-emerald-500' :
              systemStatus === 'degraded' ? 'bg-amber-500' :
              'bg-rose-500'
            }`} />
            <span className="text-[10px] font-bold uppercase tracking-wider">
              {systemStatus === 'online' ? 'Hệ thống Trực tuyến' :
               systemStatus === 'degraded' ? 'Hệ thống Cảnh báo' :
               'Hệ thống Ngoại tuyến'}
            </span>
          </div>
        </div>
      </div>

      {/* Stats Cards Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
        <StatsCard
          title="Tổng lượt Quét"
          value={stats?.totalScans?.toLocaleString() || '0'}
          changeLabel="hiện có trong hệ thống"
          icon={<ScanSearch className="w-5 h-5 text-blue-600" />}
          iconBg="bg-blue-50"
          delay={100}
        />
        <StatsCard
          title="Vết nứt Phát hiện"
          value={stats?.cracksDetected?.toLocaleString() || '0'}
          changeLabel="tổng số phát hiện"
          icon={<Zap className="w-5 h-5 text-purple-600" />}
          iconBg="bg-purple-50"
          delay={200}
        />
        <StatsCard
          title="Phiên Chat AI"
          value={stats?.chatSessions?.toLocaleString() || '0'}
          changeLabel="phiên hội thoại AI"
          icon={<Bot className="w-5 h-5 text-emerald-600" />}
          iconBg="bg-emerald-50"
          delay={300}
        />
        <StatsCard
          title="Hiệu suất Hệ thống"
          value={`${stats?.performance || 0}%`}
          changeLabel="tỉ lệ hoàn thành"
          icon={<Activity className="w-5 h-5 text-amber-600" />}
          iconBg="bg-amber-50"
          delay={400}
        />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Main Chart Area */}
        <div className="lg:col-span-2 space-y-6">
          <ActivityChart data={chartData} />
          
          {/* Service Status Sub-grid */}
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <ServiceStatusCard 
                name="AI Detection" 
                status={services.some(s => (s.service?.includes("Crack") || s.service?.includes("Detection")) && s.status === 'healthy') ? "online" : "offline"} 
                latency={services.find(s => s.service?.includes("Crack"))?.response_time_ms ? `${services.find(s => s.service?.includes("Crack"))?.response_time_ms}ms` : "N/A"}
                icon={<ShieldCheck className="w-4 h-4" />}
            />
            <ServiceStatusCard 
                name="RAG Knowledge" 
                status={services.some(s => (s.service?.includes("RAGFlow") || s.service?.includes("RAG")) && s.status === 'healthy') ? "online" : "offline"} 
                latency={services.find(s => s.service?.includes("RAGFlow") || s.service?.includes("RAG"))?.response_time_ms ? `${services.find(s => s.service?.includes("RAGFlow") || s.service?.includes("RAG"))?.response_time_ms}ms` : "N/A"}
                icon={<Database className="w-4 h-4" />}
            />
            <ServiceStatusCard 
                name="Cơ sở dữ liệu" 
                status={services.some(s => s.service === 'Database' && s.status === 'healthy') ? "online" : "offline"} 
                latency={services.find(s => s.service === 'Database')?.response_time_ms ? `${services.find(s => s.service === 'Database')?.response_time_ms}ms` : "N/A"}
                icon={<HardDrive className="w-4 h-4" />}
            />
          </div>
        </div>

        {/* Sidebar Widgets Area */}
        <div className="space-y-6">
          <RecentAlerts />
        </div>
      </div>
    </div>
  );
}
