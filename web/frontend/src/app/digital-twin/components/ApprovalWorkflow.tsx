'use client';

import React, { useState, useEffect } from 'react';
import { 
  X, 
  CheckCircle, 
  XCircle, 
  Bot, 
  Info, 
  Maximize2, 
  CheckCircle2, 
  AlertCircle,
  Brain,
  ChevronLeft,
  ChevronRight,
  Loader2
} from 'lucide-react';
import { archiveAPI, surveysAPI, calibrationAPI, crackAPI } from '@/lib/api';
import { withAccessToken } from '@/lib/mediaAuth';
import { translateAIClass, autoTCVNGrade, TCVN_GRADES, suggestRepairMethod } from '@/lib/translate';
import dynamic from 'next/dynamic';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

const MiniMapPicker = dynamic(() => import('@/components/map/MiniMapPicker'), {
  ssr: false,
  loading: () => <div className="w-full h-48 bg-slate-100 animate-pulse rounded-2xl" />
});

const fetchReverseGeocode = async (lat: number, lng: number, routeName?: string, routeKm?: number): Promise<string> => {
  try {
    const url = `https://nominatim.openstreetmap.org/reverse?format=json&lat=${lat}&lon=${lng}&zoom=18&addressdetails=1&accept-language=vi`;
    const response = await fetch(url, {
      headers: {
        'User-Agent': 'DigitalTwinInspectionApp/1.0'
      }
    });
    if (!response.ok) throw new Error('Geocoding network error');
    const data = await response.json();
    
    const address = data.address || {};
    const parts: string[] = [];
    
    const road = address.road || address.suburb || address.quarter || address.neighbourhood;
    const village = address.village || address.town || address.city_district || address.district;
    const city = address.city || address.province || address.state;

    if (road) parts.push(road);
    if (village) parts.push(village);
    if (city) parts.push(city);

    let addressStr = parts.join(', ');
    if (!addressStr && data.display_name) {
      addressStr = data.display_name;
    }
    
    let prefix = '';
    if (routeName) {
      prefix = `Tuyến ${routeName}`;
      if (routeKm !== undefined && routeKm !== null && routeKm > 0) {
        prefix = `Km ${routeKm} - ${prefix}`;
      } else if (routeKm === 0) {
        prefix = `Km 0 - ${prefix}`;
      }
    }
    
    if (prefix) {
      return addressStr ? `${prefix}, ${addressStr}` : prefix;
    }
    
    return addressStr || `${lat.toFixed(5)}, ${lng.toFixed(5)}`;
  } catch (error) {
    console.error('Failed to reverse geocode:', error);
    let fallback = `${lat.toFixed(5)}, ${lng.toFixed(5)}`;
    if (routeName) {
      let routePrefix = `Tuyến ${routeName}`;
      if (routeKm !== undefined && routeKm !== null && routeKm >= 0) {
        routePrefix = `Km ${routeKm} - ${routePrefix}`;
      }
      fallback = `${routePrefix}, ${fallback}`;
    }
    return fallback;
  }
};

interface ApprovalWorkflowProps {
  show: boolean;
  taskId: string;
  frameIndex: number | null;
  taskData: any;
  onClose: () => void;
  onApproved: (updatedTask: any, isFrameLevel: boolean) => void;
}

