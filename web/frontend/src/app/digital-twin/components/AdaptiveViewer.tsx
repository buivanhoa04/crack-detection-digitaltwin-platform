'use client';

import React, { useState, useEffect, useRef } from 'react';
import { ThreeViewer } from './ThreeViewer';
import { 
  Image as ImageIcon, 
  ChevronLeft, 
  ChevronRight, 
  ChevronDown,
  ChevronUp,
  Maximize2,
  Bot,
  Ruler,
  Sun,
  Layers,
  Camera,
  RotateCcw
} from 'lucide-react';
import { DefectMarkerData } from '../types';
import { withAccessToken } from '@/lib/mediaAuth';

interface AdaptiveViewerProps {
  jobId?: string;
  layers: { mesh: boolean; texture: boolean };
  defects: DefectMarkerData[];
  focusTrackId: number | null;
  onMarkerClick: (trackId: number) => void;
  activeFilters: { classes: string[]; severities: string[] };
  
  // 2D fallback data (from crackAPI.getStatus or task details)
  taskData?: any;
  isLoadingTask?: boolean;
}

export default function AdaptiveViewer({
  jobId,
  layers,
  defects,
  focusTrackId,
  onMarkerClick,
  activeFilters,
  taskData,
  isLoadingTask = false
}: AdaptiveViewerProps) {
  const [activeFrameIdx, setActiveFrameIdx] = useState(0);
  const [showBBox, setShowBBox] = useState(true);
  const [naturalSize, setNaturalSize] = useState({ width: 1920, height: 1080 });

  // Fullscreen States & Controls
  const containerRef = useRef<HTMLDivElement>(null);
  const [isFullscreen, setIsFullscreen] = useState(false);

  const toggleFullscreen = () => {
    if (!containerRef.current) return;
    if (!document.fullscreenElement) {
      containerRef.current.requestFullscreen().catch((err) => {
        console.error(`Error enabling fullscreen: ${err.message}`);
      });
    } else {
      document.exitFullscreen();
    }
  };

  useEffect(() => {
    const handleFullscreenChange = () => {
      setIsFullscreen(!!document.fullscreenElement);
    };
    document.addEventListener('fullscreenchange', handleFullscreenChange);
    return () => {
      document.removeEventListener('fullscreenchange', handleFullscreenChange);
    };
  }, []);

  // 3D Dashboard States
  const [isDashboardCollapsed, setIsDashboardCollapsed] = useState(false);
  const [localLayers, setLocalLayers] = useState(layers);
  const [measurementMode, setMeasurementMode] = useState(false);
  const [measuredDistance, setMeasuredDistance] = useState<number | null>(null);
  const [scaleFactor, setScaleFactor] = useState(1.0);
  const [manualScaleInput, setManualScaleInput] = useState('3.5');
  const [lightIntensity, setLightIntensity] = useState(1.5);
  const [cameraPreset, setCameraPreset] = useState<'reset' | 'topdown' | null>(null);

  // Sync prop layers changes
  useEffect(() => {
    setLocalLayers(layers);
  }, [layers]);

  // Load scale factor for current jobId from localStorage
  useEffect(() => {
    if (jobId) {
      const saved = localStorage.getItem(`scale_factor_${jobId}`);
      if (saved) {
        setScaleFactor(parseFloat(saved));
      } else {
        setScaleFactor(1.0);
      }
    }
    setMeasurementMode(false);
    setMeasuredDistance(null);
  }, [jobId]);

  const handleSaveScaleFactor = (factor: number) => {
    setScaleFactor(factor);
    if (jobId) {
      localStorage.setItem(`scale_factor_${jobId}`, factor.toString());
    }
  };

  const handleCameraPresetApplied = () => {
    setCameraPreset(null);
  };

  // Check if we have 3D model capability
  const has3DModel = !!jobId && jobId !== 'null' && jobId !== '';

  const frames = taskData?.best_frames || [];
  const currentFrame = frames[activeFrameIdx];
  const imageUrl = currentFrame?.frameFilePath 
    ? withAccessToken(`/api/v1/files/${currentFrame.frameFilePath.replace(/\\/g, '/').replace(/^(\/)?api\/v1\/files\//, '').replace(/^\//, '')}`)
    : '';

  const handlePrev = () => {
    setActiveFrameIdx(prev => (prev === 0 ? frames.length - 1 : prev - 1));
  };

  const handleNext = () => {
    setActiveFrameIdx(prev => (prev === frames.length - 1 ? 0 : prev + 1));
  };

  return (
    <div ref={containerRef} className="w-full h-full flex flex-col bg-slate-950 relative overflow-hidden select-none">
      
      {/* Top controls (only shown when not in 3D) */}
      {!has3DModel && (
        <div className="absolute top-4 left-4 right-4 flex justify-between items-center z-10">
          <div className="flex items-center gap-2">
            <span className="px-2.5 py-1 rounded-lg bg-black/60 text-white text-[9px] font-bold border border-white/10 backdrop-blur-sm">
              CHẾ ĐỘ XEM 2D
            </span>
            {frames.length > 0 && (
              <span className="px-2.5 py-1 rounded-lg bg-black/60 text-white text-[9px] font-bold border border-white/10 backdrop-blur-sm">
                ẢNH {activeFrameIdx + 1} / {frames.length}
              </span>
            )}
          </div>
          
          {frames.length > 0 && (
            <button
              onClick={() => setShowBBox(!showBBox)}
              className={`px-3 py-1 rounded-lg text-[9px] font-bold border transition-colors backdrop-blur-sm ${showBBox ? 'bg-emerald-600 border-emerald-500 text-white' : 'bg-black/60 border-white/10 text-slate-300'}`}
            >
              {showBBox ? 'Ẩn Báo Đỏ AI' : 'Hiện Báo Đỏ AI'}
            </button>
          )}
        </div>
      )}

      {/* Main viewport */}
      <div className="flex-1 flex items-center justify-center relative">
        {has3DModel ? (
          <>
            {/* SOTA HTML5 Fullscreen Toggle Button */}
            <button
              onClick={toggleFullscreen}
              className="absolute top-4 left-4 z-20 p-2 rounded-xl bg-slate-900/80 hover:bg-slate-900 border border-white/10 text-white shadow-lg pointer-events-auto transition-all hover:scale-105 active:scale-95 flex items-center justify-center cursor-pointer"
              title={isFullscreen ? "Thoát toàn màn hình" : "Xem toàn màn hình (Fullscreen)"}
            >
              <Maximize2 className="w-4 h-4" />
            </button>

            <ThreeViewer
              jobId={jobId!}
              layers={localLayers}
              defects={defects}
              focusTrackId={focusTrackId}
              onMarkerClick={onMarkerClick}
              activeFilters={activeFilters}
              measurementMode={measurementMode}
              onMeasureDistance={setMeasuredDistance}
              scaleFactor={scaleFactor}
              lightIntensity={lightIntensity}
              cameraPreset={cameraPreset}
              onCameraPresetApplied={handleCameraPresetApplied}
            />
          </>
        ) : isLoadingTask ? (
          <div className="flex flex-col items-center justify-center gap-2 text-white/50">
            <div className="w-6 h-6 rounded-full border-2 border-white/10 border-t-white animate-spin" />
            <span className="text-xs">Đang tải ảnh...</span>
          </div>
        ) : frames.length === 0 ? (
          <div className="flex flex-col items-center justify-center gap-2 text-white/30">
            <ImageIcon className="w-12 h-12" />
            <span className="text-xs">Không có dữ liệu hình ảnh</span>
          </div>
        ) : (
          <div className="relative max-w-full max-h-full inline-block p-8">
            {imageUrl && (
              <img 
                src={imageUrl} 
                className="max-w-full max-h-[60vh] object-contain rounded-lg border border-white/5 shadow-2xl" 
                alt="Defect Snapshot"
                onLoad={(event) => setNaturalSize({
                  width: event.currentTarget.naturalWidth || 1920,
                  height: event.currentTarget.naturalHeight || 1080,
                })}
              />
            )}

            {/* BBox overlays in 2D gallery */}
            {showBBox && currentFrame?.detections?.map((det: any, idx: number) => {
              const isNormalized = Math.max(...(det.bbox || [0])) <= 1.05;
              const bbox = isNormalized ? det.bbox : [det.bbox[0]/naturalSize.width, det.bbox[1]/naturalSize.height, det.bbox[2]/naturalSize.width, det.bbox[3]/naturalSize.height];
              const rawPolygon = Array.isArray(det.polygon)
                ? det.polygon.filter((p: unknown) => Array.isArray(p) && p.length >= 2)
                : [];
              const polygonIsNormalized = rawPolygon.length > 0
                && Math.max(...rawPolygon.flatMap((p: number[]) => [Number(p[0]), Number(p[1])])) <= 1.05;
              const polygon = rawPolygon.length > 0
                ? (polygonIsNormalized
                    ? rawPolygon
                    : rawPolygon.map((p: number[]) => [Number(p[0])/naturalSize.width, Number(p[1])/naturalSize.height]))
                : null;
              const isActive = det.track_id === focusTrackId;

              return (
                <svg key={idx} viewBox="0 0 1 1" preserveAspectRatio="none" className="absolute inset-0 w-full h-full pointer-events-none">
                  {polygon && Array.isArray(polygon) && polygon.length > 0 ? (
                    <polygon
                      points={polygon.map((p: number[]) => `${p[0]},${p[1]}`).join(' ')}
                      className={`stroke-2 transition-all ${isActive ? 'stroke-yellow-400 fill-yellow-400/30' : 'stroke-rose-500 fill-rose-500/20'}`}
                      style={{ strokeWidth: isActive ? 0.007 : 0.004 }}
                    />
                  ) : (
                    bbox && bbox.length === 4 && (
                      <rect
                        x={bbox[0]}
                        y={bbox[1]}
                        width={bbox[2] - bbox[0]}
                        height={bbox[3] - bbox[1]}
                        className={`border-2 transition-all ${isActive ? 'stroke-yellow-400 fill-yellow-400/30' : 'stroke-rose-500 fill-rose-500/20'}`}
                        style={{ strokeWidth: isActive ? 0.007 : 0.004 }}
                      />
                    )
                  )}
                </svg>
              );
            })}
          </div>
        )}

        {/* Gallery navigation buttons */}
        {!has3DModel && frames.length > 1 && (
          <>
            <button 
              onClick={handlePrev}
              className="absolute left-4 top-1/2 -translate-y-1/2 p-2 rounded-full bg-black/60 text-white border border-white/10 hover:bg-black/90 transition-all z-10"
            >
              <ChevronLeft className="w-5 h-5" />
            </button>
            <button 
              onClick={handleNext}
              className="absolute right-4 top-1/2 -translate-y-1/2 p-2 rounded-full bg-black/60 text-white border border-white/10 hover:bg-black/90 transition-all z-10"
            >
              <ChevronRight className="w-5 h-5" />
            </button>
          </>
        )}
      </div>

      {/* ── 3D Floating Dashboard Overlay ─────────────────────────────────── */}
      {has3DModel && (
        <div className="absolute top-4 right-4 z-10 flex flex-col gap-3 max-w-[20rem] animate-fade-in pointer-events-none">
          <div className="glass-card bg-slate-900/85 backdrop-blur-md border border-white/10 p-3.5 rounded-2xl shadow-xl flex flex-col gap-3.5 w-72 pointer-events-auto text-white">
            
            <div 
              onClick={() => setIsDashboardCollapsed(!isDashboardCollapsed)}
              className="flex items-center justify-between border-b border-white/10 pb-2 cursor-pointer select-none hover:opacity-85"
            >
              <div className="flex items-center gap-1.5">
                <span className="text-[10px] font-bold uppercase tracking-wider text-slate-300">Bản đồ số 3D</span>
                <span className="px-1.5 py-0.5 rounded bg-blue-500 text-white font-bold text-[8px] uppercase tracking-wide">RTK Calibrated</span>
              </div>
              <button className="text-slate-400 hover:text-white transition-colors">
                {isDashboardCollapsed ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronUp className="w-3.5 h-3.5" />}
              </button>
            </div>

            {!isDashboardCollapsed && (
              <>
                {/* 1. Measurement Section */}
                <div className="space-y-1.5">
                  <div className="flex justify-between items-center">
                    <span className="text-[10px] text-slate-400 font-bold uppercase tracking-wide flex items-center gap-1.5">
                      <Ruler className="w-3.5 h-3.5 text-rose-400" /> Đo đạc & Tỉ lệ
                    </span>
                    {measuredDistance !== null && (
                      <button 
                        onClick={() => { setMeasuredDistance(null); }}
                        className="text-[9px] text-rose-400 hover:text-rose-300 font-bold"
                      >
                        ✕ Xóa đo
                      </button>
                    )}
                  </div>
                  
                  <div className="flex gap-2">
                    <button
                      onClick={() => setMeasurementMode(!measurementMode)}
                      className={`flex-1 py-1.5 px-3 rounded-lg text-xs font-bold transition-all border ${
                        measurementMode 
                          ? 'bg-rose-500 border-rose-400 text-white shadow-lg shadow-rose-500/20' 
                          : 'bg-white/5 border-white/10 text-slate-300 hover:bg-white/10'
                      }`}
                    >
                      {measurementMode ? 'Thước đo: Bật' : 'Bật Thước đo 3D'}
                    </button>
                  </div>

                  {measurementMode && (
                    <div className="bg-white/5 border border-white/10 p-2.5 rounded-xl text-xs space-y-2.5 mt-2 animate-fade-in">
                      <div className="flex justify-between items-center">
                        <span className="text-slate-400 text-[10px]">Tỉ lệ hiện tại:</span>
                        <span className="font-bold text-slate-200">{scaleFactor.toFixed(3)}x</span>
                      </div>

                      {measuredDistance !== null ? (
                        <div className="space-y-2">
                          <div className="flex justify-between">
                            <span className="text-slate-400 text-[10px]">Khoảng cách ảo:</span>
                            <span className="font-bold text-slate-200">{measuredDistance.toFixed(3)} đ.vị</span>
                          </div>
                          <div className="flex justify-between items-center">
                            <span className="text-slate-400 text-[10px]">Khoảng cách thực:</span>
                            <span className="font-bold text-rose-400">{(measuredDistance * scaleFactor).toFixed(2)}m</span>
                          </div>

                          <div className="pt-2 border-t border-white/10 space-y-1.5">
                            <span className="text-slate-400 text-[9px] block">Hiệu chuẩn khoảng cách (m):</span>
                            <div className="flex gap-1.5">
                              <input 
                                type="number"
                                step="0.1"
                                value={manualScaleInput}
                                onChange={(e) => setManualScaleInput(e.target.value)}
                                className="w-20 bg-slate-800 border border-white/15 rounded px-2 py-1 text-center font-bold text-white focus:outline-none"
                                placeholder="3.5"
                              />
                              <button
                                onClick={() => {
                                  const physical = parseFloat(manualScaleInput);
                                  if (physical > 0 && measuredDistance > 0) {
                                    const newS = physical / measuredDistance;
                                    handleSaveScaleFactor(newS);
                                  }
                                }}
                                className="flex-1 py-1 px-2 bg-emerald-600 hover:bg-emerald-500 rounded text-[9px] font-bold text-white transition-colors"
                              >
                                Lưu Tỉ Lệ
                              </button>
                            </div>
                          </div>
                        </div>
                      ) : (
                        <p className="text-[10px] text-slate-400 italic text-center">Click 2 điểm để đo đạc</p>
                      )}
                    </div>
                  )}
                </div>

                {/* 2. Layers Toggles */}
                <div className="space-y-2 border-t border-white/10 pt-3">
                  <span className="text-[10px] text-slate-400 font-bold uppercase tracking-wide flex items-center gap-1.5">
                    <Layers className="w-3.5 h-3.5 text-blue-400" /> Các lớp hiển thị
                  </span>
                  <div className="flex gap-2">
                    <button
                      onClick={() => setLocalLayers(prev => ({ ...prev, mesh: !prev.mesh }))}
                      className={`flex-1 py-1.5 rounded-lg text-[10px] font-bold transition-all border ${
                        localLayers.mesh 
                          ? 'bg-blue-500/20 border-blue-500/50 text-blue-300' 
                          : 'bg-white/5 border-white/10 text-slate-400'
                      }`}
                    >
                      {localLayers.mesh ? 'Mesh: Bật' : 'Mesh: Tắt'}
                    </button>
                    <button
                      onClick={() => setLocalLayers(prev => ({ ...prev, texture: !prev.texture }))}
                      className={`flex-1 py-1.5 rounded-lg text-[10px] font-bold transition-all border ${
                        localLayers.texture 
                          ? 'bg-blue-500/20 border-blue-500/50 text-blue-300' 
                          : 'bg-white/5 border-white/10 text-slate-400'
                      }`}
                    >
                      {localLayers.texture ? 'Vân ảnh: Bật' : 'Vân ảnh: Tắt'}
                    </button>
                  </div>
                </div>

                {/* 3. Camera Controls */}
                <div className="space-y-2 border-t border-white/10 pt-3">
                  <span className="text-[10px] text-slate-400 font-bold uppercase tracking-wide flex items-center gap-1.5">
                    <Camera className="w-3.5 h-3.5 text-violet-400" /> Điểm nhìn camera
                  </span>
                  <div className="flex gap-2">
                    <button
                      onClick={() => setCameraPreset('reset')}
                      className="flex-1 py-1.5 bg-white/5 border border-white/10 hover:bg-white/10 text-slate-300 rounded-lg text-[10px] font-bold transition-colors"
                    >
                      Reset View
                    </button>
                    <button
                      onClick={() => setCameraPreset('topdown')}
                      className="flex-1 py-1.5 bg-white/5 border border-white/10 hover:bg-white/10 text-slate-300 rounded-lg text-[10px] font-bold transition-colors"
                    >
                      Góc nhìn Đỉnh
                    </button>
                  </div>
                </div>

                {/* 4. Light Control */}
                <div className="space-y-2 border-t border-white/10 pt-3">
                  <div className="flex justify-between items-center text-[10px]">
                    <span className="text-slate-400 font-bold uppercase tracking-wide flex items-center gap-1.5">
                      <Sun className="w-3.5 h-3.5 text-amber-400" /> Cường độ sáng
                    </span>
                    <span className="font-bold text-amber-400">{lightIntensity.toFixed(1)}x</span>
                  </div>
                  <input 
                    type="range"
                    min="0.5"
                    max="3.0"
                    step="0.1"
                    value={lightIntensity}
                    onChange={(e) => setLightIntensity(parseFloat(e.target.value))}
                    className="w-full h-1 bg-slate-800 rounded-lg appearance-none cursor-pointer accent-amber-500"
                  />
                </div>
              </>
            )}
          </div>
        </div>
      )}

      {/* Bottom thumbnails strip */}
      {!has3DModel && frames.length > 1 && (
        <div className="h-16 shrink-0 bg-black/40 border-t border-white/5 p-2 flex gap-2 overflow-x-auto custom-scrollbar scroll-smooth">
          {frames.map((frame: any, idx: number) => {
            const thumbUrl = frame.frameFilePath 
              ? withAccessToken(`/api/v1/files/${frame.frameFilePath.replace(/\\/g, '/').replace(/^(\/)?api\/v1\/files\//, '').replace(/^\//, '')}`)
              : '';
            return (
              <button
                key={idx}
                onClick={() => setActiveFrameIdx(idx)}
                className={`h-full aspect-[4/3] rounded-lg overflow-hidden shrink-0 border-2 transition-all ${activeFrameIdx === idx ? 'border-blue-500 scale-105 opacity-100 shadow-md' : 'border-transparent opacity-40 hover:opacity-100'}`}
              >
                {thumbUrl ? (
                  <img src={thumbUrl} className="w-full h-full object-cover" alt="" />
                ) : (
                  <div className="w-full h-full flex items-center justify-center bg-slate-900"><Bot className="w-4 h-4 text-slate-700" /></div>
                )}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
