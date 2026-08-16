'use client';

import React from 'react';
import { Zap, Clock, Timer, Activity, CheckCircle2, Loader2, BarChart2 } from 'lucide-react';

interface TelemetryData {
  task_id?: string;
  filename?: string;
  progress?: number;
  processingStatus?: string;
  fps?: number;
  eta_seconds?: number;
  elapsed_seconds?: number;
  processed_count?: number;
  total_count?: number;
}

interface RealtimeTelemetryHUDProps {
  telemetry: TelemetryData;
  onClose?: () => void;
}

export default function RealtimeTelemetryHUD({ telemetry, onClose }: RealtimeTelemetryHUDProps) {
  const {
    filename = 'Tác vụ khảo sát',
    progress = 0,
    processingStatus = 'Đang phân tích...',
    fps = 0,
    eta_seconds = 0,
    elapsed_seconds = 0,
    processed_count = 0,
    total_count = 0,
  } = telemetry;

  const formatSeconds = (sec?: number) => {
    if (!sec || sec <= 0) return 'Đang tính toán...';
    const h = Math.floor(sec / 3600);
    const m = Math.floor((sec % 3600) / 60);
    const s = sec % 60;
    if (h > 0) return `${h} giờ ${m} phút`;
    if (m > 0) return `${m} phút ${s} giây`;
    return `${s} giây`;
  };

  const formatElapsed = (sec?: number) => {
    if (!sec || sec <= 0) return '00:00';
    const m = Math.floor(sec / 60);
    const s = sec % 60;
    return `${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
  };

  const msPerFrame = fps > 0 ? (1000 / fps).toFixed(1) : 0;

  return (
    <div className="bg-slate-900 text-white rounded-2xl p-5 shadow-2xl border border-slate-800 relative overflow-hidden backdrop-blur-xl">
      {/* Background Ambient Glow */}
      <div className="absolute -top-24 -right-24 w-60 h-60 bg-blue-600/20 rounded-full blur-3xl pointer-events-none" />
      <div className="absolute -bottom-24 -left-24 w-60 h-60 bg-emerald-600/15 rounded-full blur-3xl pointer-events-none" />

      {/* Header */}
      <div className="flex items-center justify-between pb-3 border-b border-slate-800 mb-4">
        <div className="flex items-center gap-2.5">
          <div className="w-8 h-8 rounded-xl bg-blue-500/20 border border-blue-400/30 flex items-center justify-center text-blue-400">
            <Activity className="w-4 h-4 animate-pulse" />
          </div>
          <div>
            <h3 className="text-xs font-black uppercase tracking-wider text-slate-200">
              Giám sát Tốc độ Phân tích Realtime (AI HUD)
            </h3>
            <p className="text-[10px] text-slate-400 font-mono truncate max-w-[280px]">
              {filename}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-emerald-500/10 border border-emerald-500/30 text-emerald-400 text-[10px] font-black uppercase tracking-wider">
            <Loader2 className="w-3 h-3 animate-spin text-emerald-400" /> Live AI Engine
          </span>
        </div>
      </div>

      {/* Telemetry Metrics Grid */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
        {/* 1. Processing Speed (FPS) */}
        <div className="bg-slate-800/80 border border-slate-700/60 rounded-xl p-3 flex flex-col justify-between">
          <div className="flex items-center justify-between text-[10px] font-bold text-slate-400 uppercase tracking-wider">
            <span>Tốc độ xử lý</span>
            <Zap className="w-3.5 h-3.5 text-amber-400" />
          </div>
          <div className="mt-2">
            <div className="text-lg font-black text-white font-mono tracking-tight">
              {fps > 0 ? fps : '--'} <span className="text-xs font-bold text-amber-400">ảnh/s</span>
            </div>
            <div className="text-[9px] text-slate-400 font-medium mt-0.5">
              ~{msPerFrame} ms / khung hình
            </div>
          </div>
        </div>

        {/* 2. Estimated Remaining Time (ETA) */}
        <div className="bg-slate-800/80 border border-slate-700/60 rounded-xl p-3 flex flex-col justify-between">
          <div className="flex items-center justify-between text-[10px] font-bold text-slate-400 uppercase tracking-wider">
            <span>Dự toán còn lại (ETA)</span>
            <Timer className="w-3.5 h-3.5 text-blue-400" />
          </div>
          <div className="mt-2">
            <div className="text-sm font-black text-blue-300 font-mono tracking-tight truncate">
              {formatSeconds(eta_seconds)}
            </div>
            <div className="text-[9px] text-slate-400 font-medium mt-0.5">
              Tốc độ trung bình thời gian thực
            </div>
          </div>
        </div>

        {/* 3. Elapsed Time */}
        <div className="bg-slate-800/80 border border-slate-700/60 rounded-xl p-3 flex flex-col justify-between">
          <div className="flex items-center justify-between text-[10px] font-bold text-slate-400 uppercase tracking-wider">
            <span>Đã phân tích</span>
            <Clock className="w-3.5 h-3.5 text-emerald-400" />
          </div>
          <div className="mt-2">
            <div className="text-lg font-black text-emerald-300 font-mono tracking-tight">
              {formatElapsed(elapsed_seconds)}
            </div>
            <div className="text-[9px] text-slate-400 font-medium mt-0.5">
              Thời gian chạy từ lúc bắt đầu
            </div>
          </div>
        </div>

        {/* 4. Total Frames Processed */}
        <div className="bg-slate-800/80 border border-slate-700/60 rounded-xl p-3 flex flex-col justify-between">
          <div className="flex items-center justify-between text-[10px] font-bold text-slate-400 uppercase tracking-wider">
            <span>Khung hình / Ảnh</span>
            <BarChart2 className="w-3.5 h-3.5 text-purple-400" />
          </div>
          <div className="mt-2">
            <div className="text-sm font-black text-purple-300 font-mono tracking-tight truncate">
              {processed_count > 0 ? processed_count.toLocaleString('vi-VN') : 0} / {total_count > 0 ? total_count.toLocaleString('vi-VN') : '---'}
            </div>
            <div className="text-[9px] text-slate-400 font-medium mt-0.5">
              {total_count > processed_count ? `Còn ${ (total_count - processed_count).toLocaleString('vi-VN') } ảnh` : 'Đang hoàn tất'}
            </div>
          </div>
        </div>
      </div>

      {/* Progress Bar & Status Text */}
      <div className="space-y-1.5">
        <div className="flex items-center justify-between text-[11px] font-bold">
          <span className="text-slate-300 truncate max-w-[80%] font-medium">
            {processingStatus}
          </span>
          <span className="text-blue-400 font-mono font-black">
            {progress}%
          </span>
        </div>
        <div className="w-full bg-slate-800 rounded-full h-2.5 overflow-hidden p-0.5 border border-slate-700/50">
          <div
            className="bg-gradient-to-r from-blue-500 via-indigo-500 to-emerald-400 h-full rounded-full transition-all duration-500 relative"
            style={{ width: `${Math.max(3, Math.min(100, progress))}%` }}
          >
            <div className="absolute inset-0 bg-white/20 animate-pulse rounded-full" />
          </div>
        </div>
      </div>
    </div>
  );
}