export default function ApprovalWorkflow({
  show,
  taskId,
  frameIndex,
  taskData,
  onClose,
  onApproved
}: ApprovalWorkflowProps) {
  const [surveys, setSurveys] = useState<any[]>([]);
  const [showBBox, setShowBBox] = useState(true);
  const [loadingCalib, setLoadingCalib] = useState(false);
  const [naturalSize, setNaturalSize] = useState({ width: 1920, height: 1080 });
  const [activeRightTab, setActiveRightTab] = useState<'form' | 'ai'>('form');
  const [currentFrameIndex, setCurrentFrameIndex] = useState<number>(frameIndex ?? 0);
  const [highlightedTrackId, setHighlightedTrackId] = useState<string | null>(null);
  const [isAnalyzingAll, setIsAnalyzingAll] = useState(false);
  const [frameAnalysis, setFrameAnalysis] = useState<any>(null);
  const [isAnalyzingFrame, setIsAnalyzingFrame] = useState(false);
  const [isSavingDecision, setIsSavingDecision] = useState(false);
  const [details, setDetails] = useState<any>({
    title: '',
    description: '',
    severity: 'warning',
    asset_type: 'road',
    lat: null,
    lng: null,
    address: '',
    route_name: '',
    route_km: null,
    lane_position: '',
    tcvn_grade: '',
    tcvn_grade_auto: '',
    survey_id: '',
    damage_area_m2: null,
    damage_width_mm: null,
    repair_method: '',
    classification: '',
    confidence: 0,
    gsd_mm_per_pixel: null,
    calibration_source: '',
    is_calibrated: false,
  });

  // Filter & Pagination for left frame list
  const [defectFilter, setDefectFilter] = useState<'all' | 'defects' | 'clean'>('all');
  const [pageSize, setPageSize] = useState<number>(50);
  const [listPage, setListPage] = useState<number>(1);

  const allFrames = (taskData?.best_frames || []).map((f: any, idx: number) => ({
    ...f,
    originalIndex: idx
  }));

  const filteredFrames = allFrames.filter((frame: any) => {
    const hasDefects = frame.detections && frame.detections.length > 0;
    if (defectFilter === 'defects') return hasDefects;
    if (defectFilter === 'clean') return !hasDefects;
    return true;
  });

  const totalListPages = Math.ceil(filteredFrames.length / pageSize) || 1;
  const paginatedFrames = filteredFrames.slice((listPage - 1) * pageSize, listPage * pageSize);

  // Lightbox zoom & pan
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const [isPanning, setIsPanning] = useState(false);
  const [startPan, setStartPan] = useState({ x: 0, y: 0 });

  // Pre-load surveys
  useEffect(() => {
    surveysAPI.getAll().then(res => setSurveys(res.data?.surveys || [])).catch(() => {});
  }, []);

  // Sync currentFrameIndex when prop changes
  useEffect(() => {
    setCurrentFrameIndex(frameIndex ?? 0);
  }, [frameIndex]);

  // Reset highlight when current frame changes
  useEffect(() => {
    setHighlightedTrackId(null);
  }, [currentFrameIndex]);

  // Set initial form values based on task details
  useEffect(() => {
    if (!show || !taskData) return;

    const setupForm = async () => {
      setLoadingCalib(true);
      const targetFrame = currentFrameIndex !== null ? taskData.best_frames?.[currentFrameIndex] : taskData.best_frames?.[0];
      const detectionClass = targetFrame?.detections?.[0]?.class || '';
      const conf = targetFrame?.detections?.[0]?.confidence || 0;
      const vnClass = translateAIClass(detectionClass);
      const autoTitle = vnClass ? `Phát hiện: ${vnClass}` : `Ghi nhận sự cố`;
      const linkedSurvey = surveys.find(s => s.id === taskData.survey_id)
        || surveys.find(s => taskData.route_name && s.route_name === taskData.route_name);

      // Auto GSD Calibration
      let damageArea: number | null = null;
      let damageWidth: number | null = null;
      let isCalib = false;
      let gsdUsed: number | null = null;
      let calibSource = '';

      if (targetFrame?.detections && targetFrame.detections.length > 0) {
        try {
          const calibRes = await calibrationAPI.processFrame(targetFrame.detections);
          if (calibRes.data?.status && calibRes.data.data?.is_calibrated) {
             const calibData = calibRes.data.data;
             isCalib = true;
             gsdUsed = calibData.gsd_mm_per_pixel;
             calibSource = calibData.calibration_source;
             
             const mainDamage = calibData.damages.find((d: any) => d.class_name === detectionClass);
             if (mainDamage) {
               damageArea = mainDamage.real_area_m2;
               damageWidth = mainDamage.real_width_mm;
             }
          }
        } catch (e) {
          console.warn("[GSD Calibration] Failed to run calibration:", e);
        }
      }

      const autoGrade = isCalib
        ? autoTCVNGrade(detectionClass, conf, damageArea || 0, damageWidth || 0, true)
        : '';
      const autoSeverity = ['D','E'].includes(autoGrade) ? 'critical' : 'warning';

      const rawLat = targetFrame?.lat ?? targetFrame?.latitude ?? taskData.lat ?? taskData.latitude;
      const rawLng = targetFrame?.lng ?? targetFrame?.longitude ?? taskData.lng ?? taskData.longitude;
      const initialLat = Number.isFinite(Number(rawLat)) ? Number(rawLat) : null;
      const initialLng = Number.isFinite(Number(rawLng)) ? Number(rawLng) : null;
      const initialRouteName = linkedSurvey?.route_name || '';
      const rawRouteKm = targetFrame?.route_km ?? taskData.route_km ?? linkedSurvey?.route_km_start;
      const initialRouteKm = Number.isFinite(Number(rawRouteKm)) ? Number(rawRouteKm) : null;

      let initialAddress = '';
      if (initialLat !== null && initialLng !== null) {
        try {
          initialAddress = await fetchReverseGeocode(initialLat, initialLng, initialRouteName, initialRouteKm ?? undefined);
        } catch (e) {
          console.warn("Failed to get initial geocode", e);
        }
      }

      setDetails({
        title: autoTitle,
        description: `AI phát hiện từ: ${taskData.filename || taskData.task_id}`,
        severity: autoSeverity,
        asset_type: taskData.infrastructure_category || 'road',
        lat: initialLat,
        lng: initialLng,
        address: initialAddress,
        route_name: initialRouteName,
        route_km: initialRouteKm,
        lane_position: '',
        tcvn_grade: autoGrade,
        tcvn_grade_auto: autoGrade,
        survey_id: taskData.survey_id || linkedSurvey?.id || '',
        damage_area_m2: damageArea,
        damage_width_mm: damageWidth,
        repair_method: suggestRepairMethod(detectionClass),
        classification: detectionClass,
        confidence: conf,
        gsd_mm_per_pixel: gsdUsed,
        calibration_source: calibSource,
        is_calibrated: isCalib,
      });
      setZoom(1);
      setPan({ x: 0, y: 0 });
      setActiveRightTab('form');
      setLoadingCalib(false);
    };

    setupForm();
  }, [show, taskId, currentFrameIndex, taskData, surveys]);

  // Load frame-level analysis if exists
  useEffect(() => {
    if (!show || !taskId || currentFrameIndex === null) return;
    
    // Check if taskData already has it
    const targetF = taskData.best_frames?.[currentFrameIndex];
    if (!targetF) return;
    if (targetF.frame_analysis && targetF.frame_analysis_version === 6) {
      setFrameAnalysis(targetF.frame_analysis);
      return;
    }

    // Otherwise, try to query from cached database
    setFrameAnalysis(null);
    crackAPI.analyzeFrame(taskId, targetF.frame_index, false, currentFrameIndex)
      .then(res => {
        if (res.data?.data?.analysis) {
          setFrameAnalysis(res.data.data.analysis);
        }
      })
      .catch(() => { /* No cached frame analysis yet */ });
  }, [show, taskId, currentFrameIndex, taskData]);

  if (!show || !taskData) return null;

  const targetFrame = currentFrameIndex !== null ? taskData.best_frames?.[currentFrameIndex] : taskData.best_frames?.[0];
  const imageUrl = targetFrame?.frameFilePath 
    ? withAccessToken(`/api/v1/files/${targetFrame.frameFilePath.replace(/\\/g, '/').replace(/^(\/)?api\/v1\/files\//, '').replace(/^\//, '')}`)
    : '';

  const handleAnalyzeFrame = async () => {
    if (currentFrameIndex === null) return;
    const targetF = taskData.best_frames?.[currentFrameIndex];
    if (!targetF) return;
    const detections = targetF.detections || [];
    if (detections.length === 0) return;
    
    setIsAnalyzingFrame(true);
    try {
      const res = await crackAPI.analyzeFrame(taskId, targetF.frame_index, true, currentFrameIndex);
      if (res.data?.status && res.data.data?.analysis) {
        const holisticAnalysis = res.data.data.analysis;
        setFrameAnalysis(holisticAnalysis);
        
        // Update taskData in parent state so it persists
        const updatedTask = {
          ...taskData,
          best_frames: taskData.best_frames.map((f: any, idx: number) => 
            idx === currentFrameIndex 
              ? { ...f, frame_analysis: holisticAnalysis, frame_analysis_version: 6 }
              : f
          )
        };
        onApproved(updatedTask, true);
        alert("🎉 Đã hoàn thành phân tích tổng thể hình ảnh bằng Vision AI!");
      } else {
        alert("Không nhận được dữ liệu phân tích từ Vision AI: " + (res.data?.error || "Không rõ nguyên nhân"));
      }
    } catch (e: any) {
      console.error(e);
      alert("Lỗi khi kết nối đến máy chủ AI để phân tích: " + (e.response?.data?.detail || e.message));
    } finally {
      setIsAnalyzingFrame(false);
    }
  };

  const handleFinalApprove = async () => {
    if (!details.survey_id) {
      alert('Phải chọn đúng đợt khảo sát trước khi lưu.');
      return;
    }
    if (!Number.isFinite(details.lat) || !Number.isFinite(details.lng)) {
      alert('Thiếu tọa độ thật. Hãy chọn vị trí trên bản đồ; hệ thống không tự điền tọa độ mẫu.');
      return;
    }
    if (!details.tcvn_grade) {
      alert('Phải đánh giá hạng TCVN trước khi lưu.');
      return;
    }
    setIsSavingDecision(true);
    try {
      if (currentFrameIndex !== null) {
        // Frame-level approval
        await archiveAPI.postAction('/snapshot/action', {
          task_id: taskId,
          frame_index: currentFrameIndex,
          batch_result_index: targetFrame?._batch_result_index,
          status: 'approved',
          metadata: details
        });
        
        const updatedTask = { ...taskData };
        if (updatedTask.best_frames && updatedTask.best_frames[currentFrameIndex]) {
          updatedTask.best_frames[currentFrameIndex] = {
            ...updatedTask.best_frames[currentFrameIndex],
            status: 'approved',
            metadata: details
          };
        }
        onApproved(updatedTask, true);
      } else {
        // Legacy full task-level approval
        await archiveAPI.approveDetailed(taskId, details);
        const updatedTask = { ...taskData, status: 'approved', approval_status: 'approved' };
        onApproved(updatedTask, false);
      }
      onClose();
    } catch (e: any) {
      alert(`Lỗi khi phê duyệt ảnh: ${e.response?.data?.detail || e.message || 'Không rõ nguyên nhân'}`);
    } finally {
      setIsSavingDecision(false);
    }
  };

  const handleReject = async () => {
    const targetLabel = currentFrameIndex !== null
      ? `Ảnh ${(targetFrame?._batch_result_index ?? currentFrameIndex) + 1}`
      : 'tác vụ này';
    if (!confirm(`Từ chối ${targetLabel}? Kết quả này sẽ không được đưa vào GIS và Báo cáo Kỹ thuật TCVN.`)) {
      return;
    }

    setIsSavingDecision(true);
    try {
      if (currentFrameIndex !== null) {
        await archiveAPI.postAction('/snapshot/action', {
          task_id: taskId,
          frame_index: currentFrameIndex,
          batch_result_index: targetFrame?._batch_result_index,
          status: 'rejected'
        });

        const updatedTask = { ...taskData };
        if (updatedTask.best_frames?.[currentFrameIndex]) {
          updatedTask.best_frames = [...updatedTask.best_frames];
          updatedTask.best_frames[currentFrameIndex] = {
            ...updatedTask.best_frames[currentFrameIndex],
            status: 'rejected'
          };
        }
        onApproved(updatedTask, true);
      } else {
        await archiveAPI.approve(taskId, 'rejected');
        onApproved({ ...taskData, approval_status: 'rejected' }, false);
      }
      onClose();
    } catch (e: any) {
      alert(`Lỗi khi từ chối ảnh: ${e.response?.data?.detail || e.message || 'Không rõ nguyên nhân'}`);
    } finally {
      setIsSavingDecision(false);
    }
  };

  // Zoom & Pan functions
  const handleWheel = (e: React.WheelEvent) => {
    const delta = e.deltaY < 0 ? 0.1 : -0.1;
    setZoom(prev => Math.min(Math.max(prev + delta, 0.5), 5));
  };

  const handleMouseDown = (e: React.MouseEvent) => {
    if (e.button === 2) { // Right Click
      setIsPanning(true);
      setStartPan({ x: e.clientX - pan.x, y: e.clientY - pan.y });
    }
  };

  const handleMouseMove = (e: React.MouseEvent) => {
    if (isPanning) {
      setPan({ x: e.clientX - startPan.x, y: e.clientY - startPan.y });
    }
  };

  const handleMouseUp = () => {
    setIsPanning(false);
  };

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center p-6 bg-slate-900/90 backdrop-blur-xl animate-fade-in">
       <div className="bg-white w-[96vw] h-[94vh] max-w-[1700px] rounded-[2rem] shadow-2xl overflow-hidden flex flex-col border border-white/20">
          
          {/* Modal Header */}
          <div className="px-8 py-5 border-b border-slate-100 flex items-center justify-between bg-white z-10 shrink-0">
             <div className="flex items-center gap-4">
                <div className="w-10 h-10 rounded-xl bg-blue-50 flex items-center justify-center text-blue-600">
                   <Bot className="w-6 h-6" />
                </div>
                <div>
                    <h2 className="text-md font-bold text-slate-800">
                       {currentFrameIndex !== null ? `Kiểm định Ảnh ${currentFrameIndex + 1}` : 'Phê duyệt Nhiệm vụ'}
                    </h2>
                </div>

                {/* Frame Navigation Controls */}
                {taskData?.best_frames && taskData.best_frames.length > 1 && (
                   <div className="flex items-center gap-2 ml-6 bg-slate-50 border border-slate-200/60 p-1.5 rounded-xl">
                     <button
                        type="button"
                        disabled={currentFrameIndex === 0}
                        onClick={() => setCurrentFrameIndex(prev => prev - 1)}
                        className="p-1.5 rounded-lg bg-white border border-slate-200 hover:bg-slate-100 text-slate-600 disabled:opacity-30 disabled:pointer-events-none transition-all active:scale-90"
                        title="Ảnh trước"
                     >
                        <ChevronLeft className="w-4 h-4" />
                     </button>
                     <span className="text-xs font-bold text-slate-600 min-w-[3.5rem] text-center select-none">
                        {currentFrameIndex + 1} / {taskData.best_frames.length}
                     </span>
                     <button
                        type="button"
                        disabled={currentFrameIndex === taskData.best_frames.length - 1}
                        onClick={() => setCurrentFrameIndex(prev => prev + 1)}
                        className="p-1.5 rounded-lg bg-white border border-slate-200 hover:bg-slate-100 text-slate-600 disabled:opacity-30 disabled:pointer-events-none transition-all active:scale-90"
                        title="Ảnh sau"
                     >
                        <ChevronRight className="w-4 h-4" />
                     </button>
                   </div>
                 )}
             </div>
             <button onClick={onClose} className="p-2 hover:bg-slate-100 rounded-full transition-colors">
                <X className="w-5 h-5 text-slate-400 hover:text-red-500" />
             </button>
          </div>

          {/* Modal Content */}
          <div className="flex-1 flex flex-row overflow-hidden min-h-0 w-full">
             
             {/* 1. Left: Photo List Sidebar (Danh sách ảnh khảo sát - Nền sáng) */}
             <div className="w-64 bg-slate-50 border-r border-slate-200 flex flex-col shrink-0 text-slate-700 select-none">
                {/* Defect Status Filter Tabs */}
                <div className="p-2 bg-white border-b border-slate-200 flex flex-col gap-1.5 shrink-0">
                   <div className="grid grid-cols-3 gap-1 bg-slate-100 p-0.5 rounded-lg text-[9px] font-bold">
                      <button
                         type="button"
                         onClick={() => { setDefectFilter('all'); setListPage(1); }}
                         className={`py-1 rounded-md transition-all ${defectFilter === 'all' ? 'bg-white text-blue-600 shadow-sm font-extrabold' : 'text-slate-500 hover:text-slate-800'}`}
                      >
                         Tất cả ({allFrames.length})
                      </button>
                      <button
                         type="button"
                         onClick={() => { setDefectFilter('defects'); setListPage(1); }}
                         className={`py-1 rounded-md transition-all ${defectFilter === 'defects' ? 'bg-rose-500 text-white shadow-sm font-extrabold' : 'text-slate-500 hover:text-slate-800'}`}
                      >
                         Có lỗi ({allFrames.filter((f: any) => f.detections?.length > 0).length})
                      </button>
                      <button
                         type="button"
                         onClick={() => { setDefectFilter('clean'); setListPage(1); }}
                         className={`py-1 rounded-md transition-all ${defectFilter === 'clean' ? 'bg-emerald-600 text-white shadow-sm font-extrabold' : 'text-slate-500 hover:text-slate-800'}`}
                      >
                         Sạch ({allFrames.filter((f: any) => !f.detections || f.detections.length === 0).length})
                      </button>
                   </div>

                   {/* Page Size Selector & Pagination */}
                   <div className="flex items-center justify-between gap-1 text-[9px] pt-0.5">
                      <div className="flex items-center gap-1 font-semibold text-slate-500">
                         <span>Xem:</span>
                         <select
                            value={pageSize}
                            onChange={(e) => { setPageSize(Number(e.target.value)); setListPage(1); }}
                            className="bg-slate-100 border border-slate-200 rounded px-1.5 py-0.5 font-bold text-slate-700 outline-none cursor-pointer hover:bg-slate-200 transition-colors"
                         >
                            <option value={50}>50 / trang</option>
                            <option value={100}>100 / trang</option>
                            <option value={200}>200 / trang</option>
                            <option value={500}>500 / trang</option>
                         </select>
                      </div>

                      {totalListPages > 1 && (
                         <div className="flex items-center gap-1">
                            <button
                               type="button"
                               disabled={listPage <= 1}
                               onClick={() => setListPage(p => p - 1)}
                               className="p-0.5 rounded border border-slate-200 bg-white hover:bg-slate-100 disabled:opacity-30 text-slate-600"
                            >
                               <ChevronLeft className="w-3 h-3" />
                            </button>
                            <span className="font-bold text-slate-700">{listPage}/{totalListPages}</span>
                            <button
                               type="button"
                               disabled={listPage >= totalListPages}
                               onClick={() => setListPage(p => p + 1)}
                               className="p-0.5 rounded border border-slate-200 bg-white hover:bg-slate-100 disabled:opacity-30 text-slate-600"
                            >
                               <ChevronRight className="w-3 h-3" />
                            </button>
                         </div>
                      )}
                   </div>
                </div>

                {/* Table List Header */}
                <div className="flex items-center justify-between bg-slate-100/70 border-b border-slate-200 text-[10px] font-bold text-slate-500 uppercase py-2 px-3 tracking-wider shrink-0">
                   <span>Ảnh #{ (listPage - 1) * pageSize + 1 } – { Math.min(listPage * pageSize, filteredFrames.length) }</span>
                   <span>Sự cố</span>
                </div>

                {/* Table List */}
                <div className="flex-1 overflow-y-auto custom-scrollbar divide-y divide-slate-150">
                   {paginatedFrames.length === 0 ? (
                      <div className="p-4 text-center text-xs text-slate-400 font-medium">Không có ảnh phù hợp</div>
                   ) : paginatedFrames.map((frame: any) => {
                      const idx = frame.originalIndex;
                      const isSelected = currentFrameIndex === idx;
                      const photoName = frame.filename 
                        ? frame.filename.replace(/\.[^/.]+$/, "") 
                        : `Ảnh #${idx + 1}`;
                        
                      const detCount = frame.detections?.length || 0;

                      return (
                        <div
                           key={idx}
                           onClick={() => setCurrentFrameIndex(idx)}
                           className={`flex items-center justify-between py-2.5 px-3 text-[11px] font-mono cursor-pointer transition-colors ${
                              isSelected 
                                ? 'bg-blue-600 text-white font-bold shadow-sm' 
                                : 'hover:bg-slate-100 text-slate-700 font-medium'
                           }`}
                        >
                           <div className="truncate flex items-center gap-1.5 pr-2" title={photoName}>
                              <span className={`text-[9px] font-bold w-5 ${isSelected ? 'text-blue-100' : 'text-slate-400'}`}>#{idx + 1}</span>
                              <span className="truncate">{photoName}</span>
                           </div>
                           {detCount > 0 ? (
                             <span className={`px-1.5 py-0.5 rounded font-bold text-[9px] border whitespace-nowrap ${isSelected ? 'bg-red-500 text-white border-red-400' : 'bg-red-50 text-red-600 border-red-200'}`}>
                               {detCount} lỗi
                             </span>
                           ) : (
                             <span className={`text-[9px] font-semibold ${isSelected ? 'text-blue-100' : 'text-emerald-600'}`}>Sạch</span>
                           )}
                        </div>
                      );
                   })}
                </div>
             </div>

             {/* 2. Center: Image Viewer & Bottom Path Bar */}
             <div className="flex-1 bg-slate-100/90 flex flex-col relative overflow-hidden min-w-0 border-r border-slate-200">
                <div 
                   className="flex-1 flex items-center justify-center relative overflow-hidden cursor-crosshair"
                   onWheel={handleWheel}
                   onMouseDown={handleMouseDown}
                   onMouseMove={handleMouseMove}
                   onMouseUp={handleMouseUp}
                   onMouseLeave={handleMouseUp}
                   onContextMenu={(e) => e.preventDefault()}
                >
                <div 
                    className={`relative ${isPanning ? '' : 'transition-all duration-300 ease-out'} inline-block`}
                    style={{ 
                       transform: `scale(${zoom}) translate(${pan.x}px, ${pan.y}px)`,
                       transformOrigin: 'center center'
                    }}
                >
                    {imageUrl ? (
                      <img 
                         src={imageUrl}
                         className="max-h-[72vh] w-auto block select-none pointer-events-none p-4 rounded-lg shadow-sm"
                         alt="Auditing Preview"
                         onLoad={(e) => {
                            const w = e.currentTarget.naturalWidth || 1920;
                            const h = e.currentTarget.naturalHeight || 1080;
                            setNaturalSize({
                               width: w,
                               height: h
                            });
                         }}
                      />
                    ) : (
                      <div className="w-96 h-96 flex items-center justify-center bg-slate-200 text-slate-500 rounded-xl border border-slate-300">
                        <Loader2 className="w-8 h-8 animate-spin text-blue-600" />
                      </div>
                    )}
                    
                    {/* SVG BBox/Polygon Overlay & HTML Labels */}
                    {showBBox && targetFrame?.detections?.map((box: any, bIdx: number) => {
                          if (!box || !box.bbox || !Array.isArray(box.bbox) || box.bbox.length < 4) return null;
                          const isNormalized = Math.max(...box.bbox) <= 1.05;
                           const width = naturalSize.width || 1920;
                           const height = naturalSize.height || 1080;
                           const bbox = isNormalized ? box.bbox : [box.bbox[0]/width, box.bbox[1]/height, box.bbox[2]/width, box.bbox[3]/height];
                           const rawPolygon = Array.isArray(box.polygon)
                              ? box.polygon.filter((p: unknown) => Array.isArray(p) && p.length >= 2)
                              : [];
                           const polygonIsNormalized = rawPolygon.length > 0
                              && Math.max(...rawPolygon.flatMap((p: number[]) => [Number(p[0]), Number(p[1])])) <= 1.05;
                           const polygon = rawPolygon.length > 0
                              ? (polygonIsNormalized
                                  ? rawPolygon
                                  : rawPolygon.map((p: number[]) => [Number(p[0])/width, Number(p[1])/height]))
                              : null;
                          const isNearTop = bbox[1] < 0.05;
                          
                          const isHighlighted = highlightedTrackId === (box.track_id || bIdx.toString());
                          
                          return (
                             <React.Fragment key={bIdx}>
                               <svg viewBox="0 0 1 1" preserveAspectRatio="none" className="absolute inset-0 w-full h-full pointer-events-none p-4">
                                  {polygon && Array.isArray(polygon) && polygon.length > 0 ? (
                                     <polygon
                                        points={polygon.map((p: number[]) => `${p[0]},${p[1]}`).join(' ')}
                                        className={isHighlighted ? "stroke-red-500 fill-red-500/15 animate-pulse" : "stroke-emerald-400 fill-emerald-400/10"}
                                        style={{ strokeWidth: isHighlighted ? 3 : 2 }}
                                        vectorEffect="non-scaling-stroke"
                                     />
                                  ) : (
                                     bbox && bbox.length === 4 && (
                                        <rect
                                           x={bbox[0]}
                                           y={bbox[1]}
                                           width={bbox[2] - bbox[0]}
                                           height={bbox[3] - bbox[1]}
                                           className={isHighlighted ? "stroke-red-500 fill-red-500/15 animate-pulse" : "stroke-emerald-400 fill-emerald-400/10"}
                                           style={{ strokeWidth: isHighlighted ? 3 : 2 }}
                                           vectorEffect="non-scaling-stroke"
                                        />
                                     )
                                  )}
                               </svg>
                               
                               {/* HTML label overlay */}
                               {bbox && bbox.length === 4 && (
                                 <div className="absolute inset-0 w-full h-full pointer-events-none p-4">
                                   <div className="relative w-full h-full">
                                     <div 
                                        className={`absolute text-white text-[9px] px-1.5 py-0.5 font-bold rounded-sm uppercase tracking-tighter whitespace-nowrap shadow-md pointer-events-none z-10 transition-all ${
                                           isHighlighted ? 'bg-red-600 scale-110 ring-2 ring-red-350' : 'bg-emerald-600'
                                        }`}
                                        style={{
                                           left: `${bbox[0] * 100}%`,
                                           top: `${bbox[1] * 100}%`,
                                           transform: isNearTop ? 'none' : 'translateY(-100%)',
                                        }}
                                     >
                                        {translateAIClass(box.class)} {box.confidence !== undefined ? `${Math.round(box.confidence <= 1 ? box.confidence * 100 : box.confidence)}%` : ''}
                                     </div>
                                   </div>
                                 </div>
                               )}
                             </React.Fragment>
                          );
                    })}
                </div>
                
                {/* Floating controls */}
                <div className="absolute bottom-6 left-6 flex gap-2">
                   <button 
                      onClick={() => { setZoom(1); setPan({ x: 0, y: 0 }); }}
                      className="px-3 py-1.5 rounded-lg bg-white/90 text-slate-700 text-[10px] font-bold border border-slate-300 hover:bg-white shadow-sm transition-colors"
                   >
                      Reset View
                   </button>
                   <button 
                      onClick={() => setShowBBox(!showBBox)}
                      className={`px-3 py-1.5 rounded-lg text-[10px] font-bold border transition-colors ${showBBox ? 'bg-emerald-600 text-white border-emerald-500 shadow-sm' : 'bg-white/90 text-slate-700 border-slate-300'}`}
                   >
                      {showBBox ? 'Tắt BBox' : 'Bật BBox'}
                   </button>
                </div>
                {loadingCalib && (
                   <div className="absolute inset-0 bg-white/60 backdrop-blur-sm flex items-center justify-center text-slate-800 gap-2 font-bold text-xs">
                     <div className="w-5 h-5 rounded-full border-2 border-blue-600 border-t-transparent animate-spin" />
                     Đang tính toán GSD Calibration...
                   </div>
                )}
             </div>

             {/* Bottom Information Status Bar */}
             <div className="bg-slate-50 border-t border-slate-200 px-4 py-2 flex items-center justify-between text-[11px] font-mono text-slate-600 shrink-0">
               <div className="truncate text-slate-700 flex items-center gap-2">
                 <span className="truncate" title={targetFrame?.frameFilePath || ''}>
                   {targetFrame?.frameFilePath || `D:\\survey_data\\${taskData?.survey_name || 'survey'}\\${targetFrame?.filename || 'P' + String(currentFrameIndex).padStart(5, '0') + '.jpg'}`}
                 </span>
                 <span className="text-slate-300">—</span>
                 <span className="text-amber-600 font-bold whitespace-nowrap">
                   {targetFrame?.detections?.length || 0} vùng khuyết tật
                 </span>
               </div>
               <div className="flex items-center gap-3 shrink-0">
                 <span className="text-slate-500 font-bold">Zoom: {Math.round(zoom * 100)}%</span>
               </div>
             </div>
          </div>

          {/* Right: Business Form & AI Analysis Tabs */}
          <div className="w-[440px] xl:w-[480px] bg-slate-50 flex flex-col shrink-0 h-full min-h-0 border-l border-slate-200">
                {/* Right Column Tabs */}
                <div className="flex bg-slate-100 p-1 rounded-xl gap-1 border border-slate-200/50 shrink-0 mb-4">
                  <button
                    type="button"
                    onClick={() => setActiveRightTab('form')}
                    className={`flex-1 py-1.5 rounded-lg text-xs font-bold text-center transition-all ${activeRightTab === 'form' ? 'bg-white text-blue-600 shadow-sm' : 'text-slate-400 hover:text-slate-650'}`}
                  >
                    Thông tin duyệt
                  </button>
                  <button
                    type="button"
                    onClick={() => setActiveRightTab('ai')}
                    className={`flex-1 py-1.5 rounded-lg text-xs font-bold text-center transition-all flex items-center justify-center gap-1 transition-all ${activeRightTab === 'ai' ? 'bg-white text-blue-600 shadow-sm' : 'text-slate-400 hover:text-slate-650'}`}
                  >
                    <Bot className="w-3.5 h-3.5" />
                    Phân tích AI
                  </button>
                </div>

                <div className="flex-1 min-h-0 overflow-y-auto space-y-4 pr-1 -mr-1 custom-scrollbar text-slate-800">
                  {activeRightTab === 'form' && (
                    <div className="space-y-4 animate-fade-in pb-4">
                      <div className="space-y-1">
                         <label className="text-[9px] font-black text-slate-400 uppercase tracking-wider">Tiêu đề sự cố</label>
                         <input type="text" value={details.title}
                            onChange={(e) => setDetails({ ...details, title: e.target.value })}
                            className="w-full px-3 py-2 rounded-xl border border-slate-200 focus:border-blue-500 focus:outline-none text-xs font-semibold text-slate-700 bg-white" />
                      </div>
                      
                      <div className="grid grid-cols-2 gap-3">
                          <div className="space-y-1">
                              <label className="text-[9px] font-black text-slate-400 uppercase tracking-wider">Loại hạ tầng</label>
                              <select value={details.asset_type}
                                  onChange={(e) => setDetails({ ...details, asset_type: e.target.value })}
                                  className="w-full px-2 py-2 rounded-xl border border-slate-200 text-xs font-semibold bg-white">
                                  <option value="road">Đường bộ</option>
                                  <option value="bridge">Công trình Cầu</option>
                              </select>
                          </div>
                          <div className="space-y-1">
                              <label className="text-[9px] font-black text-slate-400 uppercase tracking-wider">Mức độ nguy hiểm</label>
                              <select value={details.severity}
                                  onChange={(e) => setDetails({ ...details, severity: e.target.value })}
                                  className="w-full px-2 py-2 rounded-xl border border-slate-200 text-xs font-semibold bg-white text-slate-700">
                                  <option value="critical">🔴 Nguy hiểm</option>
                                  <option value="warning">🟡 Cảnh báo</option>
                                  <option value="info">🔵 Thông tin</option>
                              </select>
                          </div>
                      </div>

                      <div className="space-y-1">
                         <label className="text-[9px] font-black text-slate-400 uppercase tracking-wider flex items-center justify-between">
                           Đánh giá chất lượng TCVN 8866
                           {details.tcvn_grade_auto && (
                             <span className="text-blue-500 font-medium normal-case italic text-[10px]">
                               AI đề xuất: Hạng {details.tcvn_grade_auto}
                             </span>
                           )}
                         </label>
                         <div className="flex gap-1">
                            {(['A','B','C','D','E'] as const).map(g => (
                               <button 
                                 key={g} 
                                 type="button"
                                 onClick={() => setDetails({...details, tcvn_grade: g})}
                                 className={`flex-1 py-1.5 rounded-lg text-[10px] font-bold border transition-all ${details.tcvn_grade === g ? 'bg-blue-50 border-blue-400 text-blue-700 font-extrabold shadow-sm' : 'bg-white border-slate-200 text-slate-400 hover:bg-slate-50'}`}
                               >
                                 {g}
                               </button>
                            ))}
                         </div>
                      </div>

                      <div className="grid grid-cols-2 gap-2">
                         <div className="space-y-1">
                            <label className="text-[9px] font-black text-slate-400 uppercase tracking-wider">Tuyến đường</label>
                            <input value={details.route_name} onChange={e => setDetails({...details, route_name: e.target.value})}
                              className="w-full px-2 py-2 rounded-xl border border-slate-200 text-xs font-semibold bg-white" placeholder="QL1A" />
                         </div>
                         <div className="space-y-1">
                            <label className="text-[9px] font-black text-slate-400 uppercase tracking-wider">Lý trình (Km)</label>
                            <input type="number" step="0.001" value={details.route_km ?? ''} onChange={e => {
                              const value = e.target.value === '' ? null : Number(e.target.value);
                              setDetails({...details, route_km: Number.isFinite(value) ? value : null});
                            }}
                              className="w-full px-2 py-2 rounded-xl border border-slate-200 text-xs font-semibold bg-white" placeholder="Chưa có dữ liệu" />
                         </div>
                      </div>

                      <div className="space-y-1 mt-2">
                         <label className="text-[9px] font-black text-slate-400 uppercase tracking-wider">Làn đường (Có thể chọn nhiều)</label>
                         <div className="flex flex-wrap gap-1">
                            {([
                              { value: 'left', label: 'Trái (Left)' },
                              { value: 'center', label: 'Giữa (Center)' },
                              { value: 'right', label: 'Phải (Right)' },
                              { value: 'shoulder', label: 'Lề (Shoulder)' }
                            ] as const).map(lane => {
                               const selectedLanes = details.lane_position ? details.lane_position.split(',') : [];
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
                                        setDetails({
                                           ...details,
                                           lane_position: updated.join(',')
                                        });
                                     }}
                                     className={`px-2 py-1.5 text-[10px] font-bold rounded-lg border transition-all ${
                                        isSelected 
                                          ? 'bg-blue-50 border-blue-300 text-blue-700 ring-1 ring-blue-100' 
                                          : 'bg-white border-slate-200 text-slate-500 hover:bg-slate-50'
                                     }`}
                                  >
                                     {lane.label}
                                  </button>
                               );
                            })}
                         </div>
                      </div>

                      {details.is_calibrated ? (
                          <div className="px-2.5 py-1.5 bg-emerald-50 border border-emerald-100 rounded-lg flex items-start gap-1.5 text-[9px] leading-tight text-emerald-800">
                            <CheckCircle2 className="w-3.5 h-3.5 text-emerald-500 shrink-0 mt-0.5" />
                            <span>Kích thước đã được tự động hiệu chuẩn (GSD).</span>
                          </div>
                      ) : (
                          <div className="px-2.5 py-1.5 bg-amber-50 border border-amber-100 rounded-lg flex items-start gap-1.5 text-[9px] leading-tight text-amber-800">
                            <AlertCircle className="w-3.5 h-3.5 text-amber-500 shrink-0 mt-0.5" />
                            <span>Chưa hiệu chuẩn tự động. Nhập kích thước thủ công.</span>
                          </div>
                      )}

                      <div className="grid grid-cols-2 gap-3">
                         <div className="space-y-1">
                            <label className="text-[9px] font-black text-slate-400 uppercase tracking-wider">Diện tích (m²)</label>
                            <input type="number" step="0.01" value={details.damage_area_m2 ?? ''} 
                              onChange={e => {
                                const v = e.target.value === '' ? null : Number(e.target.value);
                                setDetails({...details, damage_area_m2: Number.isFinite(v) ? v : null});
                              }} 
                              className={`w-full px-2 py-2 rounded-xl border text-xs font-semibold bg-white ${details.is_calibrated ? 'border-emerald-200 bg-emerald-50/50' : 'border-slate-200'}`} placeholder="Chưa đo" />
                         </div>
                         <div className="space-y-1">
                            <label className="text-[9px] font-black text-slate-400 uppercase tracking-wider">Bề rộng nứt (mm)</label>
                            <input type="number" step="0.1" value={details.damage_width_mm ?? ''} 
                              onChange={e => {
                                const v = e.target.value === '' ? null : Number(e.target.value);
                                setDetails({...details, damage_width_mm: Number.isFinite(v) ? v : null});
                              }} 
                              className={`w-full px-2 py-2 rounded-xl border text-xs font-semibold bg-white ${details.is_calibrated ? 'border-emerald-200 bg-emerald-50/50' : 'border-slate-200'}`} placeholder="Chưa đo" />
                         </div>
                      </div>

                      <div className="space-y-1">
                         <label className="text-[9px] font-black text-slate-400 uppercase tracking-wider">Khuyến nghị Sửa chữa</label>
                         <input value={details.repair_method} onChange={e => setDetails({...details, repair_method: e.target.value})}
                           className="w-full px-2 py-2 rounded-xl border border-slate-200 text-xs font-semibold text-blue-600 bg-white" />
                      </div>

                      <div className="space-y-1">
                          <label className="text-[9px] font-black text-slate-400 uppercase tracking-wider">Mô tả chi tiết</label>
                          <textarea value={details.description}
                              onChange={(e) => setDetails({ ...details, description: e.target.value })}
                              className="w-full px-3 py-2 rounded-xl border border-slate-200 text-xs h-14 resize-none bg-white" placeholder="Mô tả sự cố..." />
                      </div>

                      <div className="space-y-1.5">
                          <label className="text-[9px] font-black text-slate-400 uppercase tracking-wider flex items-center justify-between">
                             Vị trí Tọa độ (Map)
                             <span className="text-blue-500 lowercase text-[9px] font-medium italic">Nhấp bản đồ để lấy tọa độ</span>
                          </label>
                          <div className="h-36 w-full rounded-xl overflow-hidden border border-slate-200 relative z-0">
                             <MiniMapPicker lat={details.lat} lng={details.lng}
                                onLocationSelect={async (lat, lng) => {
                                   setDetails((prev: any) => ({ ...prev, lat, lng }));
                                   try {
                                      const addr = await fetchReverseGeocode(lat, lng, details.route_name, details.route_km);
                                      setDetails((prev: any) => ({ ...prev, lat, lng, address: addr }));
                                   } catch (e) {
                                      console.error(e);
                                   }
                                }} />
                          </div>
                          <div className="flex gap-2">
                             <input value={details.address} onChange={e => setDetails({...details, address: e.target.value})}
                               className="flex-1 px-2.5 py-1.5 rounded-xl border border-slate-200 text-[10px] font-semibold text-slate-600 bg-white" placeholder="Địa chỉ sự cố" />
                             <span className="text-[9px] text-blue-600 font-mono flex items-center">
                               {Number.isFinite(details.lat) && Number.isFinite(details.lng)
                                 ? `${details.lat.toFixed(4)}, ${details.lng.toFixed(4)}`
                                 : 'Chưa có tọa độ'}
                             </span>
                          </div>
                      </div>
                    </div>
                  )}

                  {activeRightTab === 'ai' && (
                    <div className="h-full flex flex-col min-h-0 space-y-4 animate-fade-in pb-4">
                      {/* Top Header & Re-analyze button */}
                      <div className="flex items-center justify-between pb-3 border-b border-slate-200/60 shrink-0">
                        <h3 className="text-xs font-bold text-slate-700 flex items-center gap-1.5">
                          <Bot className="w-4 h-4 text-violet-600" />
                          Phân Tích Tổng Thể
                        </h3>
                        {targetFrame?.detections && targetFrame.detections.length > 0 && (
                          <button
                            type="button"
                            onClick={handleAnalyzeFrame}
                            disabled={isAnalyzingFrame}
                            className="px-3 py-1.5 text-[11px] font-bold bg-violet-600 hover:bg-violet-750 disabled:bg-violet-400 text-white rounded-xl flex items-center gap-1 transition-all active:scale-95 shadow-md shadow-violet-500/10"
                            title="Chạy phân tích tổng thể toàn bộ ảnh bằng Vision AI"
                          >
                            {isAnalyzingFrame ? (
                              <Loader2 className="w-3 h-3 animate-spin" />
                            ) : (
                              <Bot className="w-3 h-3" />
                            )}
                            {isAnalyzingFrame ? 'Đang phân tích...' : 'Phân Tích Hư Hại'}
                          </button>
                        )}
                      </div>

                      {/* Warning Banner if not yet analyzed by LLM */}
                      {!frameAnalysis && (
                        <div className="p-2.5 bg-amber-50 border border-amber-200 rounded-xl text-amber-800 text-[10px] leading-relaxed flex items-start gap-1.5 shrink-0">
                          <AlertCircle className="w-3.5 h-3.5 text-amber-600 shrink-0 mt-0.5" />
                          <div>
                            <span className="font-bold">Lưu ý:</span> Ảnh này chưa có báo cáo phân tích tổng thể từ Vision AI. Hãy bấm nút <b>Phân Tích Hư Hại</b> để bắt đầu.
                          </div>
                        </div>
                      )}

                      {/* Scrollable content area */}
                      <div className="flex-1 min-h-0 overflow-y-auto space-y-4 pr-1 -mr-1 custom-scrollbar">
                        {frameAnalysis ? (
                          <div className="p-5 bg-white rounded-2xl border border-slate-200/60 shadow-sm space-y-4 text-slate-800">
                            <div className="flex items-center pb-2.5 border-b border-slate-100">
                              <span className="text-[12px] font-extrabold text-slate-800 uppercase tracking-wide flex items-center gap-1.5">
                                <Brain className="w-4 h-4 text-violet-600 animate-pulse" />
                                Báo cáo giám định hình ảnh tổng thể
                              </span>
                            </div>

                            <div className="space-y-3.5 text-xs">
                              {frameAnalysis.observed_object && (
                                <div className="space-y-0.5">
                                  <span className="text-[9px] font-black text-slate-400 uppercase tracking-wider">Đối tượng quan trắc</span>
                                  <p className="font-semibold text-slate-700">{frameAnalysis.observed_object}</p>
                                  {frameAnalysis.observed_context && (
                                    <p className="mt-1 text-[10px] leading-relaxed text-slate-500">Bối cảnh quan sát: {frameAnalysis.observed_context}</p>
                                  )}
                                </div>
                              )}

                              {frameAnalysis.defect_code_mapping && (
                                <div className="space-y-0.5">
                                  <span className="text-[9px] font-black text-slate-400 uppercase tracking-wider">Hệ thống mã khuyết tật</span>
                                  <p className="font-semibold text-violet-700">{frameAnalysis.defect_code_mapping}</p>
                                </div>
                              )}

                              {Array.isArray(frameAnalysis.defect_catalog) && frameAnalysis.defect_catalog.length > 0 && (
                                <div className="space-y-1.5">
                                  <span className="text-[9px] font-black text-slate-400 uppercase tracking-wider">Mã hóa hư hỏng AI (Defect Catalog)</span>
                                  <div className="grid gap-1.5">
                                    {frameAnalysis.defect_catalog.map((item: any, index: number) => {
                                      const minConfidence = Number(item.min_confidence ?? item.confidence ?? 0);
                                      const maxConfidence = Number(item.max_confidence ?? item.confidence ?? 0);
                                      const minPercent = Math.round((minConfidence > 1 ? minConfidence / 100 : minConfidence) * 100);
                                      const maxPercent = Math.round((maxConfidence > 1 ? maxConfidence / 100 : maxConfidence) * 100);
                                      const confidenceLabel = minPercent === maxPercent ? `${maxPercent}%` : `${minPercent}–${maxPercent}%`;
                                      return (
                                        <div key={`${item.code}-${index}`} className="flex items-center justify-between gap-3 rounded-lg border border-violet-100 bg-violet-50/60 px-2.5 py-2">
                                          <div className="min-w-0">
                                            <span className="font-semibold text-slate-700">[{item.code}] {item.name}</span>
                                            {Number(item.count ?? 1) > 1 && (
                                              <span className="ml-1.5 text-[9px] font-bold text-slate-400"> · {item.count} vùng</span>
                                            )}
                                          </div>
                                          <span className="shrink-0 font-bold text-violet-700">{confidenceLabel}</span>
                                        </div>
                                      );
                                    })}
                                  </div>
                                </div>
                              )}

                              {(frameAnalysis.current_status_details || frameAnalysis.description) && (
                                <div className="space-y-0.5">
                                  <span className="text-[9px] font-black text-slate-400 uppercase tracking-wider">Chi tiết hiện trạng</span>
                                  <div className="ai-report-content text-slate-600 leading-relaxed text-[11px] bg-slate-50 p-3 rounded-xl border border-slate-150">
                                    <ReactMarkdown
                                      remarkPlugins={[remarkGfm]}
                                      components={{
                                        h1: ({children}) => <h3 className="font-extrabold text-slate-800 text-xs mt-1 mb-2">{children}</h3>,
                                        h2: ({children}) => <h3 className="font-extrabold text-slate-800 text-xs mt-1 mb-2">{children}</h3>,
                                        h3: ({children}) => <h4 className="font-bold text-violet-700 text-[11px] mt-2 mb-1">{children}</h4>,
                                        p: ({children}) => <p className="mb-2 last:mb-0">{children}</p>,
                                        ul: ({children}) => <ul className="list-disc pl-4 space-y-1 mb-2">{children}</ul>,
                                        ol: ({children}) => <ol className="list-decimal pl-4 space-y-1 mb-2">{children}</ol>,
                                        table: ({children}) => <div className="overflow-x-auto my-2"><table className="min-w-full text-[10px] border-collapse">{children}</table></div>,
                                        th: ({children}) => <th className="text-left font-bold bg-slate-200/70 border border-slate-200 px-2 py-1">{children}</th>,
                                        td: ({children}) => <td className="border border-slate-200 px-2 py-1 align-top">{children}</td>,
                                        strong: ({children}) => <strong className="font-bold text-slate-800">{children}</strong>,
                                      }}
                                    >{frameAnalysis.current_status_details || frameAnalysis.description}</ReactMarkdown>
                                  </div>
                                </div>
                              )}

                              {Array.isArray(frameAnalysis.visual_evidence) && frameAnalysis.visual_evidence.length > 0 && (
                                <div className="space-y-2">
                                  <span className="text-[9px] font-black text-slate-400 uppercase tracking-wider">Bằng chứng thị giác theo hư hỏng</span>
                                  <div className="space-y-2">
                                    {frameAnalysis.visual_evidence.map((evidence: any, i: number) => {
                                      const isSuspected = String(evidence.ai_validation || '').toLowerCase().includes('dương tính giả');
                                      const isUncertain = String(evidence.ai_validation || '').toLowerCase().includes('không đủ');
                                      return (
                                        <div key={`${evidence.defect_class}-${i}`} className="rounded-xl border border-slate-200 bg-white px-3 py-3 shadow-sm">
                                          <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                                            <span className="font-extrabold text-slate-800">{evidence.defect_class}</span>
                                            <span className={`rounded-full px-2 py-0.5 text-[9px] font-black uppercase tracking-wide ${
                                              isSuspected ? 'bg-red-50 text-red-700' : isUncertain ? 'bg-amber-50 text-amber-700' : 'bg-emerald-50 text-emerald-700'
                                            }`}>
                                              {evidence.ai_validation}
                                            </span>
                                          </div>
                                          <div className="grid gap-1.5 text-[10.5px] leading-relaxed text-slate-600">
                                            <p><b className="text-slate-700">Vị trí:</b> {evidence.location}</p>
                                            <p><b className="text-slate-700">Dấu hiệu quan sát:</b> {evidence.visual_characteristics}</p>
                                            <p><b className="text-slate-700">Phạm vi:</b> {evidence.extent}</p>
                                            <p><b className="text-slate-700">Ý nghĩa sàng lọc:</b> {evidence.engineering_significance}</p>
                                          </div>
                                        </div>
                                      );
                                    })}
                                  </div>
                                </div>
                              )}

                              {Array.isArray(frameAnalysis.technical_findings) && frameAnalysis.technical_findings.length > 0 && (
                                <div className="space-y-2">
                                  <span className="text-[9px] font-black text-slate-400 uppercase tracking-wider">Phân tích Kỹ thuật (Dẫn chiếu Tiêu chuẩn)</span>
                                  <div className="space-y-2">
                                    {frameAnalysis.technical_findings.map((finding: any, i: number) => (
                                      <div key={`${finding.standard}-${i}`} className="rounded-xl border border-slate-200 bg-slate-50 px-3 py-2.5">
                                        <div className="mb-1 flex items-center gap-2">
                                          <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-violet-100 text-[10px] font-black text-violet-700">{i + 1}</span>
                                          <span className="font-extrabold text-slate-800">{finding.standard}</span>
                                        </div>
                                        <div className="space-y-1.5 pl-7 text-[11px] leading-relaxed text-slate-600">
                                          {finding.applicable_scope && <p><b className="text-slate-700">Phạm vi áp dụng:</b> {finding.applicable_scope}</p>}
                                          {finding.observed_evidence && <p><b className="text-slate-700">Bằng chứng liên quan:</b> {finding.observed_evidence}</p>}
                                          <p><b className="text-slate-700">Nhận định kỹ thuật:</b> {finding.assessment}</p>
                                          {finding.limitation && <p><b className="text-slate-700">Giới hạn kết luận:</b> {finding.limitation}</p>}
                                        </div>
                                      </div>
                                    ))}
                                  </div>
                                </div>
                              )}

                              {(!Array.isArray(frameAnalysis.technical_findings) || frameAnalysis.technical_findings.length === 0) && ((frameAnalysis.technical_analysis?.tcvn_references?.length ?? 0) > 0 || (frameAnalysis.causes?.length ?? 0) > 0) && (
                                <div className="space-y-0.5">
                                  <span className="text-[9px] font-black text-slate-400 uppercase tracking-wider">Căn cứ pháp lý & tiêu chuẩn kỹ thuật (TCVN)</span>
                                  <ul className="list-disc pl-4 space-y-1 text-slate-655 text-[11px]">
                                    {(frameAnalysis.technical_analysis?.tcvn_references || frameAnalysis.causes || []).map((ref: string, i: number) => (
                                      <li key={i}>{ref}</li>
                                    ))}
                                  </ul>
                                </div>
                              )}

                              {(frameAnalysis.conclusion_details || frameAnalysis.conclusion_and_repair_plan || frameAnalysis.structural_impact) && (
                                <div className="space-y-0.5">
                                  <span className="text-[9px] font-black text-slate-400 uppercase tracking-wider">Kết luận & Phương án xử lý</span>
                                  {frameAnalysis.conclusion_details ? (
                                    <div className="space-y-1.5 text-slate-655 leading-relaxed text-[11px] bg-slate-50 p-3 rounded-xl border border-slate-150">
                                      <p><b className="text-slate-700">Tổng hợp tình trạng:</b> {frameAnalysis.conclusion_details.condition_summary}</p>
                                      <p><b className="text-slate-700">Sàng lọc rủi ro:</b> {frameAnalysis.conclusion_details.risk_screening}</p>
                                      <p><b className="text-slate-700">Nội dung cần xác minh:</b> {frameAnalysis.conclusion_details.required_confirmation}</p>
                                    </div>
                                  ) : (
                                    <p className="text-slate-655 leading-relaxed text-[11px] bg-slate-50 p-3 rounded-xl border border-slate-150">
                                      {frameAnalysis.conclusion_and_repair_plan || frameAnalysis.structural_impact}
                                    </p>
                                  )}
                                </div>
                              )}

                              {((frameAnalysis.recommendations_detailed?.length ?? 0) > 0 || (frameAnalysis.recommendations_to_contractor?.length ?? 0) > 0 || (frameAnalysis.recommended_actions?.length ?? 0) > 0) && (
                                <div className="space-y-0.5">
                                  <span className="text-[9px] font-black text-slate-400 uppercase tracking-wider">Kiến nghị cho đơn vị xây dựng</span>
                                  {Array.isArray(frameAnalysis.recommendations_detailed) && frameAnalysis.recommendations_detailed.length > 0 ? (
                                    <div className="space-y-2">
                                      {frameAnalysis.recommendations_detailed.map((rec: any, i: number) => (
                                        <div key={`${rec.priority}-${i}`} className="rounded-xl border border-blue-100 bg-blue-50/40 px-3 py-2.5 text-[11px] leading-relaxed text-slate-600">
                                          <div className="mb-1 font-extrabold text-blue-700">{rec.priority}</div>
                                          <p><b className="text-slate-700">Hành động:</b> {rec.action}</p>
                                          <p><b className="text-slate-700">Mục đích:</b> {rec.purpose}</p>
                                          <p><b className="text-slate-700">Phương pháp:</b> {rec.method}</p>
                                        </div>
                                      ))}
                                    </div>
                                  ) : (
                                    <ul className="list-disc pl-4 space-y-1 text-slate-655 text-[11px]">
                                      {(frameAnalysis.recommendations_to_contractor || frameAnalysis.recommended_actions || []).map((rec: string, i: number) => (
                                        <li key={i}>{rec}</li>
                                      ))}
                                    </ul>
                                  )}
                                </div>
                              )}
                            </div>
                          </div>
                        ) : (
                          <div className="p-6 text-center bg-white border border-slate-200/60 rounded-2xl text-xs text-slate-400 italic font-medium">
                            Hãy chạy phân tích để tạo báo cáo tổng thể cho ảnh này.
                          </div>
                        )}

                        {/* List of defect locations for highlight and spatial ref */}
                        {targetFrame?.detections && targetFrame.detections.length > 0 ? (
                          <div className="space-y-2">
                            <h4 className="text-[10px] font-black text-slate-400 uppercase tracking-wider pl-1">
                              Vị trí khuyết tật phát hiện ({targetFrame.detections.length})
                            </h4>
                            <div className="grid grid-cols-1 gap-2">
                              {targetFrame.detections.map((det: any, detIdx: number) => {
                                const isHighlighted = highlightedTrackId === (det.track_id || detIdx.toString());
                                const confPct = det.confidence !== undefined ? `${Math.round(det.confidence <= 1 ? det.confidence * 100 : det.confidence)}%` : '';
                                return (
                                  <div
                                    key={detIdx}
                                    onClick={() => setHighlightedTrackId(prev => prev === (det.track_id || detIdx.toString()) ? null : (det.track_id || detIdx.toString()))}
                                    className={`px-4 py-2.5 bg-white rounded-xl border text-xs cursor-pointer flex items-center justify-between transition-all hover:border-blue-400 ${
                                      isHighlighted 
                                        ? 'border-blue-500 ring-2 ring-blue-50 bg-blue-50/10' 
                                        : 'border-slate-200/60'
                                    }`}
                                  >
                                    <div className="flex flex-col gap-0.5">
                                      <span className="font-bold text-slate-700">
                                        Hư hại {detIdx + 1}: {translateAIClass(det.class)}
                                      </span>
                                      <span className="text-[10px] text-slate-400 font-medium">
                                        Độ tin cậy: {confPct}
                                      </span>
                                    </div>
                                    <div className="text-right">
                                      <span className="text-[10px] bg-slate-100 text-slate-600 px-2 py-0.5 rounded-md font-mono font-bold">
                                        ID: {det.track_id}
                                      </span>
                                    </div>
                                  </div>
                                );
                              })}
                            </div>
                          </div>
                        ) : (
                          <div className="text-xs text-slate-400 italic font-medium p-6 text-center bg-white border border-slate-100 rounded-2xl">
                            Không phát hiện khuyết tật nào trong ảnh.
                          </div>
                        )}
                      </div>
                    </div>
                  )}
                </div>
             </div>
          </div>

          {/* Modal Footer */}
          <div className="px-6 py-3.5 border-t border-slate-100 flex flex-col sm:flex-row sm:items-center justify-between gap-3 bg-slate-50">
             <div className="text-[10px] font-semibold text-slate-500">
                {targetFrame?.status === 'approved' ? (
                  <span className="inline-flex items-center gap-1.5 text-emerald-600">
                    <CheckCircle2 className="w-3.5 h-3.5" /> Ảnh đã duyệt — duyệt lại sẽ cập nhật bản ghi hiện có
                  </span>
                ) : targetFrame?.status === 'rejected' ? (
                  <span className="inline-flex items-center gap-1.5 text-red-600">
                    <XCircle className="w-3.5 h-3.5" /> Ảnh đang bị từ chối và không xuất hiện trong báo cáo
                  </span>
                ) : (
                  <span>Chỉ ảnh được duyệt mới được đưa vào GIS và Báo cáo Kỹ thuật TCVN.</span>
                )}
             </div>
             <div className="flex items-center justify-end gap-2 shrink-0">
               <button 
                  onClick={onClose}
                  disabled={isSavingDecision}
                  className="px-4 py-2 text-[10px] font-bold text-slate-500 hover:text-slate-800 uppercase tracking-wider transition-colors disabled:opacity-50"
               >
                  Đóng
               </button>
               <button
                  onClick={handleReject}
                  disabled={isSavingDecision}
                  className="px-5 py-2 rounded-xl border border-red-200 bg-white hover:bg-red-50 text-red-600 text-[10px] font-bold uppercase tracking-wider active:scale-95 transition-all disabled:opacity-50 inline-flex items-center gap-1.5"
               >
                  {isSavingDecision ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <XCircle className="w-3.5 h-3.5" />}
                  Từ chối ảnh
               </button>
               <button 
                  onClick={handleFinalApprove}
                  disabled={isSavingDecision}
                  className="px-6 py-2 rounded-xl bg-emerald-600 hover:bg-emerald-700 text-white text-[10px] font-bold uppercase tracking-wider shadow-lg shadow-emerald-500/20 active:scale-95 transition-all disabled:opacity-50 inline-flex items-center gap-1.5"
               >
                  {isSavingDecision ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <CheckCircle2 className="w-3.5 h-3.5" />}
                  Duyệt ảnh &amp; đưa vào báo cáo
               </button>
             </div>
           </div>
        </div>
     </div>
  );
}
