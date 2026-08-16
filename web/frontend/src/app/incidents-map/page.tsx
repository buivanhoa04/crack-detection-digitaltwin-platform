'use client';

import { useState, useEffect } from 'react';
import { useSearchParams } from 'next/navigation';
import dynamic from 'next/dynamic';
import { useAuth } from '@/hooks/useAuth';
import { incidentsAPI, surveysAPI, alignmentAPI, segmentsAPI } from '@/lib/api';
import type { Incident } from '@/types';
import { translateAIClass, translateTitle, TCVN_GRADES, REPAIR_STATUSES, translateLanes } from '@/lib/translate';
import {
  MapIcon,
  AlertTriangle,
  Clock,
  MapPin,
  Info,
  CheckCircle2,
  Calendar,
  X,
  Loader2,
  Settings2,
  Database,
  Cpu,
  Route,
  Download,
  GitCompare,
  Eye,
  EyeOff,
  Sparkles,
  History,
  TrendingDown,
  FileText
} from 'lucide-react';
import { format } from 'date-fns';
import { withAccessToken } from '@/lib/mediaAuth';

const safeFormatDate = (dateVal: any, formatStr: string = 'dd/MM/yyyy') => {
  if (!dateVal) return '--/--/----';
  try {
    const d = new Date(dateVal);
    if (isNaN(d.getTime())) return '--/--/----';
    return format(d, formatStr);
  } catch {
    return '--/--/----';
  }
};

const MiniMapPicker = dynamic(() => import('@/components/map/MiniMapPicker'), { ssr: false });

// Dynamic import for Leaflet map turning off SSR
const MapComponent = dynamic(() => import('@/components/map/MapComponent'), {
  ssr: false,
  loading: () => (
    <div className="w-full h-full bg-slate-900 animate-pulse rounded-xl flex items-center justify-center">
      <MapIcon className="w-8 h-8 text-slate-700 animate-bounce" />
    </div>
  ),
});

// ── COMPONENT 1: ALIGNMENT MAP (BÌNH ĐỒ 2D SVG) ─────────

function AlignmentMap({ 
  data, 
  selectedId, 
  onSelect, 
  startKm, 
  endKm, 
  onShowEvolution 
}: { 
  data: any; 
  selectedId: string | null; 
  onSelect: (id: string) => void; 
  startKm: number; 
  endKm: number;
  onShowEvolution: () => void;
}) {
  if (!data || !data.incidents) return null;
  const incidents = data.incidents;
  const lengthKm = endKm - startKm;

  if (lengthKm <= 0) {
    return (
      <div className="w-full h-full flex items-center justify-center bg-slate-950 border border-white/5 rounded-2xl p-10 text-center">
        <p className="text-sm font-bold text-slate-500 uppercase tracking-widest">Độ dài đợt khảo sát không hợp lệ (Km bắt đầu trùng hoặc lớn hơn Km kết thúc)</p>
      </div>
    );
  }

  // Tỷ lệ X/Y của SVG
  const getX = (km: number) => {
    const ratio = (km - startKm) / lengthKm;
    return 60 + ratio * 880; // Margin 60px hai bên
  };

  const getY = (offsetM: number) => {
    // 1 mét lệch tim = 4.5 pixel. Kẹp offset từ -10m đến 10m
    const clampedOffset = Math.max(-10, Math.min(10, offsetM));
    return 100 + clampedOffset * 4.5;
  };

  // Tạo các mốc Km chẵn
  const ticks = [];
  const startInt = Math.ceil(startKm);
  const endInt = Math.floor(endKm);
  for (let k = startInt; k <= endInt; k++) {
    ticks.push(k);
  }

  return (
    <div className="w-full bg-slate-900 border border-white/10 rounded-2xl p-6 flex flex-col justify-between h-full relative z-10 shadow-2xl">
      <div className="mb-4 flex flex-col sm:flex-row justify-between items-start sm:items-center gap-3">
        <div>
          <h3 className="text-sm font-bold text-white uppercase tracking-wider flex items-center gap-2">
            <Route className="w-4 h-4 text-blue-500" />
            Bình đồ duỗi phẳng 2D — Tuyến {data.route_name}
          </h3>
          <p className="text-[10px] text-slate-400">Định vị hư hỏng theo Km lý trình dọc tuyến và khoảng cách lệch tim.</p>
        </div>
        <div className="flex gap-2">
          <button
            onClick={onShowEvolution}
            className="flex items-center gap-1.5 px-3 py-2 bg-slate-800 hover:bg-slate-700 text-slate-300 font-bold rounded-xl text-xs transition active:scale-95 border border-white/5 shadow-sm"
          >
            <GitCompare className="w-3.5 h-3.5" /> So sánh Tiến triển
          </button>
          <a
            href={alignmentAPI.getDxfUrl(data.survey_id)}
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-center gap-1.5 px-4 py-2 bg-blue-600 hover:bg-blue-500 text-white font-bold rounded-xl text-xs transition active:scale-95 shadow-md"
          >
            <Download className="w-3.5 h-3.5" /> Xuất bản vẽ CAD
          </a>
        </div>
      </div>
      
      {/* Canvas vẽ tuyến */}
      <div className="flex-1 min-h-[300px] flex items-center justify-center relative bg-slate-950 rounded-xl border border-white/5 p-4 overflow-x-auto">
        <svg viewBox="0 0 1000 200" className="w-full min-w-[800px] h-auto block select-none">
          {/* Làn ngoài trái Y = 55 */}
          <line x1="50" y1="55" x2="950" y2="55" stroke="#334155" strokeWidth="1" strokeDasharray="3,3" />
          
          {/* Mép đường trái Y = 70 */}
          <line x1="50" y1="70" x2="950" y2="70" stroke="#475569" strokeWidth="2" />
          
          {/* Dải phân cách làn trái Y = 85 */}
          <line x1="50" y1="85" x2="950" y2="85" stroke="#334155" strokeWidth="1" strokeDasharray="4,4" />

          {/* Tim đường liền màu trắng Y = 100 */}
          <line x1="50" y1="100" x2="950" y2="100" stroke="#f1f5f9" strokeWidth="3" />

          {/* Dải phân cách làn phải Y = 115 */}
          <line x1="50" y1="115" x2="950" y2="115" stroke="#334155" strokeWidth="1" strokeDasharray="4,4" />

          {/* Mép đường phải Y = 130 */}
          <line x1="50" y1="130" x2="950" y2="130" stroke="#475569" strokeWidth="2" />

          {/* Làn ngoài phải Y = 145 */}
          <line x1="50" y1="145" x2="950" y2="145" stroke="#334155" strokeWidth="1" strokeDasharray="3,3" />

          {/* Ticks chia chẵn Km */}
          {ticks.map(k => {
            const x = getX(k);
            return (
              <g key={k}>
                <line x1={x} y1="90" x2={x} y2="110" stroke="#cbd5e1" strokeWidth="2" />
                <text x={x} y="45" textAnchor="middle" fill="#94a3b8" className="text-[10px] font-mono font-black">Km{k}</text>
              </g>
            );
          })}

          {/* Ticks nhỏ mỗi 100m */}
          {Array.from({ length: Math.ceil(lengthKm * 10) }).map((_, idx) => {
            const km = startKm + idx * 0.1;
            if (km > endKm || Math.abs(km - Math.round(km)) < 0.01) return null;
            const x = getX(km);
            return (
              <line key={idx} x1={x} y1="95" x2={x} y2="105" stroke="#475569" strokeWidth="1" />
            );
          })}

          {/* Sự cố trên tuyến */}
          {incidents.map((inc: any) => {
            const x = getX(inc.route_km);
            const y = getY(inc.offset_m);
            const isSelected = selectedId === inc.id;
            const color = inc.severity === 'critical' || inc.severity === 'danger' ? '#ef4444' : (inc.severity === 'warning' ? '#f59e0b' : '#3b82f6');
            
            return (
              <g 
                key={inc.id} 
                className="cursor-pointer group"
                onClick={() => onSelect(inc.id)}
              >
                {/* Pulse hiệu ứng khi chọn */}
                {isSelected && (
                  <circle cx={x} cy={y} r="12" fill={color} opacity="0.4" className="animate-ping" style={{ transformOrigin: `${x}px ${y}px` }} />
                )}
                {/* Marker tam giác xoay chóp */}
                <polygon
                  points={`${x},${y-7} ${x-6},${y+4} ${x+6},${y+4}`}
                  fill={isSelected ? '#ffffff' : color}
                  stroke={isSelected ? color : '#020617'}
                  strokeWidth="2"
                  className="transition-transform duration-300 group-hover:scale-125"
                  style={{ transformOrigin: `${x}px ${y}px` }}
                />
                <title>{`${translateTitle(inc.title)} - Km${inc.route_km} (Lệch ${inc.offset_m}m)`}</title>
              </g>
            );
          })}
        </svg>
      </div>

      {/* Chú giải */}
      <div className="mt-4 flex flex-col sm:flex-row items-center justify-between gap-3 text-[10px] text-slate-400 font-medium">
        <div className="flex gap-4">
          <div className="flex items-center gap-1.5">
            <span className="w-2.5 h-2.5 bg-red-500 rounded-sm" /> Nguy cấp
          </div>
          <div className="flex items-center gap-1.5">
            <span className="w-2.5 h-2.5 bg-amber-500 rounded-sm" /> Cảnh báo
          </div>
          <div className="flex items-center gap-1.5">
            <span className="w-2.5 h-2.5 bg-blue-500 rounded-sm" /> Thông tin
          </div>
        </div>
        <div>
          <span>Tuyến: <strong className="text-white">{data.route_name}</strong> | Chiều dài: <strong className="text-white">{lengthKm.toFixed(2)} km</strong> (Km{startKm} - Km{endKm})</span>
        </div>
      </div>
    </div>
  );
}

// ── COMPONENT 2: EVOLUTION PANEL (SO SÁNH TIẾN TRIỂN) ─────

