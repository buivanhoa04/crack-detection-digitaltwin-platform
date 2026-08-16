'use client';

import { useState, useEffect, useCallback, useRef } from 'react';
import {
  AlertTriangle, Shield, ShieldCheck, ShieldAlert, ShieldX,
  Activity, ChevronDown, ChevronUp, ChevronLeft, ChevronRight, Maximize2, X,
  Loader2, Crosshair, Brain, Filter,
  Clock, Hash, Ruler, Square, MapPin, Search
} from 'lucide-react';
import { crackAPI } from '@/lib/api';
import { withAccessToken } from '@/lib/mediaAuth';
import { DefectFrame, DefectDetection, DefectReport, DefectMarkerData } from '../types';

// ── Severity Config ──
const SEVERITY_CONFIG: Record<string, { color: string; bg: string; border: string; label: string; icon: typeof ShieldX }> = {
  critical: { color: 'text-rose-400', bg: 'bg-rose-500/10', border: 'border-rose-500/30', label: 'Nguy hiểm', icon: ShieldX },
  severe:   { color: 'text-amber-400', bg: 'bg-amber-500/10', border: 'border-amber-500/30', label: 'Nghiêm trọng', icon: ShieldAlert },
  moderate: { color: 'text-yellow-400', bg: 'bg-yellow-500/10', border: 'border-yellow-500/30', label: 'Trung bình', icon: Shield },
  minor:    { color: 'text-emerald-400', bg: 'bg-emerald-500/10', border: 'border-emerald-500/30', label: 'Nhẹ', icon: ShieldCheck },
  unknown:  { color: 'text-slate-400', bg: 'bg-slate-500/10', border: 'border-slate-500/30', label: 'Chưa xác định', icon: Shield },
};

function getSeverityFromConfidence(confidence: number): string {
  if (confidence >= 0.85) return 'critical';
  if (confidence >= 0.7) return 'severe';
  if (confidence >= 0.5) return 'moderate';
  if (confidence >= 0.3) return 'minor';
  return 'unknown';
}

function formatClassName(cls: string): string {
  return cls.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
}

interface AIDefectCatalogProps {
  taskId: string | null;
  projectName?: string;
  assetType?: 'road' | 'bridge' | string;
  onFocusDefect?: (trackId: number) => void;
  focusedTrackId?: number | null;
  onFiltersChange?: (filters: { classes: string[]; severities: string[] }) => void;
  onDefectsLoaded?: (defects: DefectMarkerData[]) => void;
  isCollapsed?: boolean;
  onToggleCollapse?: () => void;
}

