'use client';

import { useMemo, useState, useEffect } from 'react';
import { Calendar, ChevronLeft, ChevronRight } from 'lucide-react';
import { alignmentAPI } from '@/lib/api';

export interface TimelineScan {
  task_id: string;
  filename: string;
  created_at: string;
  status: string;
  twin_job_id?: string;
  defect_count?: number;
  route_name?: string;
  survey_name?: string;
  route_km_start?: number;
  route_km_end?: number;
  model_type?: string;  // road | bridge | tunnel | slope
  surveyor?: string;
  survey_id?: string; // We need survey_id to fetch evolution
}

interface TimelineSliderProps {
  scans: TimelineScan[];
  activeTaskId: string | null;
  onSelectScan: (taskId: string) => void;
  surveyName?: string;
}

export function TimelineSlider({ scans, activeTaskId, onSelectScan, surveyName }: TimelineSliderProps) {
  const [evolutionData, setEvolutionData] = useState<any>(null);

  // Sort scans by date ascending (oldest first → newest last)
  const sortedScans = useMemo(() => {
    return [...scans].sort((a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime());
  }, [scans]);

  const activeIndex = sortedScans.findIndex(s => s.task_id === activeTaskId);
  const activeScan = activeIndex >= 0 ? sortedScans[activeIndex] : null;

  useEffect(() => {
    if (activeScan && activeScan.survey_id) {
      alignmentAPI.getEvolution(activeScan.survey_id)
        .then((res) => {
          if (res.data && res.data.delta) {
            setEvolutionData(res.data);
          } else {
            setEvolutionData(null);
          }
        })
        .catch(() => setEvolutionData(null));
    } else {
      setEvolutionData(null);
    }
  }, [activeScan?.survey_id]);

  const formatDate = (iso: string) => {
    const d = new Date(iso);
    return d.toLocaleDateString('vi-VN', { day: '2-digit', month: '2-digit', year: 'numeric' });
  };

  const formatTime = (iso: string) => {
    const d = new Date(iso);
    return d.toLocaleTimeString('vi-VN', { hour: '2-digit', minute: '2-digit' });
  };

  const handlePrev = () => {
    if (activeIndex > 0) onSelectScan(sortedScans[activeIndex - 1].task_id);
  };

  const handleNext = () => {
    if (activeIndex < sortedScans.length - 1) onSelectScan(sortedScans[activeIndex + 1].task_id);
  };

  if (sortedScans.length <= 1) return null;

  return (
    <div className="bg-slate-900/80 backdrop-blur-md border-t border-white/10 px-4 py-3">
      {/* Header */}
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2">
          <Calendar className="w-3.5 h-3.5 text-violet-400" />
          <span className="text-[11px] font-semibold text-white/80">
            Lịch sử Scan {surveyName && <span className="text-violet-400">— {surveyName}</span>}
          </span>
          <span className="text-[10px] text-white/40 ml-1">
            {sortedScans.length} phiên bản
          </span>
        </div>
      </div>

      {/* Timeline Track */}
      <div className="flex items-center gap-2">
        {/* Prev button */}
        <button
          onClick={handlePrev}
          disabled={activeIndex <= 0}
          className="p-1 rounded text-white/50 hover:text-white disabled:opacity-20 disabled:cursor-not-allowed transition-colors"
        >
          <ChevronLeft className="w-4 h-4" />
        </button>

        {/* Timeline dots */}
        <div className="flex-1 relative">
          {/* Track line */}
          <div className="absolute top-1/2 left-0 right-0 h-0.5 bg-white/10 -translate-y-1/2 rounded-full" />

          {/* Progress line */}
          {activeIndex >= 0 && (
            <div
              className="absolute top-1/2 left-0 h-0.5 bg-gradient-to-r from-violet-500 to-purple-500 -translate-y-1/2 rounded-full transition-all duration-500"
              style={{ width: `${sortedScans.length > 1 ? (activeIndex / (sortedScans.length - 1)) * 100 : 0}%` }}
            />
          )}

          {/* Dots */}
          <div className="relative flex justify-between">
            {sortedScans.map((scan, idx) => {
              const isActive = scan.task_id === activeTaskId;
              const isPast = idx < activeIndex;

              return (
                <button
                  key={scan.task_id}
                  onClick={() => onSelectScan(scan.task_id)}
                  className="relative group flex flex-col items-center"
                  title={`${formatDate(scan.created_at)} ${formatTime(scan.created_at)}`}
                >
                  {/* Dot */}
                  <div className={`w-3.5 h-3.5 rounded-full border-2 transition-all duration-300 ${
                    isActive
                      ? 'bg-violet-500 border-violet-300 scale-125 shadow-lg shadow-violet-500/50'
                      : isPast
                      ? 'bg-violet-500/40 border-violet-500/30'
                      : 'bg-white/10 border-white/20 hover:bg-white/30 hover:border-white/40'
                  }`} />

                  {/* Label below dot */}
                  <div className={`mt-1.5 text-center transition-opacity ${
                    isActive ? 'opacity-100' : 'opacity-0 group-hover:opacity-100'
                  }`}>
                    <div className={`text-[9px] font-bold whitespace-nowrap ${
                      isActive ? 'text-violet-300' : 'text-white/60'
                    }`}>
                      {formatDate(scan.created_at)}
                    </div>
                    <div className="text-[8px] text-white/40 whitespace-nowrap">{formatTime(scan.created_at)}</div>
                  </div>

                  {/* Active indicator */}
                  {isActive && (
                    <div className="absolute -top-1 left-1/2 -translate-x-1/2 w-5 h-5 rounded-full bg-violet-500/20 animate-ping" />
                  )}
                </button>
              );
            })}
          </div>
        </div>

        {/* Next button */}
        <button
          onClick={handleNext}
          disabled={activeIndex >= sortedScans.length - 1}
          className="p-1 rounded text-white/50 hover:text-white disabled:opacity-20 disabled:cursor-not-allowed transition-colors"
        >
          <ChevronRight className="w-4 h-4" />
        </button>
      </div>

      {/* Semantic 4D Evolution Dashboard */}
      {evolutionData && evolutionData.delta && (
        <div className="mt-4 pt-3 border-t border-white/10 grid grid-cols-2 gap-4 animate-fade-in">
          <div className="bg-white/5 rounded-lg p-2.5 border border-white/10 relative overflow-hidden">
             <div className="absolute top-0 left-0 w-1 h-full bg-blue-500" />
             <div className="text-[9px] text-white/50 uppercase font-bold tracking-wider mb-1">
               Biến động Sự cố
             </div>
             <div className="flex items-end gap-2">
               <span className="text-xl font-bold text-white">
                 {evolutionData.current?.total_incidents || 0}
               </span>
               {evolutionData.delta.incidents !== 0 && (
                 <span className={`text-xs font-bold mb-1 ${evolutionData.delta.incidents > 0 ? 'text-red-400' : 'text-emerald-400'}`}>
                   {evolutionData.delta.incidents > 0 ? '+' : ''}{evolutionData.delta.incidents} so với lần trước
                 </span>
               )}
             </div>
          </div>
          <div className="bg-white/5 rounded-lg p-2.5 border border-white/10 relative overflow-hidden">
             <div className="absolute top-0 left-0 w-1 h-full bg-rose-500" />
             <div className="text-[9px] text-white/50 uppercase font-bold tracking-wider mb-1">
               Suy thoái nghiêm trọng (Cấp C, D)
             </div>
             <div className="flex items-end gap-2">
               <span className="text-xl font-bold text-rose-400">
                 {(evolutionData.current?.by_tcvn_grade?.C || 0) + (evolutionData.current?.by_tcvn_grade?.D || 0)}
               </span>
               <span className="text-xs font-bold text-white/40 mb-1">
                 điểm phát sinh mới
               </span>
             </div>
          </div>
        </div>
      )}
    </div>
  );
}