function EvolutionPanel({ data, onClose }: { data: any; onClose: () => void }) {
  if (!data) return null;
  const current = data.current_stats || {};
  const previous = data.previous_stats || {};
  const delta = data.delta || {};
  const compareName = data.compare_survey_name || 'Kỳ trước';
  
  const renderDelta = (val: number) => {
    if (val > 0) return <span className="text-red-500 font-extrabold">+{val} (Tăng 📈)</span>;
    if (val < 0) return <span className="text-emerald-400 font-extrabold">{val} (Đã xử lý 📉)</span>;
    return <span className="text-slate-500 font-bold">0</span>;
  };

  return (
    <div className="fixed top-[6.25rem] right-0 bottom-0 w-[26rem] bg-slate-950/95 backdrop-blur-xl border-l border-white/10 z-[500] shadow-2xl flex flex-col p-6 animate-in slide-in-from-right duration-300 text-white overflow-hidden">
      <div className="flex items-center justify-between border-b border-white/10 pb-4 mb-5">
        <div>
          <h2 className="text-sm font-bold flex items-center gap-2">
            <GitCompare className="w-4 h-4 text-blue-500" />
            Tiến triển & Biến động Hư hỏng
          </h2>
          <p className="text-[10px] text-slate-400">Theo dõi tốc độ xuống cấp giữa các kỳ bảo trì.</p>
        </div>
        <button onClick={onClose} className="p-1.5 rounded-lg text-slate-400 hover:text-white hover:bg-slate-800 transition">
          <X className="w-5 h-5" />
        </button>
      </div>

      <div className="flex-1 overflow-y-auto space-y-6 pr-1 custom-scrollbar text-xs">
        {/* Thông tin đợt đối chiếu */}
        <div className="bg-slate-900/60 rounded-xl p-4 border border-white/5 space-y-2.5">
          <div className="flex justify-between">
            <span className="text-slate-400">Đợt hiện tại:</span>
            <span className="font-bold text-white">{data.survey_name}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-slate-400">Đợt so chiếu:</span>
            <span className="font-bold text-blue-400">{compareName}</span>
          </div>
        </div>

        {/* Tổng số incidents */}
        <div className="space-y-2">
          <h3 className="text-[10px] font-bold text-slate-400 uppercase tracking-widest pl-1">Tổng Số lượng Sự cố</h3>
          <div className="grid grid-cols-3 gap-3">
            <div className="bg-slate-900 p-3 rounded-xl text-center border border-white/5">
              <p className="text-[8px] text-slate-400 uppercase font-semibold">Kỳ trước</p>
              <p className="text-base font-black text-slate-300">{previous.total_incidents ?? 0}</p>
            </div>
            <div className="bg-slate-900 p-3 rounded-xl text-center border border-white/5">
              <p className="text-[8px] text-slate-400 uppercase font-semibold">Kỳ này</p>
              <p className="text-base font-black text-white">{current.total_incidents ?? 0}</p>
            </div>
            <div className="bg-slate-900 p-3 rounded-xl text-center border border-white/5 flex flex-col justify-center">
              <p className="text-[8px] text-slate-400 uppercase font-semibold">Biến động</p>
              <p className="text-[10px] font-mono">{renderDelta(delta.incidents ?? 0)}</p>
            </div>
          </div>
        </div>

        {/* Phân nhóm theo độ nguy cấp */}
        <div className="space-y-3">
          <h3 className="text-[10px] font-bold text-slate-400 uppercase tracking-widest pl-1">Theo Mức độ Cảnh báo</h3>
          {['critical', 'warning', 'info'].map(sev => {
            const currCount = current.by_severity?.[sev] || 0;
            const compCount = previous.by_severity?.[sev] || 0;
            const diff = delta.by_severity?.[sev] || 0;
            if (currCount === 0 && compCount === 0) return null;
            
            const label = sev === 'critical' ? 'Nguy cấp' : (sev === 'warning' ? 'Cảnh báo' : 'Thông tin');
            const colorClass = sev === 'critical' ? 'text-red-500' : (sev === 'warning' ? 'text-yellow-500' : 'text-blue-500');
            
            return (
              <div key={sev} className="flex items-center justify-between bg-slate-900 p-3.5 rounded-xl border border-white/5">
                <span className={`font-bold ${colorClass}`}>{label}</span>
                <div className="flex items-center gap-4">
                  <span className="text-slate-400 font-mono">{compCount} → {currCount}</span>
                  <span className="font-mono">{renderDelta(diff)}</span>
                </div>
              </div>
            );
          })}
        </div>

        {/* Biến động theo loại hư hỏng */}
        <div className="space-y-3">
          <h3 className="text-[10px] font-bold text-slate-400 uppercase tracking-widest pl-1">Theo Loại Hư hỏng AI</h3>
          <div className="space-y-2">
            {Object.keys(delta.by_classification || {}).map(cls => {
              const diff = delta.by_classification[cls];
              const currCount = current.by_classification?.[cls] || 0;
              const compCount = previous.by_classification?.[cls] || 0;
              const viName = translateAIClass(cls);
              
              return (
                <div key={cls} className="flex justify-between items-center bg-slate-900 p-3.5 rounded-xl border border-white/5">
                  <span className="font-semibold text-slate-300 truncate pr-2">{viName}</span>
                  <div className="flex items-center gap-4 shrink-0">
                    <span className="text-slate-400 font-mono">{compCount} → {currCount}</span>
                    <span className="font-mono">{renderDelta(diff)}</span>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </div>
    </div>
  );
}

// ── MAIN SCREEN PAGE ───────────────────────────────────

export default function IncidentsMapPage() {
  const { user } = useAuth();
  const isAdmin = user?.role === 'admin';

  const searchParams = useSearchParams();
  const queryIncidentId = searchParams?.get('incident_id');

  const [incidents, setIncidents] = useState<Incident[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedIncidentId, setSelectedIncidentId] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<'pending' | 'resolved'>('pending');

  useEffect(() => {
    if (queryIncidentId) {
      setSelectedIncidentId(queryIncidentId);
    }
  }, [queryIncidentId]);
  
  // Edit Modal State
  const [showEditModal, setShowEditModal] = useState(false);
  const [editForm, setEditForm] = useState<any>(null);
  const [isSaving, setIsSaving] = useState(false);
  
  // Filters & State
  const [surveyFilter, setSurveyFilter] = useState<string>('all');
  const [routeFilter, setRouteFilter] = useState<string>('');
  const [surveys, setSurveys] = useState<any[]>([]);

  // v4.0: View Mode Toggle (Map vs Alignment Map)
  const [viewMode, setViewMode] = useState<'map' | 'alignment'>('map');
  const [alignmentData, setAlignmentData] = useState<any>(null);
  const [loadingAlignment, setLoadingAlignment] = useState(false);

  // v4.0: Defect Evolution Compare Sidebar
  const [showEvolution, setShowEvolution] = useState(false);
  const [evolutionData, setEvolutionData] = useState<any>(null);
  const [loadingEvolution, setLoadingEvolution] = useState(false);

  // Digital Twin PMS States (Phases 2-4)
  const [routes, setRoutes] = useState<any[]>([]);
  const [selectedRouteId, setSelectedRouteId] = useState<string | null>(null);
  const [segments, setSegments] = useState<any[]>([]);
  const [selectedSegmentId, setSelectedSegmentId] = useState<string | null>(null);
  const [selectedSegment, setSelectedSegment] = useState<any>(null);
  const [segmentHistory, setSegmentHistory] = useState<any[]>([]);
  const [segmentPredictions, setSegmentPredictions] = useState<any[]>([]);
  const [segmentReport, setSegmentReport] = useState<string | null>(null);
  const [loadingReport, setLoadingReport] = useState(false);
  const [activeSegmentTab, setActiveSegmentTab] = useState<'info' | 'history' | 'predictions' | 'report'>('info');

  // Fetch routes on mount
  useEffect(() => {
    Promise.all([segmentsAPI.getRoutes(), surveysAPI.getAll()])
      .then(([routesRes, surveysRes]) => {
        const activeRouteNames = new Set(
          (surveysRes.data?.surveys || [])
            .map((survey: any) => String(survey.route_name || '').trim().toLowerCase())
            .filter(Boolean)
        );
        const routeList = (routesRes.data || []).filter((route: any) => {
          const name = String(route.name || '').trim().toLowerCase();
          return activeRouteNames.size === 0 || activeRouteNames.has(name);
        });
        setRoutes(routeList);
        setSelectedRouteId(current => current && routeList.some((r: any) => r.route_id === current) ? current : null);
      })
      .catch(err => console.error("Failed to fetch synchronized survey routes", err));
  }, []);

  // Fetch segments when selectedRouteId changes
  useEffect(() => {
    if (selectedRouteId) {
      segmentsAPI.getRouteSegments(selectedRouteId)
        .then(({ data }) => setSegments(data || []))
        .catch(err => console.error("Failed to fetch segments", err));
    } else {
      setSegments([]);
      setSelectedSegmentId(null);
    }
  }, [selectedRouteId]);

  // Fetch segment details and history when selectedSegmentId changes
  useEffect(() => {
    if (selectedSegmentId) {
      // Get details
      segmentsAPI.getSegmentDetails(selectedSegmentId)
        .then(({ data }) => setSelectedSegment(data))
        .catch(err => console.error("Failed to fetch segment details", err));
      
      // Get history
      segmentsAPI.getSegmentHistory(selectedSegmentId)
        .then(({ data }) => setSegmentHistory(data || []))
        .catch(err => console.error("Failed to fetch segment history", err));

      // Get predictions
      segmentsAPI.getSegmentPredictions(selectedSegmentId)
        .then(({ data }) => setSegmentPredictions(data.predictions || []))
        .catch(err => console.error("Failed to fetch segment predictions", err));

      setSegmentReport(null);
      setActiveSegmentTab('info');
    } else {
      setSelectedSegment(null);
      setSegmentHistory([]);
      setSegmentPredictions([]);
      setSegmentReport(null);
    }
  }, [selectedSegmentId]);

  const handleGenerateReport = async () => {
    if (!selectedSegmentId) return;
    try {
      setLoadingReport(true);
      const { data } = await segmentsAPI.getSegmentReport(selectedSegmentId);
      setSegmentReport(data.report);
    } catch (err) {
      console.error(err);
      alert("Lỗi khi tải báo cáo kỹ thuật AI");
    } finally {
      setLoadingReport(false);
    }
  };

  useEffect(() => {
    surveysAPI.getAll().then(({ data }) => setSurveys(data.surveys || [])).catch(() => {});
  }, []);

  const fetchData = async () => {
    try {
      setLoading(true);
      const resIncidents = await incidentsAPI.getAll();
      setIncidents(resIncidents.data.incidents || []);
    } catch (err) {
      console.error('Failed to load map data', err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchData();
  }, []);

  // Fetch Alignment Data when switching to alignment mode
  const fetchAlignment = async (surveyId: string) => {
    if (!surveyId || surveyId === 'all') {
      setAlignmentData(null);
      return;
    }
    try {
      setLoadingAlignment(true);
      const { data } = await alignmentAPI.getAlignment(surveyId);
      setAlignmentData(data);
    } catch (err) {
      console.error('Failed to load alignment data', err);
      setAlignmentData(null);
    } finally {
      setLoadingAlignment(false);
    }
  };

  useEffect(() => {
    if (viewMode === 'alignment' && surveyFilter !== 'all') {
      fetchAlignment(surveyFilter);
    }
  }, [viewMode, surveyFilter]);

  // Fetch Evolution Comparison Data
  const fetchEvolution = async (surveyId: string) => {
    if (!surveyId || surveyId === 'all') return;
    try {
      setLoadingEvolution(true);
      const { data } = await alignmentAPI.getEvolution(surveyId);
      setEvolutionData(data);
    } catch (err) {
      console.error('Failed to load evolution data', err);
      setEvolutionData(null);
    } finally {
      setLoadingEvolution(false);
    }
  };

  useEffect(() => {
    if (showEvolution && surveyFilter !== 'all') {
      fetchEvolution(surveyFilter);
    }
  }, [showEvolution, surveyFilter]);

  const getImageUrl = (framePath: string) => {
    if (!framePath) return '';
    let cleanPath = framePath.replace(/\\/g, '/');
    
    const prefixesToRemove = [
      '/api/v1/files/', 
      'api/v1/files/', 
      '/files/', 
      'files/'
    ];
    
    for (const p of prefixesToRemove) {
      if (cleanPath.startsWith(p)) {
        cleanPath = cleanPath.substring(p.length);
        break; 
      }
    }

    cleanPath = cleanPath.replace(/^\//, '');
    return withAccessToken(`/api/v1/files/${cleanPath}`);
  };

  const handleDelete = async (id: string, e?: React.MouseEvent) => {
    if (e) e.stopPropagation();
    if (!window.confirm("Bạn có chắc chắn muốn xóa vĩnh viễn sự cố này khỏi bản đồ?")) return;
    
    // Instant optimistic state update
    setIncidents(prev => prev.filter(i => i.id !== id && (i as any)._id !== id));
    if (selectedIncidentId === id) setSelectedIncidentId(null);

    try {
      await incidentsAPI.delete(id);
      fetchData();
      if (surveyFilter !== 'all' && viewMode === 'alignment') {
        fetchAlignment(surveyFilter);
      }
    } catch (err) {
      console.warn("API delete completed or soft-logged:", err);
    }
  };

  const handleOpenEdit = (incident: Incident) => {
    setEditForm({ 
      ...incident,
      repaired_at: (incident as any).repaired_at || format(new Date(), 'yyyy-MM-dd'),
      route_name: (incident as any).route_name || '',
      route_km: (incident as any).route_km ?? null,
      lane_position: (incident as any).lane_position || '',
      tcvn_grade: (incident as any).tcvn_grade || '',
      repair_status: (incident as any).repair_status || 'detected',
      damage_area_m2: (incident as any).damage_area_m2 ?? null,
      damage_width_mm: (incident as any).damage_width_mm ?? null,
      repair_method: (incident as any).repair_method || '',
      repair_cost_vnd: (incident as any).repair_cost_vnd ?? null,
      contractor: (incident as any).contractor || '',
    });
    setShowEditModal(true);
  };

  const handleSaveEdit = async () => {
    if (!editForm) return;

    // Quy trình sửa đổi trạng thái bảo trì tuần tự
    const originalIncident = incidents.find(i => i.id === editForm.id);
    if (originalIncident && editForm.repair_status !== originalIncident.repair_status) {
        const currentIndex = REPAIR_STATUSES.findIndex(s => s.value === originalIncident.repair_status);
        const nextIndex = REPAIR_STATUSES.findIndex(s => s.value === editForm.repair_status);
        if (nextIndex < currentIndex) {
            alert("⚠️ Không thể quay ngược trạng thái quy trình!");
            return;
        }
        if (nextIndex > currentIndex + 1) {
            if (!confirm(`⚠️ Bạn đang nhảy bước quy trình (từ ${REPAIR_STATUSES[currentIndex].label} đến ${REPAIR_STATUSES[nextIndex].label}). Tiếp tục?`)) {
                return;
            }
        }
    }

    setIsSaving(true);
    try {
      await incidentsAPI.update(editForm.id, editForm);
      setShowEditModal(false);
      fetchData();
      if (surveyFilter !== 'all' && viewMode === 'alignment') {
        fetchAlignment(surveyFilter);
      }
    } catch (err) {
      alert("Lỗi khi cập nhật thông tin sự cố");
    } finally {
      setIsSaving(false);
    }
  };

  const handleUpdateStatus = async (id: string) => {
    if (activeTab === 'resolved') return;
    
    const note = window.prompt("Nhập ghi chú kết quả xử lý (ví dụ: Đã trám vết nứt):");
    if (note === null) return;

    try {
      await incidentsAPI.update(id, { 
        status: 'resolved',
        notes: note || 'Đã được xử lý bởi điều hành viên',
        repaired_at: new Date().toISOString()
      });
      setSelectedIncidentId(null);
      fetchData();
      if (surveyFilter !== 'all' && viewMode === 'alignment') {
        fetchAlignment(surveyFilter);
      }
    } catch (err) {
      alert("Lỗi khi cập nhật trạng thái");
    }
  };

  const [fullscreenImage, setFullscreenImage] = useState<string | null>(null);
  const [showAILayer, setShowAILayer] = useState(true);

  const selectedIncident = incidents.find((i) => i.id === selectedIncidentId);
  const isResolved = (inc: Incident) => inc.status === 'resolved' || (inc as any).severity === 'resolved';
  
  const pendingIncidents = incidents.filter(inc => !isResolved(inc));
  const resolvedIncidents = incidents.filter(inc => isResolved(inc));
  
  const filterIncidents = (list: Incident[]) => {
    return list.filter(inc => {
        if (inc.lat === null || inc.lat === undefined || inc.lng === null || inc.lng === undefined) return false;
        const matchSurvey = surveyFilter === 'all' || inc.survey_id === surveyFilter;
        const matchRoute = !routeFilter || (inc as any).route_name?.toLowerCase().includes(routeFilter.toLowerCase());
        return matchSurvey && matchRoute;
    });
  };

  const displayIncidents = filterIncidents(activeTab === 'pending' ? pendingIncidents : resolvedIncidents);

  const formatConfidence = (val: any) => {
    if (val === undefined || val === null) return 0;
    const num = parseFloat(val);
    if (isNaN(num)) return 0;
    return num <= 1 ? Math.round(num * 100) : Math.round(num);
  };

  const filteredPendingIncidents = filterIncidents(pendingIncidents);

  const mapIncidents = selectedIncident && isResolved(selectedIncident) 
    ? [...filteredPendingIncidents, selectedIncident] 
    : filteredPendingIncidents;

  const stats = {
    critical: filteredPendingIncidents.filter((i) => i.severity === 'critical' || i.severity === 'danger').length,
    warning: filteredPendingIncidents.filter((i) => i.severity === 'warning').length,
  };

  const activeSurvey = surveys.find(s => s.id === surveyFilter);

  return (
    <div className="h-[calc(100vh-6.25rem)] flex gap-6 overflow-hidden">
      {/* Zoom ảnh lớn + Layer BBoxes */}
      {fullscreenImage && (
        <div 
          className="fixed inset-0 z-[2000] bg-black/95 backdrop-blur-xl flex items-center justify-center p-10 animate-in fade-in duration-200"
          onClick={() => setFullscreenImage(null)}
        >
          <div className="absolute top-8 right-8 flex items-center gap-4" onClick={e => e.stopPropagation()}>
            <button 
              onClick={() => setShowAILayer(!showAILayer)}
              className={`flex items-center gap-2 px-5 py-2.5 rounded-full text-xs font-black uppercase tracking-widest transition-all border shadow-2xl ${showAILayer ? 'bg-emerald-500 border-emerald-400 text-white' : 'bg-slate-800 border-white/10 text-slate-400'}`}
            >
              <Cpu className="w-4 h-4" />
              AI LAYER {showAILayer ? 'ON' : 'OFF'}
            </button>
            <button 
              onClick={() => setFullscreenImage(null)}
              className="p-3 bg-white/10 hover:bg-white/20 rounded-full text-white transition-all border border-white/5"
            >
              <X className="w-5 h-5" />
            </button>
          </div>

          <div className="relative group shadow-2xl rounded-lg overflow-hidden animate-in zoom-in-95 duration-300 inline-block max-w-full" onClick={e => e.stopPropagation()}>
            <img 
              src={fullscreenImage} 
              alt="Fullscreen evidence" 
              className="max-h-[80vh] w-auto block object-contain" 
            />
            {showAILayer && (
               <svg viewBox="0 0 1 1" preserveAspectRatio="none" className="absolute inset-0 w-full h-full pointer-events-none">
                  {selectedIncident?.detections?.map((det: any, idx: number) => {
                     const isNormalized = Math.max(...(det.bbox || [0])) <= 1.05;
                     const bbox = isNormalized ? det.bbox : [det.bbox[0]/1920, det.bbox[1]/1080, det.bbox[2]/1920, det.bbox[3]/1080];
                     const polygon = det.polygon ? (isNormalized ? det.polygon : det.polygon.map((p: number[]) => [p[0]/1920, p[1]/1080])) : null;
                     
                     return (
                        <g key={idx}>
                           {polygon && Array.isArray(polygon) && polygon.length > 0 ? (
                              <polygon
                                 points={polygon.map((p: number[]) => `${p[0]},${p[1]}`).join(' ')}
                                 className="stroke-emerald-400 fill-emerald-400/25 stroke-2"
                                 style={{ strokeWidth: 0.005 }}
                              />
                           ) : (
                              bbox && bbox.length === 4 && (
                                 <rect
                                    x={bbox[0]}
                                    y={bbox[1]}
                                    width={bbox[2] - bbox[0]}
                                    height={bbox[3] - bbox[1]}
                                    className="stroke-emerald-400 fill-emerald-400/25 border-2"
                                    style={{ strokeWidth: 0.005 }}
                                 />
                              )
                           )}
                        </g>
                     );
                  })}
               </svg>
            )}
          </div>
        </div>
      )}

      {/* ── KHU VỰC HIỂN THỊ CHÍNH (BẢN ĐỒ HOẶC BÌNH ĐỒ) ────── */}
      <div className="flex-1 flex flex-col h-full gap-3 overflow-hidden">
        
        {/* Header Bar: Mode Switcher (Bản đồ GIS / Bình đồ 2D) */}
        <div className="bg-white border border-slate-200 rounded-2xl p-2.5 px-4 flex items-center justify-between shadow-sm shrink-0">
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 rounded-xl bg-blue-50 border border-blue-100 flex items-center justify-center text-blue-600">
              {viewMode === 'map' ? <MapIcon className="w-4 h-4" /> : <Route className="w-4 h-4" />}
            </div>
            <div>
              <h2 className="text-xs font-bold text-slate-800 uppercase tracking-wider">
                {viewMode === 'map' ? 'Bản đồ số GIS Không gian' : 'Bình đồ Tuyến 2D Duỗi phẳng'}
              </h2>
              <p className="text-[10px] text-slate-400 font-medium">
                {viewMode === 'map' ? 'Định vị tọa độ các sự cố trên nền bản đồ số' : 'Định vị hư hỏng dọc theo lý trình Km và khoảng cách lệch tim'}
              </p>
            </div>
          </div>

          {/* Clean Segmented Control Switcher */}
          <div className="flex bg-slate-100 p-1 rounded-xl border border-slate-200/80">
            <button
              onClick={() => setViewMode('map')}
              className={`flex items-center gap-1.5 px-3.5 py-1.5 rounded-lg text-xs font-bold transition-all ${
                viewMode === 'map' ? 'bg-blue-600 text-white shadow-md' : 'text-slate-600 hover:text-slate-900'
              }`}
            >
              <MapIcon className="w-3.5 h-3.5" /> Bản đồ GIS
            </button>
            <button
              onClick={() => setViewMode('alignment')}
              className={`flex items-center gap-1.5 px-3.5 py-1.5 rounded-lg text-xs font-bold transition-all ${
                viewMode === 'alignment' ? 'bg-blue-600 text-white shadow-md' : 'text-slate-600 hover:text-slate-900'
              }`}
            >
              <Route className="w-3.5 h-3.5" /> Bình đồ 2D
            </button>
          </div>
        </div>

        {/* Map / Alignment Container Area */}
        <div className="flex-1 relative rounded-2xl overflow-hidden border border-slate-200 shadow-md bg-slate-950">
          {viewMode === 'map' ? (
          <>
            <MapComponent
              incidents={mapIncidents}
              selectedIncidentId={selectedIncidentId}
              onSelectIncident={(id) => {
                setSelectedIncidentId(id);
                setSelectedSegmentId(null);
              }}
              segments={segments}
              selectedSegmentId={selectedSegmentId}
              onSelectSegment={(seg) => {
                setSelectedSegmentId(seg.segment_id);
                setSelectedIncidentId(null);
              }}
            />

            {/* HUD nổi trên Map */}
            <div className="absolute top-4 left-4 z-[400] flex gap-3">
              <div className="bg-white/85 backdrop-blur-md px-4 py-2 border border-slate-200/60 rounded-xl flex items-center gap-3 shadow-md">
                <div className="flex items-center gap-2">
                  <span className="w-2.5 h-2.5 rounded-full bg-red-500 animate-pulse" />
                  <span className="text-sm font-bold text-slate-800">{stats.critical} Nguy cấp</span>
                </div>
                <div className="w-px h-4 bg-slate-200" />
                <div className="flex items-center gap-2">
                  <span className="w-2.5 h-2.5 rounded-full bg-yellow-500" />
                  <span className="text-sm font-semibold text-slate-600">{stats.warning} Cảnh báo</span>
                </div>
              </div>
            </div>
          </>
        ) : (
          <div className="w-full h-full p-2 flex flex-col justify-between">
            {surveyFilter === 'all' ? (
              <div className="flex-1 flex flex-col items-center justify-center text-center p-10 bg-slate-900 border border-white/10 rounded-2xl">
                <Database className="w-14 h-14 text-slate-500 mb-4 animate-bounce" />
                <h3 className="text-base font-black text-white uppercase tracking-wider mb-2">Chưa chọn đợt khảo sát</h3>
                <p className="text-xs text-slate-400 max-w-sm">Vui lòng chọn một đợt khảo sát cụ thể ở danh sách bộ lọc bên phải để xem dữ liệu tuyến bình đồ duỗi phẳng.</p>
              </div>
            ) : loadingAlignment ? (
              <div className="flex-1 flex flex-col items-center justify-center text-slate-400 gap-3">
                <Loader2 className="w-8 h-8 text-blue-500 animate-spin" />
                <p className="text-xs uppercase font-bold tracking-widest">Đang tải và dựng hình học bình đồ tuyến...</p>
              </div>
            ) : alignmentData ? (
              <div className="flex-1">
                <AlignmentMap
                  data={alignmentData}
                  selectedId={selectedIncidentId}
                  onSelect={setSelectedIncidentId}
                  startKm={activeSurvey?.route_km_start || 0}
                  endKm={activeSurvey?.route_km_end || 1}
                  onShowEvolution={() => setShowEvolution(true)}
                />
              </div>
            ) : (
              <div className="flex-1 flex flex-col items-center justify-center text-center p-10 bg-slate-900 border border-white/10 rounded-2xl">
                <AlertTriangle className="w-14 h-14 text-red-500 mb-4" />
                <h3 className="text-base font-black text-white uppercase tracking-wider mb-2">Lỗi tải dữ liệu</h3>
                <p className="text-xs text-slate-400">Không thể kết nối hoặc không tìm thấy tọa độ tim đường cho đợt khảo sát này.</p>
              </div>
            )}
          </div>
        )}
      </div>
      </div>

      {/* ── BẢNG TÁC VỤ PHỤ BÊN PHẢI (SIDE PANEL) ────────────── */}
      <div className="w-[25rem] flex flex-col gap-6 overflow-hidden h-full">
        {selectedIncident ? (
          <div className="glass-card flex-1 flex flex-col bg-white/80 border-slate-200/60 text-slate-800 backdrop-blur-md overflow-hidden shadow-xl animate-fade-in relative">
            <div className="p-5 border-b border-slate-100 flex items-start justify-between bg-slate-50/50">
              <div className="flex-1 min-w-0 pr-2">
                <h2 className="text-base font-bold text-slate-800 leading-tight mb-2">
                  {translateTitle(selectedIncident.title)}
                </h2>
                <div className="flex flex-wrap gap-2">
                  <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md text-[9px] font-black uppercase tracking-wider border ${
                    selectedIncident.severity === 'critical' || selectedIncident.severity === 'danger' ? 'bg-red-50 text-red-600 border-red-100' :
                    selectedIncident.severity === 'warning' ? 'bg-yellow-50 text-yellow-600 border-yellow-100' :
                    'bg-emerald-50 text-emerald-600 border-emerald-100'
                  }`}>
                    {selectedIncident.severity === 'critical' || selectedIncident.severity === 'danger' ? 'Nguy cấp' : selectedIncident.severity === 'warning' ? 'Cảnh báo' : 'Thông tin'}
                  </span>
                  <span className="px-2 py-0.5 rounded-md bg-blue-50 text-blue-600 text-[9px] font-black uppercase border border-blue-100">
                    {translateAIClass((selectedIncident as any).classification || 'Hư hỏng AI')}
                  </span>
                </div>
              </div>
              <button onClick={() => setSelectedIncidentId(null)} className="p-1.5 rounded-lg text-slate-400 hover:text-slate-700 hover:bg-slate-100 transition">
                <X className="w-5 h-5" />
              </button>
            </div>

            <div className="flex-1 overflow-y-auto p-5 space-y-5 custom-scrollbar text-slate-700">
              {selectedIncident.images && selectedIncident.images.length > 0 ? (
                <div 
                  className="w-full aspect-video rounded-xl overflow-hidden border border-slate-200 bg-slate-100 relative group shadow-sm cursor-zoom-in"
                  onClick={() => setFullscreenImage(getImageUrl(selectedIncident.images[0]))}
                >
                  <img src={getImageUrl(selectedIncident.images[0])} alt="Incident View" className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-500" />
                  <div className="absolute inset-0 bg-gradient-to-t from-black/60 via-transparent to-transparent flex items-end p-4">
                    <div className="flex items-center gap-2">
                      <div className="w-1.5 h-1.5 rounded-full bg-blue-500 animate-pulse" />
                      <span className="text-[10px] text-white/90 font-bold uppercase tracking-widest italic">Nhấn để xem ảnh gốc</span>
                    </div>
                  </div>
                </div>
              ) : (
                <div className="w-full aspect-video rounded-xl bg-slate-50 flex items-center justify-center border border-dashed border-slate-200 opacity-70">
                   <p className="text-[10px] uppercase font-bold text-slate-400">Chưa có ảnh bằng chứng</p>
                </div>
              )}

              <div className="space-y-4">
                <div className="flex gap-4">
                  <div className="w-10 h-10 rounded-xl bg-slate-50 flex items-center justify-center shrink-0 border border-slate-100">
                    <MapPin className="w-5 h-5 text-slate-500" />
                  </div>
                  <div className="flex-1 min-w-0">
                    <p className="text-[10px] text-slate-400 font-bold uppercase tracking-wider mb-1">Địa điểm / Tọa độ</p>
                    <p className="text-sm text-slate-800 leading-tight font-semibold line-clamp-2 mb-1">{selectedIncident.address}</p>
                    <p className="text-[11px] text-blue-600 font-mono italic">{selectedIncident.lat.toFixed(6)}, {selectedIncident.lng.toFixed(6)}</p>
                  </div>
                </div>

                <div className="grid grid-cols-2 gap-4">
                   <div className="bg-slate-50 border border-slate-100 rounded-xl p-3">
                      <p className="text-[9px] text-slate-400 font-bold uppercase tracking-wider mb-1">Ngày phát hiện</p>
                      <p className="text-xs text-slate-700 font-bold">{safeFormatDate(selectedIncident.detected_at, 'dd/MM/yyyy')}</p>
                   </div>
                   <div className="bg-slate-50 border border-slate-100 rounded-xl p-3">
                      <p className="text-[9px] text-slate-400 font-bold uppercase tracking-wider mb-1">Người phê duyệt</p>
                      <p className="text-xs text-slate-700 font-bold truncate">{(selectedIncident as any).approved_by || 'Hệ thống'}</p>
                   </div>
                </div>
              </div>
              <div className="bg-slate-50 border border-slate-100 rounded-2xl p-4 shadow-sm">
                <h4 className="text-[10px] font-bold text-slate-400 uppercase tracking-widest mb-3 flex items-center justify-between">
                  Chi tiết kỹ thuật
                  <span className="text-blue-600 font-mono text-[9px] bg-blue-50 px-2 py-0.5 rounded">{(selectedIncident as any).asset_type === 'bridge' ? 'CÔNG TRÌNH CẦU' : 'ĐƯỜNG BỘ'}</span>
                </h4>

                {((selectedIncident as any).route_name || (selectedIncident as any).tcvn_grade || (selectedIncident as any).lane_position) && (
                  <div className="grid grid-cols-2 gap-3 mb-4">
                    {(selectedIncident as any).route_name && (
                      <div className="bg-white border border-slate-200/60 rounded-xl p-2 shadow-sm">
                        <p className="text-[8px] text-slate-400 uppercase font-bold">Tuyến</p>
                        <p className="text-xs text-slate-700 font-bold">{(selectedIncident as any).route_name}</p>
                      </div>
                    )}
                    {((selectedIncident as any).route_km !== undefined && (selectedIncident as any).route_km !== null && (selectedIncident as any).route_km >= 0) && (
                      <div className="bg-white border border-slate-200/60 rounded-xl p-2 shadow-sm">
                        <p className="text-[8px] text-slate-400 uppercase font-bold">Lý trình</p>
                        <p className="text-xs text-slate-700 font-bold">Km {(selectedIncident as any).route_km}</p>
                      </div>
                    )}
                    {(selectedIncident as any).lane_position && (
                      <div className="bg-white border border-slate-200/60 rounded-xl p-2 shadow-sm">
                        <p className="text-[8px] text-slate-400 uppercase font-bold">Làn đường</p>
                        <p className="text-xs text-slate-700 font-bold">{translateLanes((selectedIncident as any).lane_position)}</p>
                      </div>
                    )}
                    {(selectedIncident as any).tcvn_grade && (
                      <div className="bg-white border border-slate-200/60 rounded-xl p-2 shadow-sm">
                        <p className="text-[8px] text-slate-400 uppercase font-bold">TCVN</p>
                        <p className="text-xs font-black" style={{color: ({'A':'#10b981','B':'#3b82f6','C':'#eab308','D':'#f97316','E':'#ef4444'} as Record<string,string>)[(selectedIncident as any).tcvn_grade] || '#333'}}>
                          Hạng {(selectedIncident as any).tcvn_grade}
                        </p>
                      </div>
                    )}
                  </div>
                )}

                {(selectedIncident as any).repair_status && (selectedIncident as any).repair_status !== 'detected' && (
                  <div className="mb-4 p-3 bg-white border border-slate-200/60 rounded-xl shadow-sm">
                    <p className="text-[8px] text-slate-400 uppercase font-bold mb-2">Tiến trình xử lý</p>
                    <div className="flex items-center gap-1">
                      {REPAIR_STATUSES.map((rs, i) => {
                        const currentIdx = REPAIR_STATUSES.findIndex(r => r.value === (selectedIncident as any).repair_status);
                        const isActive = i <= currentIdx;
                        return (
                          <div key={rs.value} className="flex-1 flex flex-col items-center gap-1">
                            <div className={`w-full h-1 rounded-full ${isActive ? 'bg-blue-500' : 'bg-slate-100'}`} />
                            <span className="text-[7px] text-slate-400 font-bold">{rs.icon}</span>
                          </div>
                        );
                      })}
                    </div>
                    <p className="text-[9px] text-blue-600 font-bold mt-1 text-center">
                      {REPAIR_STATUSES.find(r => r.value === (selectedIncident as any).repair_status)?.label}
                    </p>
                  </div>
                )}

                <p className="text-sm text-slate-600 leading-relaxed italic mb-4">
                   "{selectedIncident.description || 'Không có mô tả chi tiết.'}"
                </p>
                <div className="grid grid-cols-2 gap-4 border-t border-slate-100 pt-4">
                   <div>
                     <p className="text-[9px] text-slate-400 uppercase font-bold">Kích thước (Dự kiến)</p>
                     <p className="text-lg font-black text-blue-600">{selectedIncident.crack_length_m || 0}<span className="text-xs ml-1 opacity-50">m</span></p>
                   </div>
                   <div>
                     <p className="text-[9px] text-slate-400 uppercase font-bold">Độ tin cậy AI</p>
                     <p className="text-lg font-black text-emerald-600">{formatConfidence(selectedIncident.confidence)}<span className="text-xs ml-1 opacity-50">%</span></p>
                   </div>
                </div>
              </div>

               {selectedIncident.notes && (
                <div className="bg-emerald-50 border border-emerald-100 rounded-xl p-4 flex items-start gap-4">
                  <CheckCircle2 className="w-5 h-5 text-emerald-600 shrink-0 mt-0.5" />
                  <div>
                    <h4 className="text-[10px] font-black text-emerald-700 uppercase tracking-widest mb-1">Ghi chú khắc phục</h4>
                    <p className="text-xs text-slate-600 italic mb-2 leading-relaxed">"{selectedIncident.notes}"</p>
                    {(selectedIncident as any).repaired_at && (
                      <p className="text-[9px] text-emerald-600/70 font-medium">Hoàn thành: {format(new Date((selectedIncident as any).repaired_at), 'dd/MM/yyyy')}</p>
                    )}
                  </div>
                </div>
              )}
            </div>
            
            {/* Cập nhật / Duyệt bảo dưỡng */}
            {isAdmin && (
               <div className="p-4 border-t border-slate-100 flex flex-col gap-3 bg-slate-50/50">
                  <div className="grid grid-cols-2 gap-3">
                    <button 
                      onClick={() => handleOpenEdit(selectedIncident)}
                      className="flex items-center gap-2 justify-center py-2.5 rounded-xl text-xs font-bold uppercase tracking-wider border border-slate-200 bg-white text-slate-600 hover:bg-slate-50 hover:text-slate-800 transition active:scale-95 shadow-sm"
                    >
                        Cập nhật
                    </button>
                    {!isResolved(selectedIncident) && (
                      <button 
                        onClick={() => handleUpdateStatus(selectedIncident.id)}
                        className="flex items-center gap-2 justify-center py-2.5 rounded-xl text-xs font-bold uppercase tracking-wider bg-emerald-500 text-white hover:bg-emerald-600 transition shadow-sm active:scale-95"
                      >
                         <CheckCircle2 className="w-4 h-4" /> Đã xong
                      </button>
                    )}
                  </div>
                  <button 
                    onClick={() => handleDelete(selectedIncident.id)}
                    className="w-full py-2.5 rounded-xl text-[10px] font-bold uppercase tracking-wider text-red-500 border border-red-200 hover:bg-red-50 transition-colors"
                  >
                    Xóa khỏi danh sách theo dõi
                  </button>
               </div>
            )}
          </div>
        ) : selectedSegment ? (
          <div className="glass-card flex-1 flex flex-col bg-white/80 border-slate-200/60 text-slate-800 backdrop-blur-md overflow-hidden shadow-xl animate-fade-in relative h-full">
            {/* Header */}
            <div className="p-5 border-b border-slate-100 flex items-start justify-between bg-slate-50/50">
              <div className="flex-1 min-w-0 pr-2">
                <span className="text-[9px] font-black uppercase text-blue-600 bg-blue-50 px-2 py-0.5 rounded border border-blue-100 tracking-wider">
                  Hồ sơ phân đoạn khảo sát
                </span>
                <h2 className="text-base font-bold text-slate-800 leading-tight mt-1.5">
                  Đoạn: {selectedSegment.name}
                </h2>
                <p className="text-[10px] text-slate-400 font-medium mt-1">
                  Tuyến: {routes.find(r => r.route_id === selectedSegment.route_id)?.name || selectedSegment.route_id}
                </p>
              </div>
              <button 
                onClick={() => setSelectedSegmentId(null)} 
                className="p-1.5 rounded-lg text-slate-400 hover:text-slate-700 hover:bg-slate-100 transition"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            {/* PCI Score Gauge Area */}
            <div className="p-5 border-b border-slate-100 bg-slate-50/20 flex items-center justify-between gap-4">
              <div className="flex-1">
                <p className="text-[9px] text-slate-400 font-bold uppercase tracking-wider mb-1">Chỉ số PCI Hiện tại</p>
                <div className="flex items-baseline gap-1.5">
                  <span className={`text-3xl font-black ${
                    selectedSegment.pci_current >= 85 ? 'text-emerald-500' :
                    selectedSegment.pci_current >= 55 ? 'text-amber-500' :
                    'text-red-500'
                  }`}>
                    {selectedSegment.pci_current?.toFixed(1) ?? '—'}
                  </span>
                  <span className="text-slate-400 text-xs font-semibold">/100.0</span>
                </div>
                <div className="mt-1.5 flex items-center gap-1.5">
                  <span className={`w-2 h-2 rounded-full ${
                    selectedSegment.pci_current >= 85 ? 'bg-emerald-500' :
                    selectedSegment.pci_current >= 55 ? 'bg-amber-500' :
                    'bg-red-500'
                  }`} />
                  <span className="text-xs font-bold text-slate-600">
                    Trạng thái: {
                      !Number.isFinite(selectedSegment.pci_current) ? 'Chưa đo PCI' :
                      selectedSegment.pci_current >= 85 ? 'Rất tốt' :
                      selectedSegment.pci_current >= 70 ? 'Tốt' :
                      selectedSegment.pci_current >= 55 ? 'Trung bình' :
                      selectedSegment.pci_current >= 40 ? 'Kém' : 'Rất kém'
                    }
                  </span>
                </div>
              </div>
              <div className="shrink-0">
                <div className={`w-16 h-16 rounded-2xl border flex flex-col items-center justify-center shadow-sm ${
                  selectedSegment.pci_current >= 85 ? 'bg-emerald-50/50 border-emerald-200 text-emerald-600' :
                  selectedSegment.pci_current >= 55 ? 'bg-amber-50/50 border-amber-200 text-amber-600' :
                  'bg-red-50/50 border-red-200 text-red-600'
                }`}>
                  <span className="text-[10px] font-black uppercase tracking-wider">PCI</span>
                  <span className="text-base font-black leading-none mt-1">
                    {!Number.isFinite(selectedSegment.pci_current) ? '—' :
                     selectedSegment.pci_current >= 85 ? 'A' :
                     selectedSegment.pci_current >= 70 ? 'B' :
                     selectedSegment.pci_current >= 55 ? 'C' :
                     selectedSegment.pci_current >= 40 ? 'D' : 'E'}
                  </span>
                </div>
              </div>
            </div>

            {/* Tabs Selector */}
            <div className="flex border-b border-slate-100 bg-slate-50/40 p-1">
              {([
                { id: 'info', label: 'Thông tin', icon: Info },
                { id: 'history', label: 'Lịch sử', icon: History },
                { id: 'predictions', label: 'Dự báo', icon: TrendingDown },
                { id: 'report', label: 'Báo cáo AI', icon: Sparkles }
              ] as const).map(tab => {
                const Icon = tab.icon;
                const isActive = activeSegmentTab === tab.id;
                return (
                  <button
                    key={tab.id}
                    onClick={() => setActiveSegmentTab(tab.id)}
                    className={`flex-1 flex items-center justify-center gap-1.5 py-2 rounded-xl text-[10px] font-bold uppercase transition-all ${
                      isActive 
                        ? 'bg-white text-blue-600 shadow-sm border border-slate-100' 
                        : 'text-slate-500 hover:text-slate-800'
                    }`}
                  >
                    <Icon className="w-3.5 h-3.5" />
                    {tab.label}
                  </button>
                );
              })}
            </div>

            {/* Tab Contents */}
            <div className="flex-1 overflow-y-auto p-5 custom-scrollbar space-y-4">
              {activeSegmentTab === 'info' && (
                <div className="space-y-4">
                  {/* Basic Specifications */}
                  <div className="grid grid-cols-2 gap-3">
                    <div className="bg-slate-50 border border-slate-100 rounded-xl p-3 shadow-sm">
                      <p className="text-[8px] text-slate-400 uppercase font-bold">Kết cấu</p>
                      <p className="text-xs text-slate-700 font-bold mt-1">{selectedSegment.structural_type || 'Chưa cập nhật'}</p>
                    </div>
                    <div className="bg-slate-50 border border-slate-100 rounded-xl p-3 shadow-sm">
                      <p className="text-[8px] text-slate-400 uppercase font-bold">Chiều dài</p>
                      <p className="text-xs text-slate-700 font-bold mt-1">
                        {(() => {
                          const start = selectedSegment.start_gps;
                          const end = selectedSegment.end_gps;
                          if (!start || !end) return 'Chưa có hình học đo đạc';
                          const R = 6371000;
                          const phi1 = start.lat * Math.PI / 180;
                          const phi2 = end.lat * Math.PI / 180;
                          const deltaPhi = (end.lat - start.lat) * Math.PI / 180;
                          const deltaLambda = (end.lng - start.lng) * Math.PI / 180;
                          const a = Math.sin(deltaPhi / 2) * Math.sin(deltaPhi / 2) +
                                    Math.cos(phi1) * Math.cos(phi2) *
                                    Math.sin(deltaLambda / 2) * Math.sin(deltaLambda / 2);
                          const c = 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
                          const distance = Math.round(R * c);
                          return `${distance} mét`;
                        })()}
                      </p>
                    </div>
                    <div className="bg-slate-50 border border-slate-100 rounded-xl p-3 shadow-sm col-span-2">
                      <p className="text-[8px] text-slate-400 uppercase font-bold">Vị trí làn đường</p>
                      <p className="text-xs text-slate-700 font-bold mt-1">{selectedSegment.lane || 'Chưa cập nhật'}</p>
                    </div>
                  </div>

                  {/* Automated Maintenance Recommendation (Phase 4 Matrix) */}
                  <div className="border border-blue-100 bg-blue-50/30 rounded-2xl p-4 space-y-2">
                    <h4 className="text-[10px] font-black text-blue-800 uppercase tracking-widest flex items-center gap-1.5">
                      <Cpu className="w-4 h-4 text-blue-500" />
                      ĐỀ XUẤT PHƯƠNG ÁN BẢO TRÌ (PMS)
                    </h4>
                    
                    {(() => {
                      const pci = selectedSegment.pci_current;
                      if (!Number.isFinite(pci)) {
                        return <p className="text-xs text-slate-500">Chưa có PCI đã đo nên hệ thống không sinh đề xuất bảo trì.</p>;
                      }
                      let recommendation = "";
                      let category = "";
                      
                      if (pci >= 85) {
                        category = "Bảo trì định kỳ / Thường xuyên";
                        recommendation = "Mặt đường chất lượng tốt. Tiến hành làm vệ sinh mặt đường, khơi thông hệ thống rãnh thoát nước, trám vá vết nứt chân chim rất nhỏ nếu phát hiện.";
                      } else if (pci >= 70) {
                        category = "Bảo dưỡng phòng ngừa";
                        recommendation = "Bảt đầu xuất hiện hư hỏng nhẹ. Trám khe nứt dọc/ngang đơn lẻ bằng bitum nóng để chống thấm nước (Crack sealing), vá các ổ gà nhỏ để chặn đứng suy thoái.";
                      } else if (pci >= 55) {
                        category = "Sửa chữa định kỳ / Sửa chữa vừa";
                        recommendation = "Hư hỏng mức độ trung bình. Khuyến nghị vá sửa mặt đường hư hỏng diện rộng cục bộ, láng nhựa chống thấm hoặc rải lớp microsurfacing bảo vệ mặt đường.";
                      } else {
                        category = "Sửa chữa lớn / Khôi phục cải tạo";
                        recommendation = "Mặt đường hư hỏng nghiêm trọng. Yêu cầu câo bóc tái sinh nguội tại chỗ hoặc câo bóc lớp bê tông nhựa cũ và thảm lại bê tông nhựa nóng polymer mới (Milling & Overlay).";
                      }
                      
                      return (
                        <div className="space-y-2 text-xs">
                          <p className="text-slate-700 leading-relaxed"><strong className="text-slate-800 font-bold">Cấp độ:</strong> {category}</p>
                          <p className="text-slate-600 leading-relaxed bg-white/70 rounded-xl p-2.5 border border-slate-100 border-dashed italic">"{recommendation}"</p>
                        </div>
                      );
                    })()}
                  </div>
                </div>
              )}

              {activeSegmentTab === 'history' && (
                <div className="space-y-3">
                  <h4 className="text-[10px] font-bold text-slate-400 uppercase tracking-widest pl-1">
                    Lịch sử chất lượng mặt đường
                  </h4>
                  {segmentHistory.length === 0 ? (
                    <div className="p-8 text-center bg-slate-50 rounded-2xl border border-dashed border-slate-200">
                      <Clock className="w-8 h-8 text-slate-400 mx-auto mb-2 opacity-50" />
                      <p className="text-xs text-slate-400 font-medium">Chưa có lịch sử đo đạc PCI trước đây.</p>
                    </div>
                  ) : (
                    <div className="space-y-2.5">
                      {segmentHistory.map((hist, idx) => (
                        <div key={idx} className="bg-slate-50 border border-slate-100 rounded-xl p-3.5 flex items-center justify-between shadow-sm">
                          <div>
                            <p className="text-[10px] text-slate-400 font-bold">{format(new Date(hist.survey_date), 'dd/MM/yyyy')}</p>
                            <p className="text-xs text-slate-500 mt-1">Diện tích nứt: {hist.total_crack_area_m2?.toFixed(2) ?? '0.00'} m²</p>
                          </div>
                          <span className={`text-sm font-black px-2 py-0.5 rounded ${
                            hist.pci_score >= 85 ? 'bg-emerald-50 text-emerald-600' :
                            hist.pci_score >= 55 ? 'bg-amber-50 text-amber-600' :
                            'bg-red-50 text-red-600'
                          }`}>
                            {hist.pci_score?.toFixed(1)}
                          </span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}

              {activeSegmentTab === 'predictions' && (
                <div className="space-y-4">
                  <h4 className="text-[10px] font-bold text-slate-400 uppercase tracking-widest pl-1">
                    Dự báo suy giảm PCI (Mô hình AASHTO)
                  </h4>
                  <p className="text-[10px] text-slate-500 leading-relaxed pl-1 -mt-2">
                    Dự kiến tốc độ xuống cấp chất lượng mặt đường dựa trên kết cấu kết hợp diện tích hư hại hiện thời.
                  </p>
                  
                  {segmentPredictions.length === 0 ? (
                    <div className="p-8 text-center bg-slate-50 rounded-2xl border border-dashed border-slate-200 animate-pulse">
                      <Loader2 className="w-6 h-6 text-slate-400 mx-auto animate-spin" />
                    </div>
                  ) : (
                    <div className="relative border-l-2 border-slate-200 ml-3.5 pl-5 space-y-5 py-2">
                      {/* Current point */}
                      <div className="relative">
                        <span className={`absolute -left-[27px] top-0.5 w-3.5 h-3.5 rounded-full border-2 border-white ring-2 ${
                          selectedSegment.pci_current >= 85 ? 'bg-emerald-500 ring-emerald-200' :
                          selectedSegment.pci_current >= 55 ? 'bg-amber-500 ring-amber-200' :
                          'bg-red-500 ring-red-200'
                        }`} />
                        <div>
                          <p className="text-xs font-black text-slate-800">Hiện tại</p>
                          <div className="flex items-center gap-2 mt-1">
                            <span className="text-xs text-slate-500">PCI:</span>
                            <span className="text-xs font-extrabold text-slate-700">{selectedSegment.pci_current?.toFixed(1)}</span>
                            <span className="text-[10px] text-slate-400">({
                              selectedSegment.pci_current >= 85 ? 'Rất tốt' :
                              selectedSegment.pci_current >= 70 ? 'Tốt' :
                              selectedSegment.pci_current >= 55 ? 'Trung bình' :
                              selectedSegment.pci_current >= 40 ? 'Kém' : 'Rất kém'
                            })</span>
                          </div>
                        </div>
                      </div>

                      {/* Predictions timeline */}
                      {segmentPredictions.map((pred, idx) => {
                        let colorClass = "bg-emerald-500 ring-emerald-200";
                        if (pred.predicted_pci < 55) {
                          colorClass = "bg-red-500 ring-red-200";
                        } else if (pred.predicted_pci < 85) {
                          colorClass = "bg-amber-500 ring-amber-200";
                        }
                        
                        return (
                          <div key={idx} className="relative">
                            <span className={`absolute -left-[27px] top-0.5 w-3.5 h-3.5 rounded-full border-2 border-white ring-2 ${colorClass}`} />
                            <div>
                              <p className="text-xs font-bold text-slate-700">Sau {pred.period}</p>
                              <div className="flex items-center gap-2 mt-1">
                                <span className="text-xs text-slate-400">Dự báo PCI:</span>
                                <span className="text-xs font-black text-slate-800">{pred.predicted_pci?.toFixed(1)}</span>
                                <span className="text-[10px] text-slate-500">({pred.condition})</span>
                              </div>
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  )}
                </div>
              )}

              {activeSegmentTab === 'report' && (
                <div className="space-y-4">
                  {segmentReport ? (
                    <div className="space-y-4">
                      <div className="flex items-center justify-between">
                        <span className="text-[9px] font-bold text-emerald-600 bg-emerald-50 border border-emerald-100 px-2 py-0.5 rounded tracking-wider">
                          BÁO CÁO KỸ THUẬT AI SẴN SÀNG
                        </span>
                        <button
                          onClick={() => {
                            const blob = new Blob([segmentReport], { type: 'text/markdown' });
                            const url = URL.createObjectURL(blob);
                            const link = document.createElement('a');
                            link.href = url;
                            link.download = `PMS_Report_${selectedSegmentId}.md`;
                            link.click();
                          }}
                          className="flex items-center gap-1 text-[10px] font-black text-blue-600 hover:text-blue-500 transition uppercase tracking-wider"
                        >
                          <Download className="w-3 h-3" /> Tải về (.md)
                        </button>
                      </div>
                      
                      <div className="bg-slate-900 border border-white/10 rounded-2xl p-4 text-[11px] text-slate-300 font-mono leading-relaxed max-h-[350px] overflow-y-auto whitespace-pre-wrap custom-scrollbar">
                        {segmentReport}
                      </div>
                    </div>
                  ) : (
                    <div className="p-8 text-center bg-slate-50 border border-dashed border-slate-200 rounded-2xl space-y-4">
                      <Sparkles className="w-8 h-8 text-blue-500 mx-auto animate-pulse" />
                      <div>
                        <h4 className="text-xs font-bold text-slate-800">Tự động lập báo cáo kiểm định PMS</h4>
                        <p className="text-[10px] text-slate-400 mt-1 leading-relaxed">
                          Hệ thống sẽ gom các dữ liệu lịch sử đo đạc, xu hướng suy thoái và ma trận quyết định để viết báo cáo kiểm định kỳ thuật tự động thông qua mô hình AI.
                        </p>
                      </div>
                      <button
                        onClick={handleGenerateReport}
                        disabled={loadingReport}
                        className="w-full flex items-center justify-center gap-2 py-3 bg-blue-600 hover:bg-blue-500 disabled:bg-blue-400 text-white font-bold rounded-xl text-xs transition shadow-md shadow-blue-500/10 active:scale-95"
                      >
                        {loadingReport ? (
                          <>
                            <Loader2 className="w-3.5 h-3.5 animate-spin" />
                            Đang khởi tạo...
                          </>
                        ) : (
                          <>
                            <Sparkles className="w-3.5 h-3.5 animate-bounce" />
                            Sinh báo cáo kỳ thuật (AI Report)
                          </>
                        )}
                      </button>
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>
        ) : (
          <div className="glass-card flex-1 flex flex-col bg-white/80 border-slate-200/60 text-slate-800 backdrop-blur-md overflow-hidden shadow-2xl transform transition-all">
            <div className="p-5 border-b border-slate-100 bg-slate-50/50">
               <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 mb-4">
                  <h2 className="text-base font-bold text-slate-800 tracking-tight">Hồ sơ Giám sát</h2>
                  <div className="flex bg-slate-100 p-1 rounded-xl border border-slate-200/50 shadow-inner max-w-fit">
                     <button 
                        onClick={() => setActiveTab('pending')}
                        className={`px-3 py-1 rounded-lg text-[9px] font-bold tracking-wider transition-all ${activeTab === 'pending' ? 'bg-white text-blue-600 shadow-sm' : 'text-slate-500 hover:text-slate-700'}`}
                     >
                        PENDING
                     </button>
                     <button 
                        onClick={() => setActiveTab('resolved')}
                        className={`px-3 py-1 rounded-lg text-[9px] font-bold tracking-wider transition-all ${activeTab === 'resolved' ? 'bg-white text-emerald-600 shadow-sm' : 'text-slate-500 hover:text-slate-700'}`}
                     >
                        RESOLVED
                     </button>
                  </div>
               </div>
               <div className="flex items-center gap-2">
                  <div className="w-1.5 h-1.5 rounded-full bg-blue-500 animate-pulse" />
                  <p className="text-[11px] text-slate-500 font-semibold">
                    {activeTab === 'pending' ? `${pendingIncidents.length} sự cố đang chờ điều phối.` : `${resolvedIncidents.length} trường hợp đã hoàn tất khắc phục.`}
                  </p>
               </div>
            </div>

            {/* v2.0: Business Filters (GAP-09) */}
            <div className="px-5 py-3.5 bg-slate-50/50 border-b border-slate-100 space-y-3">
               <div className="flex items-center gap-3">
                  <div className="flex-1 space-y-1">
                     <label className="text-[9px] font-bold text-slate-400 uppercase tracking-wider pl-1">Lọc theo Tuyến</label>
                     <div className="relative">
                        <Route className="absolute left-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-slate-400" />
                        <input 
                           value={routeFilter} 
                           onChange={e => setRouteFilter(e.target.value)}
                           className="w-full bg-white border border-slate-200 rounded-xl pl-9 pr-3 py-2 text-[11px] text-slate-700 font-semibold outline-none focus:border-blue-500/50 shadow-sm transition-all" 
                           placeholder="VD: QL1A..."
                        />
                     </div>
                  </div>
                  <div className="flex-1 space-y-1">
                     <label className="text-[9px] font-bold text-slate-400 uppercase tracking-wider pl-1">Lọc theo Đợt khảo sát</label>
                     <select 
                        value={surveyFilter} 
                        onChange={e => setSurveyFilter(e.target.value)}
                        className="w-full bg-white border border-slate-200 rounded-xl px-3 py-2 text-[11px] text-slate-700 font-semibold outline-none cursor-pointer focus:border-blue-500/50 shadow-sm transition-all"
                     >
                        <option value="all">Tất cả đợt</option>
                        {surveys.map(s => (
                        <option key={s.id} value={s.id}>{s.name}</option>
                        ))}
                     </select>
                  </div>
               </div>
               {/* Digital Twin Route Selection */}
               <div className="space-y-1 pt-1">
                  <label className="text-[9px] font-bold text-slate-400 uppercase tracking-wider pl-1 flex items-center gap-1">
                     <Cpu className="w-3 h-3 text-blue-500 animate-pulse" />
                     Tuyến Bản sao số / Digital Twin (PMS)
                  </label>
                  <select 
                     value={selectedRouteId || ''} 
                     onChange={e => setSelectedRouteId(e.target.value || null)}
                     className="w-full bg-white border border-slate-200 rounded-xl px-3 py-2 text-[11px] text-slate-700 font-semibold outline-none cursor-pointer focus:border-blue-500/50 shadow-sm transition-all"
                  >
                     <option value="">-- Chưa chọn tuyến --</option>
                     {routes.map(r => (
                        <option key={r.route_id} value={r.route_id}>{r.name} ({r.province})</option>
                     ))}
                  </select>
               </div>
            </div>
            
            <div className="flex-1 overflow-y-auto p-4 space-y-3 custom-scrollbar">
               {displayIncidents.length === 0 ? (
                  <div className="h-full flex flex-col items-center justify-center opacity-30 text-center p-12 space-y-4">
                     <Database className="w-12 h-12 text-slate-400" />
                     <p className="text-xs font-bold uppercase tracking-widest text-slate-500">Không có dữ liệu trong tệp</p>
                  </div>
               ) : (
                  displayIncidents.map(inc => {
                     const incSurvey = surveys.find(s => s.id === inc.survey_id);
                     return (
                        <div 
                           key={inc.id}
                           onClick={() => setSelectedIncidentId(inc.id)}
                           className={`w-full group p-4 rounded-2xl border transition-all duration-300 cursor-pointer ${
                              selectedIncidentId === inc.id 
                               ? 'bg-blue-50/80 border-blue-200/80 shadow-md ring-1 ring-blue-500/10' 
                               : 'bg-white border-slate-100 hover:border-slate-200 hover:bg-slate-50/60 shadow-sm'
                           }`}
                        >
                           <div className="flex items-start justify-between gap-3 mb-2">
                                <div className="min-w-0">
                                    <div className="flex items-center gap-2 flex-wrap">
                                      <h3 className={`text-xs font-bold tracking-tight leading-snug transition-colors ${selectedIncidentId === inc.id ? 'text-blue-600' : 'text-slate-800'}`}>
                                        {translateTitle(inc.title)}
                                      </h3>
                                      {inc.is_calibrated && (
                                        <span title="Đã hiệu chuẩn GSD tự động" className="px-1.5 py-0.5 bg-emerald-50 text-emerald-600 text-[8px] font-bold rounded border border-emerald-100">AI GSD</span>
                                      )}
                                    </div>
                                    <p className="text-[10px] text-slate-400 font-medium mt-1 truncate">
                                       Đợt: <span className="font-semibold text-slate-600">{incSurvey ? incSurvey.name : 'Tác vụ đơn lẻ'}</span>
                                    </p>
                                </div>
                                <div className="flex flex-col items-end gap-1.5 shrink-0">
                                    <span className={`w-2.5 h-2.5 rounded-full shadow-[0_0_8px_rgba(0,0,0,0.1)] ${
                                        inc.severity === 'critical' || inc.severity === 'danger' ? 'bg-red-500 ring-4 ring-red-100' :
                                        inc.severity === 'warning' ? 'bg-yellow-500 ring-4 ring-yellow-100' : 
                                        isResolved(inc) ? 'bg-emerald-500 ring-4 ring-emerald-100' : 'bg-blue-500'
                                    }`} />
                                    <span className="text-[9px] font-bold text-slate-400">{format(new Date(inc.detected_at), 'dd/MM')}</span>
                                </div>
                           </div>
                           <div className="flex items-center gap-3 mt-3 pt-2.5 border-t border-slate-100">
                              <MapPin className={`w-3.5 h-3.5 ${selectedIncidentId === inc.id ? 'text-blue-500' : 'text-slate-400'}`} />
                              <span className="text-[10px] text-slate-500 truncate italic font-medium">
                                {inc.address || (incSurvey ? `Đợt: ${incSurvey.name}` : (inc as any).route_name ? `Tuyến: ${(inc as any).route_name}` : `Vị trí: ${inc.lat?.toFixed(5)}, ${inc.lng?.toFixed(5)}`)}
                              </span>
                           </div>
                        </div>
                     );
                  })
               )}
            </div>
          </div>
        )}
      </div>

      {/* ── EDIT MODAL ────────────────────────────────────── */}
      {showEditModal && editForm && (
        <div className="fixed inset-0 z-[1000] flex items-center justify-center bg-slate-900/40 backdrop-blur-sm animate-in fade-in duration-300">
           <div className="bg-white border border-slate-200 w-full max-w-lg rounded-[2.5rem] overflow-hidden shadow-2xl animate-in zoom-in-95 duration-300">
              <div className="px-10 pt-10 pb-6 text-slate-800">
                 <div className="flex items-center justify-between mb-8">
                    <div>
                       <h2 className="text-xl font-bold text-slate-800 tracking-tight">Cập nhật Sự cố</h2>
                       <p className="text-xs text-slate-500 font-medium mt-1">Thông tin sẽ được cập nhật thời gian thực vào hồ sơ số của tài sản.</p>
                    </div>
                    <button onClick={() => setShowEditModal(false)} className="p-3 bg-slate-100 rounded-full text-slate-400 hover:text-slate-600 transition-colors">
                       <X className="w-5 h-5" />
                    </button>
                 </div>

                 <div className="space-y-6 max-h-[60vh] overflow-y-auto pr-2 custom-scrollbar">
                    <div className="space-y-2">
                       <label className="text-[10px] font-bold text-slate-400 uppercase tracking-wider pl-1">Tiêu đề Ghi nhận</label>
                       <input 
                          value={translateTitle(editForm.title)} onChange={e => setEditForm({...editForm, title: e.target.value})}
                          className="w-full bg-white border border-slate-200 rounded-2xl px-5 py-3 text-sm text-slate-800 focus:border-blue-50 transition-all outline-none font-bold shadow-sm" 
                       />
                    </div>

                    <div className="grid grid-cols-2 gap-5">
                       <div className="space-y-2">
                          <label className="text-[10px] font-bold text-slate-400 uppercase tracking-wider pl-1">Loại hạ tầng</label>
                          <select 
                             value={editForm.asset_type} onChange={e => setEditForm({...editForm, asset_type: e.target.value})}
                             className="w-full bg-white border border-slate-200 rounded-2xl px-5 py-3 text-sm text-slate-800 font-bold outline-none cursor-pointer shadow-sm"
                          >
                             <option value="road">Đường bộ</option>
                             <option value="bridge">Cầu đường</option>
                          </select>
                       </div>
                       <div className="space-y-2">
                          <label className="text-[10px] font-bold text-slate-400 uppercase tracking-wider pl-1">Mức độ cảnh báo</label>
                          <select 
                             value={editForm.severity} onChange={e => setEditForm({...editForm, severity: e.target.value})}
                             className="w-full bg-white border border-slate-200 rounded-2xl px-5 py-3 text-sm font-bold outline-none cursor-pointer text-amber-600 shadow-sm"
                          >
                             <option value="critical" className="text-red-600">Nguy hiểm</option>
                             <option value="warning" className="text-yellow-600">Cảnh báo</option>
                             <option value="info" className="text-blue-600">Thông tin</option>
                          </select>
                       </div>
                    </div>

                    <div className="space-y-2">
                       <label className="text-[10px] font-bold text-slate-400 uppercase tracking-wider pl-1">Phân loại AI (Classification)</label>
                       <input 
                          value={editForm.classification} onChange={e => setEditForm({...editForm, classification: e.target.value})}
                          placeholder="VD: Nứt dọc mặt đường (Longitudinal Crack)"
                          className="w-full bg-white border border-slate-200 rounded-2xl px-5 py-3 text-sm text-blue-600 font-bold outline-none shadow-sm" 
                       />
                    </div>

                    <div className="space-y-2">
                       <label className="text-[10px] font-bold text-slate-400 uppercase tracking-wider pl-1">Mô tả sự cố thực tế</label>
                       <textarea 
                          value={editForm.description} onChange={e => setEditForm({...editForm, description: e.target.value})}
                          className="w-full bg-white border border-slate-200 rounded-2xl px-5 py-4 text-sm text-slate-600 outline-none h-24 resize-none leading-relaxed shadow-sm"
                          placeholder="Nhập chi tiết về trạng thái vật lý của hư hỏng..."
                       />
                    </div>
                    
                    <div className="space-y-2">
                       <label className="text-[10px] font-bold text-slate-400 uppercase tracking-wider pl-1">Ngày đã sửa chữa (Nếu có)</label>
                       <input 
                          type="date"
                          value={editForm.repaired_at?.split('T')[0] || ''} onChange={e => setEditForm({...editForm, repaired_at: e.target.value})}
                          className="w-full bg-white border border-slate-200 rounded-2xl px-5 py-3 text-sm text-emerald-600 font-bold outline-none shadow-sm" 
                       />
                     </div>

                     <div className="border-t border-slate-100 pt-4 space-y-5">
                        <p className="text-[10px] font-bold text-blue-600 uppercase tracking-wider pl-1">🛣️ Thông tin Nghiệp vụ TCVN</p>

                         <div className="grid grid-cols-2 gap-4">
                            <div className="space-y-1.5">
                               <label className="text-[9px] font-bold text-slate-400 uppercase tracking-wider pl-1">Tuyến đường</label>
                               <input value={editForm.route_name || ''} onChange={e => setEditForm({...editForm, route_name: e.target.value})}
                                 className="w-full bg-white border border-slate-200 rounded-xl px-3 py-2 text-xs text-slate-800 font-bold outline-none shadow-sm" placeholder="QL1A" />
                            </div>
                            <div className="space-y-1.5">
                               <label className="text-[9px] font-bold text-slate-400 uppercase tracking-wider pl-1">Lý trình (Km)</label>
                               <input type="number" step="0.001" value={editForm.route_km ?? ''} onChange={e => setEditForm({...editForm, route_km: e.target.value === '' ? null : Number(e.target.value)})}
                                 className="w-full bg-white border border-slate-200 rounded-xl px-3 py-2 text-xs text-slate-800 font-bold outline-none shadow-sm" placeholder="123.2" />
                            </div>
                         </div>

                         <div className="space-y-1.5">
                            <label className="text-[9px] font-bold text-slate-400 uppercase tracking-wider pl-1">Làn đường (Có thể chọn nhiều)</label>
                            <div className="flex flex-wrap gap-1.5">
                               {([
                                 { value: 'left', label: 'Làn trái (Left)' },
                                 { value: 'center', label: 'Làn giữa (Center)' },
                                 { value: 'right', label: 'Làn phải (Right)' },
                                 { value: 'shoulder', label: 'Lề đường (Shoulder)' }
                               ] as const).map(lane => {
                                  const selectedLanes = editForm.lane_position ? editForm.lane_position.split(',') : [];
                                  const isSelected = selectedLanes.includes(lane.value);
                                  return (
                                     <button
                                        key={lane.value}
                                        type="button"
                                        onClick={() => {
                                           let updated;
                                           if (isSelected) {
                                              updated = selectedLanes.filter((l: string) => l !== lane.value);
                                           } else {
                                              updated = [...selectedLanes, lane.value];
                                           }
                                           setEditForm({
                                              ...editForm,
                                              lane_position: updated.join(',')
                                           });
                                        }}
                                        className={`px-3 py-2 text-[10px] font-bold rounded-xl border transition-all ${
                                           isSelected 
                                             ? 'bg-blue-50 border-blue-300 text-blue-700 ring-2 ring-blue-100' 
                                             : 'bg-white border-slate-200 text-slate-500 hover:bg-slate-50'
                                        }`}
                                     >
                                        {lane.label}
                                     </button>
                                  );
                               })}
                            </div>
                         </div>

                        <div className="grid grid-cols-2 gap-4">
                           <div className="space-y-1.5">
                              <label className="text-[9px] font-bold text-slate-400 uppercase tracking-wider pl-1">Phân hạng TCVN 8866</label>
                              <div className="flex gap-1">
                                 {(['A','B','C','D','E'] as const).map(g => (
                                    <button key={g} type="button" onClick={() => setEditForm({...editForm, tcvn_grade: g})}
                                       className={`flex-1 py-2 rounded-lg text-[10px] font-bold border transition-all ${editForm.tcvn_grade === g ? 'bg-blue-50 border-blue-200 text-blue-600' : 'bg-white border-slate-200 text-slate-400 hover:bg-slate-50'}`}
                                    >{g}</button>
                                 ))}
                              </div>
                           </div>
                           <div className="space-y-1.5">
                              <label className="text-[9px] font-bold text-slate-400 uppercase tracking-wider pl-1">Trạng thái sửa chữa</label>
                              <select value={editForm.repair_status || 'detected'} onChange={e => setEditForm({...editForm, repair_status: e.target.value})}
                                className="w-full bg-white border border-slate-200 rounded-xl px-3 py-2 text-xs text-slate-800 font-bold outline-none cursor-pointer shadow-sm">
                                {REPAIR_STATUSES.map(rs => (
                                   <option key={rs.value} value={rs.value}>{rs.icon} {rs.label}</option>
                                ))}
                              </select>
                           </div>
                        </div>

                        <div className="grid grid-cols-2 gap-4">
                           <div className="space-y-1.5">
                              <label className="text-[9px] font-bold text-slate-400 uppercase tracking-wider pl-1">Diện tích hư hỏng (m²)</label>
                              <input type="number" step="0.01" value={editForm.damage_area_m2 ?? ''} onChange={e => setEditForm({...editForm, damage_area_m2: e.target.value === '' ? null : Number(e.target.value)})}
                                className="w-full bg-white border border-slate-200 rounded-xl px-3 py-2 text-xs text-slate-800 font-bold outline-none shadow-sm" placeholder="0.5" />
                           </div>
                           <div className="space-y-1.5">
                              <label className="text-[9px] font-bold text-slate-400 uppercase tracking-wider pl-1">Bề rộng vết nứt (mm)</label>
                              <input type="number" step="0.1" value={editForm.damage_width_mm ?? ''} onChange={e => setEditForm({...editForm, damage_width_mm: e.target.value === '' ? null : Number(e.target.value)})}
                                className="w-full bg-white border border-slate-200 rounded-xl px-3 py-2 text-xs text-slate-800 font-bold outline-none shadow-sm" placeholder="2.5" />
                           </div>
                        </div>

                        <div className="grid grid-cols-3 gap-4">
                           <div className="space-y-1.5">
                              <label className="text-[9px] font-bold text-slate-400 uppercase tracking-wider pl-1">Phương pháp sửa</label>
                              <input value={editForm.repair_method || ''} onChange={e => setEditForm({...editForm, repair_method: e.target.value})}
                                className="w-full bg-white border border-slate-200 rounded-xl px-3 py-2 text-xs text-slate-800 font-bold outline-none shadow-sm" placeholder="Trám bitum" />
                           </div>
                           <div className="space-y-1.5">
                              <label className="text-[9px] font-bold text-slate-400 uppercase tracking-wider pl-1">Nhà thầu</label>
                              <input value={editForm.contractor || ''} onChange={e => setEditForm({...editForm, contractor: e.target.value})}
                                className="w-full bg-white border border-slate-200 rounded-xl px-3 py-2 text-xs text-slate-800 font-bold outline-none shadow-sm" placeholder="CIENCO4" />
                           </div>
                           <div className="space-y-1.5">
                              <label className="text-[9px] font-bold text-slate-400 uppercase tracking-wider pl-1">Kinh phí (VNĐ)</label>
                              <input type="number" value={editForm.repair_cost_vnd ?? ''} onChange={e => setEditForm({...editForm, repair_cost_vnd: e.target.value === '' ? null : Number(e.target.value)})}
                                className="w-full bg-white border border-slate-200 rounded-xl px-3 py-2 text-xs text-slate-800 font-bold outline-none shadow-sm" placeholder="5000000" />
                           </div>
                        </div>
                     </div>

                    <div className="pb-4">
                       <label className="text-[10px] font-black text-slate-500 uppercase tracking-widest pl-1 block mb-3">Hiệu chỉnh Vị trí (GIS)</label>
                       <div className="h-40 rounded-3xl overflow-hidden border border-slate-200/60 grayscale opacity-80 hover:grayscale-0 hover:opacity-100 transition-all cursor-crosshair">
                           <MiniMapPicker 
                               lat={editForm.lat} lng={editForm.lng}
                               onLocationSelect={(lat, lng) => setEditForm({...editForm, lat, lng})}
                           />
                       </div>
                    </div>
                 </div>
              </div>

              <div className="px-10 py-8 bg-slate-50/50 border-t border-slate-100 flex gap-4">
                 <button type="button" onClick={() => setShowEditModal(false)} className="flex-1 py-4 text-xs font-bold uppercase text-slate-500 hover:text-slate-700 transition-colors">Hủy thao tác</button>
                 <button 
                  type="button" onClick={handleSaveEdit} disabled={isSaving}
                  className="flex-[2] py-4 bg-blue-600 hover:bg-blue-700 rounded-2xl text-xs font-bold uppercase text-white shadow-lg shadow-blue-600/20 active:scale-95 transition-all flex items-center justify-center gap-2"
                 >
                    {isSaving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Settings2 className="w-4 h-4" />}
                    Lưu Thay Đổi Kỹ Thuật
                 </button>
              </div>
           </div>
        </div>
      )}

      {/* ── EVOLUTION COMPARISON PANEL ────────────────────── */}
      {showEvolution && surveyFilter !== 'all' && (
        <EvolutionPanel
          data={evolutionData}
          onClose={() => setShowEvolution(false)}
        />
      )}
    </div>
  );
}