export function AIDefectCatalog({
  taskId, projectName, assetType = 'road', onFocusDefect, focusedTrackId, onFiltersChange, onDefectsLoaded,
  isCollapsed = false, onToggleCollapse
}: AIDefectCatalogProps) {
  const [frames, setFrames] = useState<DefectFrame[]>([]);
  const [reports, setReports] = useState<Record<number, DefectReport>>({});
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [expandedTrackId, setExpandedTrackId] = useState<number | null>(null);
  const [analyzingTrackId, setAnalyzingTrackId] = useState<number | null>(null);
  const [snapshotModal, setSnapshotModal] = useState<{ url: string; label: string } | null>(null);
  const [taskStatus, setTaskStatus] = useState<string>('');
  const [selectedDefectModalTrackId, setSelectedDefectModalTrackId] = useState<number | null>(null);
  const [sidebarSearch, setSidebarSearch] = useState<string>('');

  // Filter state
  const [filterClasses, setFilterClasses] = useState<string[]>([]);
  const [filterSeverities, setFilterSeverities] = useState<string[]>([]);
  const [showFilters, setShowFilters] = useState(false);

  // Refs for auto-scroll
  const listRef = useRef<HTMLDivElement>(null);
  const itemRefs = useRef<Record<number, HTMLDivElement | null>>({});

  // ── Auto-expand + scroll when focusedTrackId changes from 3D click ──
  useEffect(() => {
    if (focusedTrackId !== null && focusedTrackId !== undefined) {
      setSelectedDefectModalTrackId(focusedTrackId);
      setExpandedTrackId(focusedTrackId);
      // Auto-scroll to the focused item
      setTimeout(() => {
        const el = itemRefs.current[focusedTrackId];
        if (el) {
          el.scrollIntoView({ behavior: 'smooth', block: 'center' });
        }
      }, 100);
    }
  }, [focusedTrackId]);

  // ── Fetch crack detection results ──
  const fetchDefects = useCallback(async () => {
    if (!taskId) { setFrames([]); setReports({}); return; }
    setLoading(true);
    setError(null);
    try {
      const { data } = await crackAPI.getStatus(taskId);
      setTaskStatus(data.status || '');
      const bestFrames: DefectFrame[] = data.best_frames || [];
      setFrames(bestFrames);

      try {
        const reportRes = await crackAPI.getReports(taskId);
        if (reportRes.data?.data && Array.isArray(reportRes.data.data)) {
          const reportsMap: Record<number, DefectReport> = {};
          reportRes.data.data.forEach((r: DefectReport) => { reportsMap[r.track_id] = r; });
          setReports(reportsMap);
        }
      } catch { /* Reports may not exist yet */ }
    } catch (err: any) {
      console.error('[AIDefectCatalog] Fetch error:', err);
      setError('Không thể tải dữ liệu phân tích vết nứt.');
    } finally {
      setLoading(false);
    }
  }, [taskId]);

  useEffect(() => { fetchDefects(); }, [fetchDefects]);

  // ── Analyze track via Vision LLM ──
  const handleAnalyzeTrack = async (trackId: number, force: boolean = false) => {
    if (!taskId) return;
    setAnalyzingTrackId(trackId);
    try {
      const res = await crackAPI.analyzeSnapshot(taskId, trackId, force);
      if (res.data?.data) setReports(prev => ({ ...prev, [trackId]: res.data.data }));
    } catch (err) { console.error('[AIDefectCatalog] Analysis error:', err); }
    finally { setAnalyzingTrackId(null); }
  };

  // ── Build flat detection list ──
  const allDetections: (DefectDetection & { timestamp: string; frameFilePath: string; frame_index: number; severity: string })[] = [];
  const seenTrackIds = new Set<number>();

  frames.forEach(frame => {
    (frame.detections || []).forEach(det => {
      if (!seenTrackIds.has(det.track_id)) {
        seenTrackIds.add(det.track_id);
        allDetections.push({
          ...det,
          timestamp: frame.timestamp,
          frameFilePath: frame.frameFilePath,
          frame_index: frame.frame_index,
          severity: getSeverityFromConfidence(det.confidence),
        });
      }
    });
  });
  allDetections.sort((a, b) => b.confidence - a.confidence);

  // ── Notify parent with DefectMarkerData when data changes ──
  useEffect(() => {
    if (onDefectsLoaded && allDetections.length > 0) {
      const markers: DefectMarkerData[] = allDetections.map(d => ({
        track_id: d.track_id,
        class: d.class,
        confidence: d.confidence,
        severity: d.severity,
        frame_index: d.frame_index,
        timestamp: d.timestamp,
        frameFilePath: d.frameFilePath,
        real_width_mm: d.real_width_mm,
          real_area_m2: d.real_area_m2,
          bbox: d.bbox,
          polygon: d.polygon,
      }));
      onDefectsLoaded(markers);
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [frames]);

  // ── Notify parent when filters change ──
  useEffect(() => {
    onFiltersChange?.({ classes: filterClasses, severities: filterSeverities });
  }, [filterClasses, filterSeverities, onFiltersChange]);

  // ── Available filter options (from actual data) ──
  const availableClasses = Array.from(new Set(allDetections.map(d => d.class)));
  const availableSeverities = Array.from(new Set(allDetections.map(d => d.severity)));

  // ── Apply filters to displayed list ──
  const filteredDetections = allDetections.filter(d => {
    if (filterClasses.length > 0 && !filterClasses.includes(d.class)) return false;
    if (filterSeverities.length > 0 && !filterSeverities.includes(d.severity)) return false;
    return true;
  });

  // ── Statistics ──
  const totalCracks = allDetections.length;
  const filteredCount = filteredDetections.length;
  const avgConfidence = totalCracks > 0 ? allDetections.reduce((s, d) => s + d.confidence, 0) / totalCracks : 0;
  const highestSeverity = totalCracks > 0 ? getSeverityFromConfidence(Math.max(...allDetections.map(d => d.confidence))) : 'unknown';
  const sevConfig = SEVERITY_CONFIG[highestSeverity];

  const getSnapshotUrl = (fp: string): string => {
    if (!fp) return '';
    if (fp.startsWith('http')) return fp;
    return withAccessToken(`/api/crack/proxy-file?path=${encodeURIComponent(fp)}`);
  };

  const toggleFilter = (arr: string[], val: string, setter: (v: string[]) => void) => {
    setter(arr.includes(val) ? arr.filter(v => v !== val) : [...arr, val]);
  };

  if (isCollapsed) {
    return (
      <div className="glass-card flex flex-col items-center h-full py-4 w-full border border-slate-200 shadow-sm text-slate-800">
        {onToggleCollapse && (
          <button
            onClick={onToggleCollapse}
            className="p-1.5 rounded-lg bg-slate-50 text-slate-600 hover:bg-slate-100 transition-colors mb-4"
            title="Mở rộng danh mục"
          >
            <ChevronLeft className="w-4 h-4" />
          </button>
        )}
        <div className="flex-1 flex items-center justify-center select-none pointer-events-none">
          <span 
            className="text-[10px] font-bold text-slate-400 uppercase tracking-widest whitespace-nowrap"
            style={{ writingMode: 'vertical-rl', transform: 'rotate(180deg)' }}
          >
            Danh mục vết nứt AI
          </span>
        </div>
      </div>
    );
  }

  if (!taskId) {
    return (
      <div className="flex flex-col items-center justify-center h-full text-slate-400 py-12 px-4">
        <Activity className="w-10 h-10 mb-3 opacity-40" />
        <p className="text-xs text-center">Chọn một công trình để xem<br/>danh mục vết nứt AI</p>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full gap-2">
      {/* Header */}
      <div className="glass-card p-3 border border-slate-200 shadow-sm shrink-0">
        <div className="flex items-center justify-between mb-1">
          <h3 className="text-xs font-semibold text-slate-800 flex items-center gap-2">
            <Brain className="w-4 h-4 text-violet-500" />
            Danh mục vết nứt AI
          </h3>
          <div className="flex items-center gap-1">
            <button
              onClick={() => setShowFilters(!showFilters)}
              className={`p-1 rounded-md transition-colors ${showFilters ? 'text-violet-500 bg-violet-50' : 'text-slate-400 hover:text-violet-500 hover:bg-violet-50'}`}
              title="Bộ lọc"
            >
              <Filter className="w-3.5 h-3.5" />
            </button>
            <button onClick={fetchDefects} disabled={loading} className="p-1 rounded-md text-slate-400 hover:text-violet-500 hover:bg-violet-50 transition-colors" title="Tải lại">
              <Loader2 className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
            </button>
            {onToggleCollapse && (
              <button
                onClick={onToggleCollapse}
                className="p-1 rounded-md text-slate-400 hover:text-violet-500 hover:bg-violet-50 transition-colors"
                title="Thu nhỏ danh mục"
              >
                <ChevronRight className="w-3.5 h-3.5" />
              </button>
            )}
          </div>
        </div>
        {projectName && <p className="text-[10px] text-slate-500 truncate">{projectName}</p>}
        {taskStatus && (
          <div className={`mt-1.5 inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-medium ${
            taskStatus === 'done' ? 'bg-emerald-50 text-emerald-600 border border-emerald-200' :
            taskStatus === 'processing' ? 'bg-blue-50 text-blue-600 border border-blue-200' :
            'bg-slate-50 text-slate-500 border border-slate-200'
          }`}>
            {taskStatus === 'done' && <ShieldCheck className="w-3 h-3" />}
            {taskStatus === 'processing' && <Loader2 className="w-3 h-3 animate-spin" />}
            {taskStatus === 'done' ? 'Phân tích hoàn tất' : taskStatus === 'processing' ? 'Đang phân tích...' : taskStatus}
          </div>
        )}
      </div>

      {/* Filters Panel */}
      {showFilters && totalCracks > 0 && (
        <div className="glass-card p-3 border border-violet-200 shadow-sm shrink-0 space-y-2 animate-fade-in">
          <p className="text-[10px] font-semibold text-slate-500 uppercase tracking-wider">Loại lỗi</p>
          <div className="flex flex-wrap gap-1">
            {availableClasses.map(cls => (
              <button
                key={cls}
                onClick={() => toggleFilter(filterClasses, cls, setFilterClasses)}
                className={`px-2 py-0.5 rounded-full text-[10px] font-medium border transition-colors ${
                  filterClasses.includes(cls) ? 'bg-violet-100 text-violet-700 border-violet-300' : 'bg-white text-slate-500 border-slate-200 hover:border-violet-200'
                }`}
              >
                {formatClassName(cls)}
              </button>
            ))}
          </div>
          <p className="text-[10px] font-semibold text-slate-500 uppercase tracking-wider pt-1">Mức độ</p>
          <div className="flex flex-wrap gap-1">
            {availableSeverities.map(sev => {
              const sc = SEVERITY_CONFIG[sev];
              return (
                <button
                  key={sev}
                  onClick={() => toggleFilter(filterSeverities, sev, setFilterSeverities)}
                  className={`px-2 py-0.5 rounded-full text-[10px] font-medium border transition-colors ${
                    filterSeverities.includes(sev) ? `${sc.bg} ${sc.color} ${sc.border}` : 'bg-white text-slate-500 border-slate-200 hover:border-slate-300'
                  }`}
                >
                  {sc.label}
                </button>
              );
            })}
          </div>
          {(filterClasses.length > 0 || filterSeverities.length > 0) && (
            <button
              onClick={() => { setFilterClasses([]); setFilterSeverities([]); }}
              className="text-[10px] text-rose-500 hover:text-rose-600 font-medium"
            >
              ✕ Xóa bộ lọc
            </button>
          )}
        </div>
      )}

      {/* Summary Stats */}
      {totalCracks > 0 && (
        <div className="grid grid-cols-3 gap-2 shrink-0">
          <div className="glass-card p-2 border border-slate-200 shadow-sm text-center">
            <div className="text-lg font-bold text-slate-800">{filteredCount}<span className="text-[10px] text-slate-400 font-normal">/{totalCracks}</span></div>
            <div className="text-[9px] text-slate-500 uppercase tracking-wider font-medium">Vết nứt</div>
          </div>
          <div className="glass-card p-2 border border-slate-200 shadow-sm text-center">
            <div className="text-lg font-bold text-blue-600">{(avgConfidence * 100).toFixed(0)}%</div>
            <div className="text-[9px] text-slate-500 uppercase tracking-wider font-medium">Tin cậy TB</div>
          </div>
          <div className={`glass-card p-2 border shadow-sm text-center ${sevConfig.border}`}>
            <div className="flex justify-center mb-0.5"><sevConfig.icon className={`w-5 h-5 ${sevConfig.color}`} /></div>
            <div className={`text-[9px] uppercase tracking-wider font-bold ${sevConfig.color}`}>{sevConfig.label}</div>
          </div>
        </div>
      )}

      {/* States */}
      {error && (
        <div className="glass-card p-3 border border-rose-200 bg-rose-50/50 shrink-0">
          <p className="text-xs text-rose-600 flex items-center gap-1.5"><AlertTriangle className="w-3.5 h-3.5" />{error}</p>
        </div>
      )}
      {loading && totalCracks === 0 && (
        <div className="flex flex-col items-center justify-center py-8">
          <Loader2 className="w-6 h-6 text-violet-500 animate-spin mb-2" />
          <p className="text-xs text-slate-500">Đang tải dữ liệu...</p>
        </div>
      )}
      {!loading && totalCracks === 0 && !error && (
        <div className="glass-card p-6 border border-slate-200 flex flex-col items-center justify-center">
          <ShieldCheck className="w-8 h-8 text-emerald-400 mb-2 opacity-60" />
          <p className="text-xs text-slate-500 text-center">
            {taskStatus === 'done' ? 'Không phát hiện vết nứt nào!' : 'Chưa có kết quả phân tích.'}
          </p>
        </div>
      )}

      {/* Defect List */}
      {filteredDetections.length > 0 && (
        <div ref={listRef} className="flex-1 overflow-y-auto space-y-2 custom-scrollbar pr-0.5">
          {filteredDetections.map((det, idx) => {
            const sev = SEVERITY_CONFIG[det.severity];
            const isExpanded = expandedTrackId === det.track_id;
            const isFocused = focusedTrackId === det.track_id;
            const report = reports[det.track_id];
            const isAnalyzing = analyzingTrackId === det.track_id;
            const snapshotUrl = getSnapshotUrl(det.frameFilePath);

            return (
              <div
                key={det.track_id}
                ref={el => { itemRefs.current[det.track_id] = el; }}
                className={`glass-card border shadow-sm transition-all duration-300 ${
                  isFocused ? `${sev.border} ring-2 ring-violet-400/30` :
                  isExpanded ? `${sev.border} ring-1 ring-opacity-20` : 'border-slate-200 hover:border-slate-300'
                }`}
              >
                {/* Card Header */}
                <div className="flex items-start">
                  <button
                    onClick={() => {
                      setSelectedDefectModalTrackId(det.track_id);
                      setExpandedTrackId(det.track_id);
                    }}
                    className="flex-1 p-3 text-left flex items-start gap-3 group"
                  >
                    <div className={`p-1.5 rounded-lg ${sev.bg} shrink-0 mt-0.5`}>
                      <sev.icon className={`w-3.5 h-3.5 ${sev.color}`} />
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 mb-1">
                        <span className="text-[10px] font-bold text-slate-400">{idx + 1}</span>
                        <span className="text-xs font-semibold text-slate-800 truncate">{formatClassName(det.class)}</span>
                      </div>
                      <div className="flex items-center gap-3 text-[10px] text-slate-500">
                        <span className="flex items-center gap-0.5"><Crosshair className="w-3 h-3" />{(det.confidence * 100).toFixed(1)}%</span>
                        <span className="flex items-center gap-0.5"><Clock className="w-3 h-3" />{det.timestamp || '—'}</span>
                        <span className="flex items-center gap-0.5"><Hash className="w-3 h-3" />T{det.track_id}</span>
                      </div>
                      {(det.real_width_mm || det.real_area_m2) && (
                        <div className="flex items-center gap-3 mt-1 text-[10px]">
                          {det.real_width_mm && det.real_width_mm > 0 && (
                            <span className="flex items-center gap-0.5 text-orange-600 font-medium"><Ruler className="w-3 h-3" />{det.real_width_mm.toFixed(1)}mm</span>
                          )}
                          {det.real_area_m2 && det.real_area_m2 > 0 && (
                            <span className="flex items-center gap-0.5 text-purple-600 font-medium"><Square className="w-3 h-3" />{det.real_area_m2.toFixed(4)}m²</span>
                          )}
                        </div>
                      )}
                    </div>
                    <div className="shrink-0 text-slate-400 group-hover:text-slate-600 transition-colors">
                      {isExpanded ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
                    </div>
                  </button>
                  {/* Locate on 3D button */}
                  {onFocusDefect && (
                    <button
                      onClick={() => onFocusDefect(det.track_id)}
                      className="p-2 mr-1 mt-2 rounded-lg text-slate-400 hover:text-violet-500 hover:bg-violet-50 transition-colors shrink-0"
                      title="Tìm trên 3D"
                    >
                      <MapPin className="w-4 h-4" />
                    </button>
                  )}
                </div>

                {/* Expanded Detail */}
                {isExpanded && (
                  <div className="px-3 pb-3 space-y-3 animate-fade-in border-t border-slate-100">
                    {snapshotUrl && (
                      <div className="mt-3 relative group/img w-full overflow-hidden rounded-lg border border-slate-200 bg-slate-100">
                        <img
                          src={snapshotUrl}
                          alt={`Snapshot Track ${det.track_id}`}
                          className="w-full h-auto block cursor-pointer hover:opacity-90 transition-opacity"
                          onClick={() => setSelectedDefectModalTrackId(det.track_id)}
                          onError={(e) => { (e.target as HTMLImageElement).style.display = 'none'; }}
                        />
                        <svg viewBox="0 0 1 1" preserveAspectRatio="none" className="absolute inset-0 w-full h-full pointer-events-none">
                          {det.polygon && Array.isArray(det.polygon) && det.polygon.length > 0 ? (
                            <polygon
                              points={det.polygon.map((p: number[]) => `${p[0]},${p[1]}`).join(' ')}
                              className="stroke-red-500 fill-red-500/25"
                              style={{ strokeWidth: 0.005 }}
                            />
                          ) : (
                            det.bbox && det.bbox.length === 4 && (
                              <rect
                                x={det.bbox[0]}
                                y={det.bbox[1]}
                                width={det.bbox[2] - det.bbox[0]}
                                height={det.bbox[3] - det.bbox[1]}
                                className="stroke-red-500 fill-red-500/25"
                                style={{ strokeWidth: 0.005 }}
                              />
                            )
                          )}
                        </svg>
                        <button
                          onClick={() => setSelectedDefectModalTrackId(det.track_id)}
                          className="absolute top-2 right-2 p-1 rounded bg-black/50 text-white opacity-0 group-hover/img:opacity-100 transition-opacity"
                        >
                          <Maximize2 className="w-3 h-3" />
                        </button>
                      </div>
                    )}

                    <div className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[10px] font-bold ${sev.bg} ${sev.color} ${sev.border} border`}>
                      <sev.icon className="w-3 h-3" />{sev.label}
                    </div>

                    {report ? (
                      <div className="space-y-2 text-[11px]">
                        <div className="flex items-center gap-1.5 mb-1">
                          <Brain className={`w-3.5 h-3.5 ${report.analysis_source === 'vision_llm' ? 'text-violet-500' : 'text-slate-400'}`} />
                          <span className={`font-semibold ${report.analysis_source === 'vision_llm' ? 'text-violet-600' : 'text-slate-600'}`}>
                            {report.analysis_source === 'vision_llm' ? 'Phân tích Vision AI' : 'Phân Tích Hư Hại'}
                          </span>
                        </div>
                        {report.defect_name && <p className="text-slate-700 font-medium">{report.defect_name} ({report.defect_code})</p>}
                        {report.defect_code_mapping && !report.defect_name && <p className="text-slate-700 font-medium">{report.defect_code_mapping}</p>}
                        
                        {(report.analysis?.description || report.current_status_details) && (
                          <p className="text-slate-600 leading-relaxed line-clamp-3">{report.analysis?.description || report.current_status_details}</p>
                        )}
                        
                        <button
                          onClick={() => setSelectedDefectModalTrackId(det.track_id)}
                          className="w-full mt-2 py-1.5 border border-violet-200 text-violet-600 hover:text-white hover:bg-violet-600 rounded-lg text-[10px] font-semibold transition-all flex items-center justify-center gap-1"
                        >
                          <Maximize2 className="w-3 h-3" />
                          Xem chi tiết phân tích & Báo cáo
                        </button>
                      </div>
                    ) : (
                      <button
                        onClick={() => {
                          setSelectedDefectModalTrackId(det.track_id);
                          if (!reports[det.track_id]) {
                            handleAnalyzeTrack(det.track_id);
                          }
                        }}
                        disabled={isAnalyzing}
                        className="w-full flex items-center justify-center gap-2 py-2 rounded-lg text-xs font-medium transition-all bg-gradient-to-r from-violet-500 to-purple-600 text-white hover:from-violet-600 hover:to-purple-700 disabled:opacity-50 disabled:cursor-not-allowed shadow-md shadow-violet-500/20"
                      >
                        {isAnalyzing ? (<><Loader2 className="w-3.5 h-3.5 animate-spin" />Đang phân tích AI...</>) : (<><Brain className="w-3.5 h-3.5" />Phân tích chi tiết (Vision AI)</>)}
                      </button>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {/* Snapshot Modal */}
      {snapshotModal && (
        <div className="fixed inset-0 bg-black/80 backdrop-blur-sm flex items-center justify-center z-50 animate-fade-in" onClick={() => setSnapshotModal(null)}>
          <div className="relative max-w-4xl max-h-[90vh] m-4" onClick={e => e.stopPropagation()}>
            <button onClick={() => setSnapshotModal(null)} className="absolute -top-3 -right-3 p-1.5 rounded-full bg-white/90 text-slate-800 shadow-lg hover:bg-white transition-colors z-10">
              <X className="w-4 h-4" />
            </button>
            <img src={snapshotModal.url} alt={snapshotModal.label} className="max-w-full max-h-[85vh] rounded-xl shadow-2xl border border-white/10" />
            <div className="absolute bottom-0 left-0 right-0 bg-gradient-to-t from-black/70 to-transparent p-4 rounded-b-xl">
              <p className="text-white text-sm font-medium">{snapshotModal.label}</p>
            </div>
          </div>
        </div>
      )}

      {/* Large Technical Analysis & Navigation Modal */}
      {selectedDefectModalTrackId !== null && (() => {
        const activeDet = allDetections.find(d => d.track_id === selectedDefectModalTrackId);
        if (!activeDet) return null;
        
        const activeReport = reports[selectedDefectModalTrackId];
        const isActiveAnalyzing = analyzingTrackId === selectedDefectModalTrackId;
        const activeSev = SEVERITY_CONFIG[activeDet.severity] || SEVERITY_CONFIG.unknown;
        const activeSnapshotUrl = getSnapshotUrl(activeDet.frameFilePath);
        
        // Filter sidebar detections based on search
        const sidebarDetections = allDetections.filter(d => 
          formatClassName(d.class).toLowerCase().includes(sidebarSearch.toLowerCase()) ||
          d.track_id.toString().includes(sidebarSearch)
        );
        
        return (
          <div className="fixed inset-0 bg-slate-900/80 backdrop-blur-sm flex items-center justify-center z-50 animate-fade-in p-4 text-slate-800" onClick={() => setSelectedDefectModalTrackId(null)}>
            <div className="relative w-full max-w-6xl h-[85vh] bg-white rounded-2xl shadow-2xl flex overflow-hidden border border-slate-200 animate-scale-up" onClick={e => e.stopPropagation()}>
              
              {/* Close Button */}
              <button 
                onClick={() => setSelectedDefectModalTrackId(null)}
                className="absolute top-4 right-4 p-2 rounded-full bg-slate-100 hover:bg-slate-200 text-slate-500 hover:text-slate-700 transition-colors z-20"
                title="Đóng"
              >
                <X className="w-5 h-5" />
              </button>

              {/* ── SIDEBAR: List of all defects (Width 300px) ── */}
              <div className="w-[18.75rem] border-r border-slate-200 bg-slate-50/50 flex flex-col h-full shrink-0">
                <div className="p-4 border-b border-slate-200">
                  <h4 className="text-xs font-bold text-slate-700 flex items-center gap-1.5 mb-2">
                    <Hash className="w-3.5 h-3.5 text-violet-500" />
                    Danh sách khuyết tật ({allDetections.length})
                  </h4>
                  <div className="relative">
                    <input
                      type="text"
                      placeholder="Tìm kiếm vết nứt..."
                      value={sidebarSearch}
                      onChange={e => setSidebarSearch(e.target.value)}
                      className="w-full pl-8 pr-3 py-1.5 bg-white border border-slate-200 rounded-lg text-xs focus:outline-none focus:border-violet-500 transition-colors"
                    />
                    <Search className="w-3.5 h-3.5 text-slate-400 absolute left-2.5 top-2.5" />
                    {sidebarSearch && (
                      <button onClick={() => setSidebarSearch('')} className="absolute right-2.5 top-2 text-[10px] text-slate-400 hover:text-slate-600">✕</button>
                    )}
                  </div>
                </div>
                
                {/* Scrollable list */}
                <div className="flex-1 overflow-y-auto p-2 space-y-1.5 custom-scrollbar">
                  {sidebarDetections.map((d, index) => {
                    const ds = SEVERITY_CONFIG[d.severity] || SEVERITY_CONFIG.unknown;
                    const isSelected = d.track_id === selectedDefectModalTrackId;
                    const hasRep = !!reports[d.track_id];
                    
                    return (
                      <button
                        key={d.track_id}
                        onClick={() => setSelectedDefectModalTrackId(d.track_id)}
                        className={`w-full text-left p-2.5 rounded-xl border transition-all flex items-start gap-2.5 ${
                          isSelected 
                            ? 'bg-violet-600 border-violet-600 text-white shadow-md shadow-violet-600/10' 
                            : 'bg-white border-slate-200/80 hover:border-slate-300 text-slate-700'
                        }`}
                      >
                        <div className={`p-1.5 rounded-lg shrink-0 mt-0.5 ${isSelected ? 'bg-white/20' : ds.bg}`}>
                          <ds.icon className={`w-3.5 h-3.5 ${isSelected ? 'text-white' : ds.color}`} />
                        </div>
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center justify-between mb-0.5 gap-1">
                            <span className={`text-[10px] font-bold ${isSelected ? 'text-violet-200' : 'text-slate-400'}`}>{index + 1}</span>
                            <span className={`text-[9px] px-1.5 py-0.5 rounded-full ${
                              isSelected ? 'bg-white/25 text-white' : 'bg-slate-100 text-slate-600'
                            }`}>
                              Track {d.track_id}
                            </span>
                          </div>
                          <p className={`text-xs font-semibold truncate ${isSelected ? 'text-white' : 'text-slate-800'}`}>
                            {formatClassName(d.class)}
                          </p>
                          <div className="flex items-center gap-2 mt-1 text-[9px]">
                            <span className={isSelected ? 'text-violet-200' : 'text-slate-500'}>
                              {(d.confidence * 100).toFixed(0)}% tin cậy
                            </span>
                            {hasRep && (
                              <span className={`flex items-center gap-0.5 font-medium ${isSelected ? 'text-emerald-300' : 'text-emerald-600'}`}>
                                <Brain className="w-2.5 h-2.5" /> Đã phân tích
                              </span>
                            )}
                          </div>
                        </div>
                      </button>
                    );
                  })}
                  {sidebarDetections.length === 0 && (
                    <p className="text-[11px] text-slate-400 text-center py-8">Không tìm thấy khuyết tật</p>
                  )}
                </div>
              </div>

              {/* ── MAIN CONTENT AREA (Scrollable, Width 3/4) ── */}
              <div className="flex-1 flex flex-col h-full min-w-0 bg-white">
                {/* Header */}
                <div className="p-4 border-b border-slate-100 flex items-center justify-between pr-14 shrink-0 bg-slate-50/50">
                  <div>
                    <h3 className="text-sm font-bold text-slate-800 flex items-center gap-2">
                      <Brain className="w-4 h-4 text-violet-600" />
                      Chi tiết Phân tích Kỹ thuật & Báo cáo Giám định
                    </h3>
                    <p className="text-[10px] text-slate-500 mt-0.5">
                      Khuyết tật: <span className="font-semibold text-slate-700">{formatClassName(activeDet.class)}</span> (Mã: {activeReport?.defect_code || 'Chưa mã hóa'} | Track ID: {activeDet.track_id})
                    </p>
                  </div>
                </div>

                {/* Main Scrollable Grid */}
                <div className="flex-1 overflow-y-auto p-6 custom-scrollbar">
                  <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                    
                    {/* LEFT COLUMN: Large Image & Meta Info */}
                    <div className="space-y-4">
                      <div className="relative group rounded-xl overflow-hidden border border-slate-200 shadow-md bg-slate-900/5 w-full flex items-center justify-center">
                        {activeSnapshotUrl ? (
                          <div className="relative w-full">
                            <img
                              src={activeSnapshotUrl}
                              alt={`Large Snapshot Track ${activeDet.track_id}`}
                              className="w-full h-auto block"
                            />
                            <svg viewBox="0 0 1 1" preserveAspectRatio="none" className="absolute inset-0 w-full h-full pointer-events-none">
                              {activeDet.polygon && Array.isArray(activeDet.polygon) && activeDet.polygon.length > 0 ? (
                                <polygon
                                  points={activeDet.polygon.map((p: number[]) => `${p[0]},${p[1]}`).join(' ')}
                                  className="stroke-red-500 fill-red-500/25"
                                  style={{ strokeWidth: 0.005 }}
                                />
                              ) : (
                                activeDet.bbox && activeDet.bbox.length === 4 && (
                                  <rect
                                    x={activeDet.bbox[0]}
                                    y={activeDet.bbox[1]}
                                    width={activeDet.bbox[2] - activeDet.bbox[0]}
                                    height={activeDet.bbox[3] - activeDet.bbox[1]}
                                    className="stroke-red-500 fill-red-500/25"
                                    style={{ strokeWidth: 0.005 }}
                                  />
                                )
                              )}
                            </svg>
                          </div>
                        ) : (
                          <p className="text-xs text-slate-400 py-12">Không có ảnh chụp</p>
                        )}
                        <span className={`absolute top-3 left-3 inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-bold ${activeSev.bg} ${activeSev.color} ${activeSev.border} border backdrop-blur-md`}>
                          <activeSev.icon className="w-3.5 h-3.5" />
                          {activeSev.label}
                        </span>
                      </div>

                      {/* Technical Info Card */}
                      <div className="bg-slate-50 border border-slate-200/60 rounded-xl p-4 space-y-3">
                        <h4 className="text-xs font-bold text-slate-800 uppercase tracking-wider">Thông số kỹ thuật</h4>
                        <div className="grid grid-cols-2 gap-3 text-xs text-slate-600">
                          <div className="flex flex-col p-2 bg-white rounded-lg border border-slate-100">
                            <span className="text-[10px] text-slate-400 font-medium">Độ tin cậy AI</span>
                            <span className="font-bold text-slate-800 mt-0.5">{(activeDet.confidence * 100).toFixed(1)}%</span>
                          </div>
                          <div className="flex flex-col p-2 bg-white rounded-lg border border-slate-100">
                            <span className="text-[10px] text-slate-400 font-medium">Thời điểm phát hiện</span>
                            <span className="font-bold text-slate-800 mt-0.5">{activeDet.timestamp || '—'}</span>
                          </div>
                          {activeDet.real_width_mm && activeDet.real_width_mm > 0 && (
                            <div className="flex flex-col p-2 bg-white rounded-lg border border-slate-100">
                              <span className="text-[10px] text-slate-400 font-medium">Bề rộng đo đạc</span>
                              <span className="font-bold text-orange-600 mt-0.5">{activeDet.real_width_mm.toFixed(1)} mm</span>
                            </div>
                          )}
                          {activeDet.real_area_m2 && activeDet.real_area_m2 > 0 && (
                            <div className="flex flex-col p-2 bg-white rounded-lg border border-slate-100">
                              <span className="text-[10px] text-slate-400 font-medium">Diện tích ước lượng</span>
                              <span className="font-bold text-purple-600 mt-0.5">{activeDet.real_area_m2.toFixed(4)} m²</span>
                            </div>
                          )}
                          <div className="flex flex-col p-2 bg-white rounded-lg border border-slate-100">
                            <span className="text-[10px] text-slate-400 font-medium">Mã chỉ mục</span>
                            <span className="font-bold text-slate-800 mt-0.5">Frame {activeDet.frame_index}</span>
                          </div>
                          <div className="flex flex-col p-2 bg-white rounded-lg border border-slate-100">
                            <span className="text-[10px] text-slate-400 font-medium">Loại đối tượng</span>
                            <span className="font-bold text-slate-800 mt-0.5">{assetType === 'bridge' ? 'Kết cấu cầu' : 'Mặt đường'}</span>
                          </div>
                        </div>
                      </div>
                    </div>

                    {/* RIGHT COLUMN: AI Analysis Details */}
                    <div className="space-y-4">
                      {activeReport ? (
                        <div className="space-y-4 text-xs text-slate-600">
                          
                          {/* Heading */}
                          <div className="flex items-center justify-between pb-2 border-b border-slate-100 gap-2 flex-wrap">
                            <div className="flex items-center gap-1.5">
                              <Brain className="w-5 h-5 text-violet-600" />
                              <span className="text-xs font-bold text-violet-600 uppercase tracking-wider">
                                Phân Tích Hư Hại
                              </span>
                            </div>
                            <button
                              onClick={() => handleAnalyzeTrack(selectedDefectModalTrackId, true)}
                              disabled={isActiveAnalyzing}
                              className="px-2.5 py-1 text-[10px] font-semibold bg-violet-50 hover:bg-violet-100 text-violet-600 hover:text-violet-700 border border-violet-200 rounded-lg flex items-center gap-1 transition-all disabled:opacity-50"
                            >
                              {isActiveAnalyzing ? (
                                <><Loader2 className="w-3 h-3 animate-spin" /> Đang phân tích...</>
                              ) : (
                                <><Brain className="w-3 h-3" /> Phân Tích Hư Hại</>
                              )}
                            </button>
                          </div>

                          {/* Fallback Warning Banner if analysis is only catalog static */}
                          {activeReport.analysis_source === 'catalog' && (
                            <div className="p-2.5 bg-amber-50 border border-amber-200 rounded-xl text-amber-800 text-[11px] leading-relaxed flex items-start gap-1.5 animate-pulse">
                              <AlertTriangle className="w-4 h-4 text-amber-600 shrink-0 mt-0.5" />
                              <div>
                                <span className="font-semibold">Lưu ý:</span> Báo cáo chi tiết chưa được cập nhật từ Vision AI. Bạn hãy bấm nút <b>Phân Tích Hư Hại</b> ở trên để cập nhật báo cáo chuẩn xác nhất.
                              </div>
                            </div>
                          )}

                          {/* 1. Current Status */}
                          <div className="space-y-1">
                            <h5 className="text-xs font-bold text-slate-700">1. Hiện trạng trực quan:</h5>
                            <p className="leading-relaxed bg-slate-50 p-3 rounded-xl border border-slate-200/50 text-slate-600">
                              {activeReport.analysis?.description || activeReport.current_status_details || 'Chưa cập nhật mô tả hiện trạng.'}
                            </p>
                          </div>

                          {/* 2. Technical Analysis & Causes */}
                          <div className="space-y-1.5">
                            <h5 className="text-xs font-bold text-slate-700">2. Phân tích nguyên nhân & Luận điểm kỹ thuật:</h5>
                            <div className="bg-slate-50 p-3 rounded-xl border border-slate-200/50 space-y-2 text-slate-600">
                              {(((activeReport.analysis?.causes?.length ?? 0) > 0) || ((activeReport.technical_analysis?.causes?.length ?? 0) > 0)) ? (
                                <ul className="list-decimal list-inside space-y-1.5">
                                  {(activeReport.analysis?.causes || activeReport.technical_analysis?.causes || []).map((c: string, i: number) => (
                                    <li key={i} className="leading-relaxed pl-1">{c}</li>
                                  ))}
                                </ul>
                              ) : (
                                <p className="text-xs text-slate-500 italic">Chưa có phân tích nguyên nhân chi tiết.</p>
                              )}
                            </div>
                          </div>

                          {/* 3. Recommendations to Contractor */}
                          <div className="space-y-1.5">
                            <h5 className="text-xs font-bold text-slate-700">3. Khuyến nghị khắc phục & Biện pháp kỹ thuật:</h5>
                            <div className="bg-slate-50 p-3 rounded-xl border border-slate-200/50 space-y-2 text-slate-600">
                              {(((activeReport.recommendations?.length ?? 0) > 0) || ((activeReport.recommendations_to_contractor?.length ?? 0) > 0)) ? (
                                <ul className="list-disc list-inside space-y-1.5">
                                  {(activeReport.recommendations || activeReport.recommendations_to_contractor || []).map((r: string, i: number) => (
                                    <li key={i} className="leading-relaxed pl-1">{r}</li>
                                  ))}
                                </ul>
                              ) : (
                                <p className="text-xs text-slate-500 italic">Chưa có khuyến nghị khắc phục.</p>
                              )}
                              
                              {activeReport.analysis?.conclusion_and_repair_plan && (
                                <p className="text-xs text-slate-700 mt-2 font-medium border-t border-slate-200/60 pt-2 italic">
                                  👉 Đề xuất bảo trì: {activeReport.analysis?.conclusion_and_repair_plan}
                                </p>
                              )}
                              {activeReport.conclusion_and_repair_plan && !activeReport.analysis?.conclusion_and_repair_plan && (
                                <p className="text-xs text-slate-700 mt-2 font-medium border-t border-slate-200/60 pt-2 italic">
                                  👉 Đề xuất bảo trì: {activeReport.conclusion_and_repair_plan}
                                </p>
                              )}
                            </div>
                          </div>

                          {/* 4. Standards (TCVN) */}
                          {(((activeReport.tcvn_references?.length ?? 0) > 0) || ((activeReport.technical_analysis?.tcvn_references?.length ?? 0) > 0)) && (
                            <div className="space-y-1.5">
                              <h5 className="text-xs font-bold text-slate-700">4. Tiêu chuẩn quốc gia áp dụng (TCVN):</h5>
                              <div className="flex flex-wrap gap-1.5">
                                {(activeReport.tcvn_references || activeReport.technical_analysis?.tcvn_references || []).map((ref: string, i: number) => (
                                  <span key={i} className="px-2.5 py-1 rounded bg-blue-50 text-blue-700 text-[10px] font-mono border border-blue-200 font-medium">
                                    {ref}
                                  </span>
                                ))}
                              </div>
                            </div>
                          )}

                        </div>
                      ) : (
                        <div className="h-full flex flex-col items-center justify-center py-12 px-4 border border-dashed border-slate-200 rounded-xl bg-slate-50/50 space-y-4">
                          <Brain className="w-12 h-12 text-slate-300 animate-pulse" />
                          <div className="text-center space-y-1">
                            <h5 className="text-xs font-bold text-slate-700">Chưa có dữ liệu phân tích chi tiết</h5>
                            <p className="text-[11px] text-slate-500 max-w-xs leading-relaxed">
                              Bạn cần kích hoạt Vision AI để mô hình trực quan hóa ảnh snapshot này và đối chiếu tiêu chuẩn TCVN đưa ra chẩn đoán chính xác.
                            </p>
                          </div>
                          <button
                            onClick={() => handleAnalyzeTrack(selectedDefectModalTrackId)}
                            disabled={isActiveAnalyzing}
                            className="flex items-center gap-2 px-6 py-2.5 rounded-xl text-xs font-semibold transition-all bg-gradient-to-r from-violet-600 to-purple-600 text-white hover:from-violet-700 hover:to-purple-700 disabled:opacity-50 disabled:cursor-not-allowed shadow-lg shadow-violet-500/20"
                          >
                            {isActiveAnalyzing ? (
                              <><Loader2 className="w-4 h-4 animate-spin" /> Đang chạy phân tích AI...</>
                            ) : (
                              <><Brain className="w-4 h-4" /> Bắt đầu Phân tích Vision AI</>
                            )}
                          </button>
                        </div>
                      )}
                    </div>
                  </div>
                </div>

                {/* Footer / Navigation */}
                <div className="p-4 border-t border-slate-100 flex items-center justify-between shrink-0 bg-slate-50/50 text-xs">
                  <div className="text-[10px] text-slate-500 font-medium">
                    Nhấp vào danh sách bên trái để chuyển đổi nhanh qua các khuyết tật khác.
                  </div>
                  <div className="flex items-center gap-2">
                    <button
                      disabled={allDetections.findIndex(d => d.track_id === selectedDefectModalTrackId) <= 0}
                      onClick={() => {
                        const curIdx = allDetections.findIndex(d => d.track_id === selectedDefectModalTrackId);
                        if (curIdx > 0) {
                          setSelectedDefectModalTrackId(allDetections[curIdx - 1].track_id);
                        }
                      }}
                      className="px-3 py-1.5 rounded-lg border border-slate-200 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-40 disabled:hover:bg-transparent transition-colors"
                    >
                      ← Trước
                    </button>
                    <button
                      disabled={allDetections.findIndex(d => d.track_id === selectedDefectModalTrackId) >= allDetections.length - 1}
                      onClick={() => {
                        const curIdx = allDetections.findIndex(d => d.track_id === selectedDefectModalTrackId);
                        if (curIdx < allDetections.length - 1) {
                          setSelectedDefectModalTrackId(allDetections[curIdx + 1].track_id);
                        }
                      }}
                      className="px-3 py-1.5 rounded-lg border border-slate-200 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-40 disabled:hover:bg-transparent transition-colors"
                    >
                      Kế tiếp →
                    </button>
                  </div>
                </div>

              </div>

            </div>
          </div>
        );
      })()}
    </div>
  );
}
