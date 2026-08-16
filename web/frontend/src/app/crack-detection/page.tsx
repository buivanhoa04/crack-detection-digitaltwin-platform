'use client';

import { useState, useCallback, useEffect, useRef, useMemo, forwardRef, useImperativeHandle } from 'react';
import {
  Upload, Camera, Image as ImageIcon, Video, X, Search, Play, Pause, ArrowLeft,
  Maximize2, Wifi, WifiOff, Clock, CheckCircle2, AlertCircle, Loader2, PlusCircle,
  ChevronRight, ChevronLeft, BarChart3, Settings2, Database, MapPin, Trash2, History, RefreshCcw, Cpu, Route, ClipboardList, Edit2, FolderOpen
} from 'lucide-react';
import { useDropzone } from 'react-dropzone';
import { incidentsAPI, crackAPI, surveysAPI, calibrationAPI } from '@/lib/api';
import { useWebSocket } from '@/hooks/useWebSocket';
import { useCrack } from '@/hooks/useCrack';
import { useAuth } from '@/hooks/useAuth';
import { translateAIClass, autoTCVNGrade, TCVN_GRADES, REPAIR_STATUSES, suggestRepairMethod, type TCVNGrade } from '@/lib/translate';
import { withAccessToken } from '@/lib/mediaAuth';
import dynamic from 'next/dynamic';
import RealtimeTelemetryHUD from '@/components/RealtimeTelemetryHUD';
import { Box } from 'lucide-react';

const MiniMapPicker = dynamic(() => import('@/components/map/MiniMapPicker'), { ssr: false, loading: () => <div className="h-40 bg-slate-100 animate-pulse rounded-xl" /> });
const ModelViewer = dynamic(() => import('@/components/3d/ModelViewer'), { ssr: false, loading: () => <div className="h-full bg-slate-900 flex items-center justify-center text-white font-mono text-xs"><Loader2 className="w-8 h-8 animate-spin mr-3"/> Khởi tạo 3D Engine...</div> });

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

export default function CrackDetectionPage() {
  const { user } = useAuth();
  const [activeTab, setActiveTab] = useState<'image' | 'video' | 'stream' | 'local'>('image');
  const [sidebarTab, setSidebarTab] = useState<'upload'>('upload');
  const [searchHistoryQuery, setSearchHistoryQuery] = useState('');
  const [modelType, setModelType] = useState<'road' | 'bridge'>('road');
  const [uploadedFiles, setUploadedFiles] = useState<File[]>([]);
  const [isUploading, setIsUploading] = useState(false);
  const [localPath, setLocalPath] = useState('');
  const [isImporting, setIsImporting] = useState(false);
  const [generate3D, setGenerate3D] = useState(false);
  const [segmentationEnabled, setSegmentationEnabled] = useState(true);
  const [colorNormalizationEnabled, setColorNormalizationEnabled] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const [taskSurveyFilter, setTaskSurveyFilter] = useState('all');
  const [taskRouteFilter, setTaskRouteFilter] = useState('all');
  const [taskSurveyorFilter, setTaskSurveyorFilter] = useState('all');
  const [taskStatusFilter, setTaskStatusFilter] = useState('all');

  // Auto detect image vs video format on upload
  useEffect(() => {
    if (activeTab !== 'stream' && activeTab !== 'local') {
      if (uploadedFiles.length === 0) {
        setActiveTab('image');
      } else {
        const firstFile = uploadedFiles[0];
        const isVideo = firstFile.name.toLowerCase().match(/\.(mp4|avi|mov|mkv)$/);
        setActiveTab(isVideo ? 'video' : 'image');
      }
    }
  }, [uploadedFiles, activeTab]);

  // v2.0: Survey/Campaign State
  const [surveys, setSurveys] = useState<any[]>([]);
  const [selectedSurveyId, setSelectedSurveyId] = useState<string>('');
  const [showSurveyModal, setShowSurveyModal] = useState(false);
  const [isEditingSurvey, setIsEditingSurvey] = useState(false);
  const [newSurvey, setNewSurvey] = useState<any>({
    name: '',
    route_name: '',
    route_km_start: null,
    route_km_end: null,
    surveyor: '',
    method: 'vehicle',
    notes: '',
    num_lanes: 2,
    has_emergency_lane: false
  });

  useEffect(() => {
    surveysAPI.getAll().then(({ data }) => setSurveys(data.surveys || [])).catch(() => {});
  }, []);

  // Use Global State
  const { 
    tasks, 
    selectedResult, 
    setSelectedResult, 
    startDetection, 
    deleteTask,
    retryTask,
    isPolling 
  } = useCrack();

  const taskFilterOptions = useMemo(() => {
    const surveyCounts = new Map<string, number>();
    const routeCounts = new Map<string, number>();
    const surveyorCounts = new Map<string, number>();
    const statusCounts = new Map<string, number>();

    tasks.forEach(task => {
      const surveyId = task.survey_id ? String(task.survey_id) : '__unassigned';
      const survey = task.survey_id ? surveys.find((s: any) => String(s.id) === String(task.survey_id)) : null;
      const route = survey?.route_name?.trim();
      const surveyor = survey?.surveyor?.trim();

      surveyCounts.set(surveyId, (surveyCounts.get(surveyId) || 0) + 1);
      if (route) routeCounts.set(route, (routeCounts.get(route) || 0) + 1);
      if (surveyor) surveyorCounts.set(surveyor, (surveyorCounts.get(surveyor) || 0) + 1);
      if (task.status) statusCounts.set(task.status, (statusCounts.get(task.status) || 0) + 1);
    });

    const statusLabels: Record<string, string> = {
      pending: 'Đang chờ',
      queued: 'Đang xếp hàng',
      processing: 'Đang phân tích',
      done: 'Hoàn thành',
      completed: 'Hoàn thành',
      error: 'Có lỗi',
      cancelled: 'Đã hủy'
    };

    return {
      surveys: Array.from(surveyCounts.entries()).map(([value, count]) => {
        if (value === '__unassigned') return { value, label: 'Không gắn đợt', count };
        const survey = surveys.find((s: any) => String(s.id) === value);
        return { value, label: survey?.name || `Đợt ${value}`, count };
      }).sort((a, b) => a.label.localeCompare(b.label, 'vi')),
      routes: Array.from(routeCounts.entries()).map(([value, count]) => ({ value, label: value, count })).sort((a, b) => a.label.localeCompare(b.label, 'vi')),
      surveyors: Array.from(surveyorCounts.entries()).map(([value, count]) => ({ value, label: value, count })).sort((a, b) => a.label.localeCompare(b.label, 'vi')),
      statuses: Array.from(statusCounts.entries()).map(([value, count]) => ({ value, label: statusLabels[value] || value, count }))
    };
  }, [tasks, surveys]);

  const filteredTasks = useMemo(() => {
    return tasks.filter(task => {
      const surveyId = task.survey_id ? String(task.survey_id) : '__unassigned';
      const survey = task.survey_id ? surveys.find((s: any) => String(s.id) === String(task.survey_id)) : null;
      const q = searchQuery.trim().toLowerCase();
      const matchesSearch = !q || (
        task.filename?.toLowerCase().includes(q) ||
        task.task_id?.toLowerCase().includes(q) ||
        (survey?.name && survey.name.toLowerCase().includes(q)) ||
        (survey?.route_name && survey.route_name.toLowerCase().includes(q)) ||
        (survey?.surveyor && survey.surveyor.toLowerCase().includes(q))
      );
      return (
        matchesSearch &&
        (taskSurveyFilter === 'all' || surveyId === taskSurveyFilter) &&
        (taskRouteFilter === 'all' || survey?.route_name === taskRouteFilter) &&
        (taskSurveyorFilter === 'all' || survey?.surveyor === taskSurveyorFilter) &&
        (taskStatusFilter === 'all' || task.status === taskStatusFilter)
      );
    });
  }, [tasks, searchQuery, surveys, taskSurveyFilter, taskRouteFilter, taskSurveyorFilter, taskStatusFilter]);

  const activeTaskFilterCount = [
    taskSurveyFilter,
    taskRouteFilter,
    taskSurveyorFilter,
    taskStatusFilter
  ].filter(value => value !== 'all').length + (searchQuery.trim() ? 1 : 0);

  const clearTaskFilters = () => {
    setSearchQuery('');
    setTaskSurveyFilter('all');
    setTaskRouteFilter('all');
    setTaskSurveyorFilter('all');
    setTaskStatusFilter('all');
  };

  const getCrackCount = (task: any) => {
    if (task.total_detections !== undefined && task.total_detections !== null) return task.total_detections;
    if (!task.best_frames) return 0;
    return task.best_frames.reduce((acc: number, f: any) => acc + (f.detections?.length || 0), 0);
  };

  // Local Selection & Tracking (v34.0 Professional)
  const [trackingData, setTrackingData] = useState<any[]>([]);
  const [currentFrameDetections, setCurrentFrameDetections] = useState<any[]>([]);
  const [activeKeyframeIndex, setActiveKeyframeIndex] = useState<number | null>(null);
  const [currentRoadContour, setCurrentRoadContour] = useState<any[]>([]);
  const [isSidebarCollapsed, setIsSidebarCollapsed] = useState(false);
  const [batchResultItems, setBatchResultItems] = useState<any[]>([]);
  const [batchResultPage, setBatchResultPage] = useState(1);
  const [batchResultTotal, setBatchResultTotal] = useState(0);
  const [batchResultsLoading, setBatchResultsLoading] = useState(false);
  const batchResultPageSize = 100;
  const videoRef = useRef<HTMLVideoElement>(null);
  const viewerRef = useRef<any>(null);

  const [videoFPS, setVideoFPS] = useState(30); // Default fallback
  const [globalShowRoadMask, setGlobalShowRoadMask] = useState(true);
  const [globalShowAILayer, setGlobalShowAILayer] = useState(true);

  const loadBatchResultPage = useCallback(async (page: number) => {
    if (!selectedResult?.task_id) return;
    setBatchResultsLoading(true);
    try {
      const { data } = await crackAPI.getBatchResults(
        selectedResult.task_id,
        page,
        batchResultPageSize,
        true
      );
      setBatchResultItems(data.items || []);
      setBatchResultTotal(data.total || 0);
      setBatchResultPage(data.page || page);
    } catch (error) {
      console.error('Failed to load batch results', error);
    } finally {
      setBatchResultsLoading(false);
    }
  }, [selectedResult]);

  useEffect(() => {
    setBatchResultItems([]);
    setBatchResultTotal(0);
    setBatchResultPage(1);
    const isVideoTask = selectedResult?.filename?.toLowerCase().match(/\.(mp4|avi|mov)$/);
    if (selectedResult?.status === 'done' && selectedResult?.task_id && !isVideoTask) {
      loadBatchResultPage(1);
    }
  }, [selectedResult?.task_id, selectedResult?.status, loadBatchResultPage]);

  // Handle redirect from Digital Twin ArchiveBrowser
  useEffect(() => {
    if (typeof window !== 'undefined') {
      const redirectTaskStr = localStorage.getItem('redirect_task');
      if (redirectTaskStr) {
        try {
          const task = JSON.parse(redirectTaskStr);
          setSelectedResult(task);
          setActiveTab(task.filename?.toLowerCase().match(/\.(mp4|avi|mov)$/) ? 'video' : 'image');
        } catch (e) {
          console.error('Failed to parse redirect_task', e);
        }
        localStorage.removeItem('redirect_task');
      }
    }
  }, [setSelectedResult]);

  // Auto-select logic: When a task is clicked, automatically select its first result
  useEffect(() => {
    if (selectedResult && selectedResult.status === 'done') {
        const frames = selectedResult.best_frames || [];
        if (frames.length > 0 && !selectedResult.frameFilePath) {
            setSelectedResult({ ...selectedResult, ...frames[0] });
        }
        
        // Load tracking for video
        if (selectedResult.filename?.toLowerCase().match(/\.(mp4|avi|mov)$/)) {
            let trackUrl = selectedResult.trackingDataUrl || (selectedResult.results?.trackingDataUrl);
            
            // v72.7: Fallback for older tasks that were completed before trackingDataUrl was added to DB
            if (!trackUrl && selectedResult.task_id) {
                trackUrl = `/crack/tracking/${selectedResult.task_id}`;
            }
            
            if (trackUrl) {
                // Remove any leading /api to avoid double /api/api
                const cleanPath = trackUrl.replace(/^\/api/, '');
                
                // If we don't have a specific API URL, use the Next.js proxy /api
                // BUT if it's exactly /api, we use http://localhost:8081/api to bypass proxy issues
                const rawApiUrl = process.env.NEXT_PUBLIC_API_URL;
                const apiBase = (!rawApiUrl || rawApiUrl === '/api') ? '/api' : rawApiUrl;
                
                const finalUrl = cleanPath.startsWith('/crack/tracking') 
                    ? `${apiBase}${cleanPath}`
                    : (trackUrl.startsWith('/api/') || trackUrl.startsWith('http')) ? trackUrl : `${apiBase}/crack/proxy-file?path=${encodeURIComponent(trackUrl)}`;

                fetch(withAccessToken(finalUrl))
                    .then(res => res.json())
                    .then(data => {
                        // v72.7: Dynamic FPS from AI Metadata
                        if (data.fps) {
                            setVideoFPS(data.fps);
                        }

                        let frames_array = [];
                        let road_contours_obj = data.road_contours || {};
                        if (Array.isArray(data)) {
                            frames_array = data;
                        } else if (data.frames) {
                            frames_array = Object.entries(data.frames).map(([idx, dets]) => ({
                                frame_index: parseInt(idx, 10),
                                detections: dets,
                                road_contour: road_contours_obj[idx] || null
                            }));
                        }
                        setTrackingData(frames_array);
                    })
                    .catch(err => console.error("Tracking load error", err));
            }
        }
    }
  }, [selectedResult]);

  // Sync detections for Video at 60FPS using requestAnimationFrame
  useEffect(() => {
    const video = videoRef.current;
    if (!video || trackingData.length === 0) return;
    
    // v52.0 Fix: Optimize lookup to O(1) using a Map for smooth 30fps iteration
    const trackingMap = new Map();
    const roadContourMap = new Map();
    trackingData.forEach(f => {
        trackingMap.set(f.frame_index, f.detections);
        if (f.road_contour) {
            roadContourMap.set(f.frame_index, f.road_contour);
        }
    });

    let animationFrameId: number;

    const syncFrames = () => {
        if (!video) return;
        const frameIdx = Math.floor(video.currentTime * videoFPS); // Use calibrated FPS
        
        // Find nearest frame (handles AI processing at lower FPS e.g. 5fps vs 30fps video)
        let nearestFrame = -1;
        let minDiff = Infinity;
        for (const f of Array.from(trackingMap.keys())) {
            const diff = Math.abs(f - frameIdx);
            if (diff < minDiff) {
                minDiff = diff;
                nearestFrame = f;
            }
        }
        
        // Show if within 1 second (videoFPS frames)
        let detections = [];
        let roadContour = null;
        if (nearestFrame !== -1 && minDiff <= videoFPS) {
            detections = trackingMap.get(nearestFrame);
            roadContour = roadContourMap.get(nearestFrame);
        }

        setCurrentFrameDetections(prev => {
             // Deep compare to prevent infinite re-renders
             if (!prev && !detections) return prev;
             if (prev && detections && prev.length === detections.length && JSON.stringify(prev) === JSON.stringify(detections)) return prev;
             return detections || [];
        });

        setCurrentRoadContour(roadContour || []);

        // Find nearest keyframe to highlight in list
        let nearestKeyframe = null;
        let minKeyframeDiff = Infinity;
        const bestFrames = selectedResult?.best_frames || [];
        for (const f of bestFrames) {
            const diff = Math.abs(f.frame_index - frameIdx);
            if (diff < minKeyframeDiff) {
                minKeyframeDiff = diff;
                nearestKeyframe = f.frame_index;
            }
        }

        if (nearestKeyframe !== null && minKeyframeDiff <= videoFPS * 1.5) {
            setActiveKeyframeIndex(nearestKeyframe);
        } else {
            setActiveKeyframeIndex(null);
        }

        if (!video.paused && !video.ended) {
            animationFrameId = requestAnimationFrame(syncFrames);
        }
    };

    video.addEventListener('play', () => { animationFrameId = requestAnimationFrame(syncFrames); });
    video.addEventListener('pause', () => cancelAnimationFrame(animationFrameId));
    video.addEventListener('seeked', syncFrames);

    // Initial sync
    syncFrames();

    return () => {
        video.removeEventListener('play', syncFrames);
        video.removeEventListener('pause', syncFrames);
        video.removeEventListener('seeked', syncFrames);
        cancelAnimationFrame(animationFrameId);
    };
  }, [trackingData, selectedResult, videoFPS]);

  // Live Stream State
  const [rtspUrl, setRtspUrl] = useState('');
  
  // Verification Modal State (v2.0 — Extended with business fields)
  const [showVerifyModal, setShowVerifyModal] = useState(false);
  const [verifyForm, setVerifyForm] = useState<any>({
    title: '', severity: 'warning', description: '',
    lat: null, lng: null, address: '',
    route_name: '', route_km: null, lane_position: '',
    tcvn_grade: '' as string, tcvn_grade_auto: '' as string,
    damage_area_m2: null, damage_width_mm: null,
    repair_method: '', classification: '', confidence: 0,
    // v3.0: Calibration
    gsd_mm_per_pixel: null, calibration_source: '', is_calibrated: false,
  });
  const [isConfirming, setIsConfirming] = useState(false);

  const onDrop = useCallback((acceptedFiles: File[]) => {
    setUploadedFiles(prev => [...prev, ...acceptedFiles]);
  }, []);

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    accept: {
      'image/*': ['.jpg', '.jpeg', '.png', '.bmp'],
      'video/*': ['.mp4', '.avi', '.mov'],
    },
    // v4.5: Removed maxSize limit to allow massive survey videos (50GB+)
  });

  const handleStartDetection = async () => {
    if (uploadedFiles.length === 0 || !modelType) {
        if (!modelType) alert("Vui lòng chọn loại mô hình AI (Cầu hoặc Đường) trước!");
        return;
    }
    setIsUploading(true);
    await startDetection(
      uploadedFiles,
      modelType,
      generate3D,
      selectedSurveyId || undefined,
      segmentationEnabled,
      modelType === 'road' ? colorNormalizationEnabled : false
    );
    setUploadedFiles([]);
    setIsUploading(false);
    setSidebarTab('upload');
    // Removed auto-switching to history/archive
  };

  const handleLocalImport = async () => {
    if (!localPath.trim() || !modelType) {
        if (!modelType) alert("Vui lòng chọn loại mô hình AI trước khi nạp file!");
        return;
    }
    setIsImporting(true);
    try {
      const res = await crackAPI.importLocal(
        localPath,
        modelType,
        selectedSurveyId || undefined,
        segmentationEnabled,
        modelType === 'road' ? colorNormalizationEnabled : false
      );
      if (res.data?.task_id) {
        setLocalPath('');
        // task will be picked up by useCrack polling
        setSidebarTab('upload');
        // Removed auto-switching to history/archive
        alert("Đã nhận lệnh nạp file cục bộ. AI đang bắt đầu xử lý...");
      }
    } catch (e: any) {
      alert("Lỗi nạp file: " + (e.response?.data?.detail || e.message));
    } finally {
      setIsImporting(false);
    }
  };

  // WebSocket for Live
  const wsUrl = `${typeof window !== 'undefined' ? window.location.protocol === 'https:' ? 'wss' : 'ws' : 'ws'}://${typeof window !== 'undefined' ? window.location.host : 'localhost'}/api/crack/ws/stream?stream_url=${rtspUrl}&model_type=${modelType}`;
  const { isConnected: wsConnected, lastMessage, connect: wsConnect, disconnect: wsDisconnect } = useWebSocket({ url: wsUrl, autoConnect: false });

  const statusConfig: Record<string, any> = {
    queued: { icon: Clock, label: 'Đang xếp hàng', class: 'text-blue-600 bg-blue-50 border-blue-100' },
    transferring: { icon: Upload, label: 'Đang tải lên', class: 'text-blue-500 bg-blue-50 border-blue-100 animate-pulse' },
    processing: { icon: Loader2, label: 'Đang phân tích', class: 'text-amber-600 bg-amber-50 border-amber-100 animate-pulse' },
    done: { icon: CheckCircle2, label: 'Hoàn tất', class: 'text-emerald-600 bg-emerald-50 border-emerald-100' },
    error: { icon: AlertCircle, label: 'Thất bại', class: 'text-red-600 bg-red-50 border-red-100' },
  };

  // v2.0: Auto-calculate TCVN Grade when opening verify modal
  const handleOpenVerify = async () => {
    if (!selectedResult) return;
    const frame = selectedResult.detections?.length
      ? selectedResult
      : selectedResult.best_frames?.[0];
    if (!frame) return;
    const classification = frame.detections?.[0]?.class || '';
    const confidence = frame.detections?.[0]?.confidence || 0;
    const vnClass = translateAIClass(classification);
    const activeSurvey = surveys.find((s: any) => s.id === selectedSurveyId);

    // v3.0: Auto GSD Calibration
    let damageArea = 0;
    let damageWidth = 0;
    let isCalib = false;
    let gsdUsed = 0;
    let calibSource = '';

    if (frame.detections && frame.detections.length > 0) {
      try {
        const calibRes = await calibrationAPI.processFrame(
          frame.detections,
          frame.frameFilePath || selectedResult.frameFilePath || '',
          0,
          activeSurvey?.num_lanes || 2,
          !!activeSurvey?.has_emergency_lane
        );
        if (calibRes.data?.status && calibRes.data.data?.is_calibrated) {
           const calibData = calibRes.data.data;
           isCalib = true;
           gsdUsed = calibData.gsd_mm_per_pixel;
           calibSource = calibData.calibration_source;
           
           const mainDamage = calibData.damages.find((d: any) => d.class_name === classification);
           if (mainDamage) {
             damageArea = mainDamage.real_area_m2;
             damageWidth = mainDamage.real_width_mm;
           }
        }
      } catch (e) {
        console.warn("[GSD] Calibration failed", e);
      }
    }

    const autoGrade = isCalib ? autoTCVNGrade(classification, confidence, damageArea, damageWidth, true) : '';
    const rawLat = frame?.lat ?? selectedResult?.lat ?? selectedResult?.best_frames?.[0]?.lat;
    const rawLng = frame?.lng ?? selectedResult?.lng ?? selectedResult?.best_frames?.[0]?.lng;
    const initialLat = Number.isFinite(Number(rawLat)) ? Number(rawLat) : null;
    const initialLng = Number.isFinite(Number(rawLng)) ? Number(rawLng) : null;
    const initialRouteName = activeSurvey?.route_name || '';
    const initialRouteKm = Number.isFinite(Number(activeSurvey?.route_km_start)) ? Number(activeSurvey.route_km_start) : null;

    let initialAddress = '';
    if (initialLat !== null && initialLng !== null) {
      try {
        initialAddress = await fetchReverseGeocode(initialLat, initialLng, initialRouteName, initialRouteKm ?? undefined);
      } catch (e) {
        console.warn("Failed to get initial geocode", e);
      }
    }

    setVerifyForm({
      title: `Phát hiện: ${vnClass} (${selectedResult.filename})`,
      severity: autoGrade === 'E' || autoGrade === 'D' ? 'critical' : 'warning',
      description: `Hệ thống AI tự động phát hiện hư hỏng từ tệp: ${selectedResult.filename}`,
      lat: initialLat,
      lng: initialLng,
      address: initialAddress,
      route_name: initialRouteName,
      route_km: initialRouteKm,
      lane_position: '',
      tcvn_grade: autoGrade,
      tcvn_grade_auto: autoGrade,
      damage_area_m2: isCalib ? damageArea : null,
      damage_width_mm: isCalib ? damageWidth : null,
      repair_method: suggestRepairMethod(classification),
      classification,
      confidence,
      gsd_mm_per_pixel: isCalib ? gsdUsed : null,
      calibration_source: calibSource,
      is_calibrated: isCalib,
    });
    setShowVerifyModal(true);
  };

  const handleVerify = async () => {
    if (!selectedResult) return;
    const activeFrame = selectedResult.detections?.length
      ? selectedResult
      : selectedResult.best_frames?.[0];
    if (!activeFrame) return;
    if (!selectedSurveyId) {
      alert('Phải chọn đúng đợt khảo sát trước khi đăng sự cố.');
      return;
    }
    if (!Number.isFinite(verifyForm.lat) || !Number.isFinite(verifyForm.lng)) {
      alert('Thiếu tọa độ thật. Hãy chọn vị trí trên bản đồ.');
      return;
    }
    if (!verifyForm.tcvn_grade) {
      alert('Phải đánh giá hạng TCVN trước khi đăng sự cố.');
      return;
    }
    setIsConfirming(true);
    try {
       const frame = activeFrame;
       const imageUrl = frame.frameFilePath;
       const classification = frame.detections?.[0]?.class || '';
       const confidence = frame.detections?.[0]?.confidence || 0;
       const detections = frame.detections || [];

       await incidentsAPI.create({
           title: verifyForm.title,
           description: verifyForm.description,
           severity: verifyForm.severity,
           lat: verifyForm.lat,
           lng: verifyForm.lng,
           address: verifyForm.address,
           images: [imageUrl],
           asset_type: selectedResult.model_type || 'road',
           classification,
           confidence: confidence <= 1 ? confidence * 100 : confidence,
           detections,
           // v2.0: Business fields
           route_name: verifyForm.route_name,
           route_km: verifyForm.route_km,
           lane_position: verifyForm.lane_position,
           tcvn_grade: verifyForm.tcvn_grade,
           tcvn_grade_auto: verifyForm.tcvn_grade_auto,
           survey_id: selectedSurveyId || undefined,
           damage_area_m2: verifyForm.damage_area_m2,
           damage_width_mm: verifyForm.damage_width_mm,
           repair_method: verifyForm.repair_method,
           repair_status: 'detected',
       });
       alert('🎉 Đã đẩy sự cố lên bản đồ giám sát trung tâm!');
       setShowVerifyModal(false);
    } catch(e) {
       alert('Lỗi kết nối Server.');
    } finally {
       setIsConfirming(false);
    }
  };

  // v2.0: Create / Update survey
  const handleSaveSurvey = async () => {
    if (
      !newSurvey.name?.trim() ||
      !newSurvey.route_name?.trim() ||
      !Number.isFinite(newSurvey.route_km_start) ||
      !Number.isFinite(newSurvey.route_km_end) ||
      newSurvey.route_km_end <= newSurvey.route_km_start
    ) {
      alert('Phải nhập tên, tuyến và khoảng lý trình khảo sát hợp lệ; hệ thống không tự sinh Km mẫu.');
      return;
    }
    try {
      if (isEditingSurvey && selectedSurveyId) {
        const { data } = await surveysAPI.update(selectedSurveyId, newSurvey);
        setSurveys(prev => prev.map(s => s.id === selectedSurveyId ? data.survey : s));
      } else {
        const { data } = await surveysAPI.create(newSurvey);
        setSurveys(prev => [data.survey, ...prev]);
        setSelectedSurveyId(data.survey.id);
      }
      setShowSurveyModal(false);
      setIsEditingSurvey(false);
      setNewSurvey({ name: '', route_name: '', route_km_start: null, route_km_end: null, surveyor: '', method: 'vehicle', notes: '', num_lanes: 2, has_emergency_lane: false });
    } catch (e) {
      alert('Lỗi lưu đợt khảo sát');
    }
  };

  const handleDeleteSurvey = async () => {
    if (!window.confirm('Bạn có chắc chắn muốn xoá đợt khảo sát này? Toàn bộ dữ liệu liên quan sẽ bị ảnh hưởng.')) return;
    try {
      await surveysAPI.delete(selectedSurveyId);
      setSurveys(prev => prev.filter(s => s.id !== selectedSurveyId));
      setSelectedSurveyId('');
    } catch (e) {
      alert('Lỗi xoá đợt khảo sát');
    }
  };

  const handleEditSurvey = () => {
    const survey = surveys.find(s => s.id === selectedSurveyId);
    if (!survey) return;
    setNewSurvey({
      name: survey.name || '',
      route_name: survey.route_name || '',
      route_km_start: survey.route_km_start ?? null,
      route_km_end: survey.route_km_end ?? null,
      surveyor: survey.surveyor || '',
      method: survey.method || 'vehicle',
      notes: survey.notes || '',
      num_lanes: survey.num_lanes || 2,
      has_emergency_lane: !!survey.has_emergency_lane
    });
    setIsEditingSurvey(true);
    setShowSurveyModal(true);
  };

  return (
    <div className="flex h-[calc(100vh-var(--topbar-height)-3rem)] bg-[#F8FAFC] overflow-hidden font-sans text-slate-900 selection:bg-blue-500/30 w-full">
      {selectedResult || (activeTab === 'stream' && wsConnected) ? (
        // ==========================================
        // VIEW MODE: 75% Viewer / 25% Side Panel
        // ==========================================
        <div className="flex-1 flex h-full overflow-hidden w-full">
            {/* LEFT 75%: DETECTION VIEWER */}
            <div className="flex-[3] relative h-full bg-[#f1f5f9] overflow-hidden flex flex-col">
                {/* VIEWER TOP HEADER TOOLBAR */}
                <div className="px-6 py-4 bg-white border-b border-slate-200/60 flex items-center justify-between shrink-0">
                    <div className="flex items-center gap-3">
                        <button 
                            onClick={() => {
                                if (activeTab === 'stream' && wsConnected) {
                                    wsDisconnect();
                                } else {
                                    setSelectedResult(null);
                                }
                            }}
                            className="flex items-center gap-1.5 text-xs font-black text-slate-600 hover:text-blue-600 uppercase px-3 py-2 rounded-xl bg-slate-100 hover:bg-blue-50 transition-colors border border-slate-200/60 active:scale-95 shadow-sm"
                        >
                            <ArrowLeft className="w-4 h-4" /> Quay lại không gian làm việc
                        </button>
                        <div className="h-5 w-[1px] bg-slate-200" />
                        <div className="min-w-0">
                            <p className="text-xs font-bold text-slate-800 truncate max-w-[300px]" title={selectedResult?.filename || 'Luồng AI Live Stream'}>
                                {selectedResult?.filename || 'Luồng AI Live Stream'}
                            </p>
                        </div>
                    </div>

                    {/* Quick Verify/Approve Button for Completed Tasks */}
                    {selectedResult?.status === 'done' && (
                        <button 
                            onClick={handleOpenVerify}
                            className="px-4 py-2 bg-emerald-500 hover:bg-emerald-600 text-white text-xs font-black rounded-xl shadow-lg shadow-emerald-500/10 transition-all flex items-center gap-1.5 active:scale-95 uppercase tracking-widest"
                        >
                            <CheckCircle2 className="w-4 h-4" /> Kiểm duyệt &amp; Đẩy lên Bản đồ
                        </button>
                    )}

                    {/* Disconnect Live Stream button */}
                    {activeTab === 'stream' && wsConnected && (
                        <button 
                            onClick={wsDisconnect}
                            className="px-4 py-2 bg-red-500 hover:bg-red-600 text-white text-xs font-black rounded-xl shadow-lg shadow-red-500/10 transition-all flex items-center gap-1.5 active:scale-95 uppercase tracking-widest"
                        >
                            <WifiOff className="w-4 h-4" /> Ngắt kết nối Stream
                        </button>
                    )}
                </div>

                {/* VIEWER CONTAINER */}
                <div className="flex-1 relative w-full h-full flex items-center justify-center p-4 bg-slate-100/50">
                    {selectedResult && (selectedResult.status === 'queued' || selectedResult.status === 'processing') ? (
                        // AI Processing Realtime Telemetry HUD Screen
                        <div className="w-[96%] h-[96%] rounded-[2rem] bg-slate-950 flex flex-col items-center justify-center p-6 shadow-2xl relative overflow-hidden">
                            <div className="w-full max-w-3xl">
                                <RealtimeTelemetryHUD 
                                    telemetry={{
                                        task_id: selectedResult.task_id,
                                        filename: selectedResult.filename || selectedResult.survey_name || 'Nhiệm vụ kiểm định AI',
                                        progress: selectedResult.progress || 10,
                                        processingStatus: selectedResult.processingStatus || 'Đang tiến hành nhận diện khuyết tật...',
                                        fps: selectedResult.fps || 0,
                                        eta_seconds: selectedResult.eta_seconds || 0,
                                        elapsed_seconds: selectedResult.elapsed_seconds || 0,
                                        processed_count: selectedResult.processed_count || 0,
                                        total_count: selectedResult.total_count || 0,
                                    }}
                                />
                                <div className="mt-6 flex justify-center">
                                    <button 
                                        onClick={() => setSelectedResult(null)}
                                        className="px-6 py-2.5 bg-slate-800 hover:bg-slate-700 text-slate-300 hover:text-white rounded-xl text-xs font-bold transition-all active:scale-95 border border-slate-700 shadow-md"
                                    >
                                        Quay lại không gian làm việc
                                    </button>
                                </div>
                            </div>
                        </div>
                    ) : (
                        // Standard Viewer Box
                        <div className="relative w-full h-full max-w-[98%] max-h-[98%] rounded-[2rem] overflow-hidden shadow-2xl bg-black border-[12px] border-white group/viewer">
                            {activeTab !== 'stream' && selectedResult ? (
                               <DetectionViewer 
                                   ref={viewerRef}
                                   key={selectedResult.task_id}
                                   src={(() => {
                                       const rawApiUrl = process.env.NEXT_PUBLIC_API_URL;
                                       const apiBase = (!rawApiUrl || rawApiUrl === '/api') ? '/api' : rawApiUrl;
                                       if (selectedResult.filename?.toLowerCase().match(/\.(mp4|avi|mov)$/)) {
                                           return withAccessToken(`${apiBase}/crack/video/${selectedResult.task_id}`);
                                       } else {
                                           const rawPath = selectedResult.frameFilePath || selectedResult.best_frames?.[0]?.frameFilePath || selectedResult.results?.best_frames?.[0]?.frameFilePath || '';
                                           if (rawPath.startsWith('/api/') || rawPath.startsWith('http')) return rawPath;
                                           return withAccessToken(`${apiBase}/crack/proxy-file?path=${encodeURIComponent(rawPath)}`);
                                       }
                                   })()} 
                                   bboxes={selectedResult.filename?.toLowerCase().match(/\.(mp4|avi|mov)$/) ? currentFrameDetections : (selectedResult.detections || selectedResult.best_frames?.[0]?.detections || selectedResult.results?.best_frames?.[0]?.detections || [])}
                                   roadContour={
                                       selectedResult.filename?.toLowerCase().match(/\.(mp4|avi|mov)$/) 
                                           ? currentRoadContour 
                                           : (selectedResult.road_contour || selectedResult.best_frames?.[0]?.road_contour || selectedResult.results?.best_frames?.[0]?.road_contour || [])
                                   }
                                   isVideo={selectedResult.filename?.toLowerCase().match(/\.(mp4|avi|mov)$/)}
                                   videoRef={videoRef}
                                   showRoadMask={globalShowRoadMask}
                                   setShowRoadMask={setGlobalShowRoadMask}
                                   showAILayer={globalShowAILayer}
                                   setShowAILayer={setGlobalShowAILayer}
                               />
                            ) : (
                                wsConnected && lastMessage?.data?.datas?.[0]?.images?.[0] ? (
                                    <DetectionViewer
                                        ref={viewerRef}
                                        key="live-stream-viewer"
                                        src={(() => {
                                            const imgData = lastMessage.data.datas[0].images[0];
                                            const rawApiUrl = process.env.NEXT_PUBLIC_API_URL;
                                            const apiBase = (!rawApiUrl || rawApiUrl === '/api') ? '/api' : rawApiUrl;
                                            const rawPath = imgData.frameFilePath || '';
                                            if (rawPath.startsWith('/api/') || rawPath.startsWith('http')) return rawPath;
                                            return withAccessToken(`${apiBase}/crack/proxy-file?path=${encodeURIComponent(rawPath)}`);
                                        })()}
                                        bboxes={lastMessage.data.datas[0].images[0].detections || []}
                                        roadContour={lastMessage.data.datas[0].images[0].road_contour || []}
                                        isVideo={false}
                                        showRoadMask={globalShowRoadMask}
                                        setShowRoadMask={setGlobalShowRoadMask}
                                        showAILayer={globalShowAILayer}
                                        setShowAILayer={setGlobalShowAILayer}
                                    />
                                ) : (
                                   <div className="w-full h-full flex flex-col items-center justify-center bg-slate-900 gap-6">
                                      <div className="w-24 h-24 rounded-full bg-emerald-500/10 flex items-center justify-center animate-pulse border-2 border-emerald-500/20">
                                          <Wifi className="w-12 h-12 text-emerald-500" />
                                      </div>
                                      <p className="text-white text-[10px] font-black uppercase tracking-[0.4em]">
                                          {wsConnected ? 'Đang nhận luồng AI Stream...' : 'Hệ thống Stream đang sẵn sàng'}
                                      </p>
                                   </div>
                                )
                            )}
                        </div>
                    )}
                </div>
            </div>

            {/* RIGHT 25%: DEFECT CATALOG SIDE PANEL */}
            <div className="flex-1 w-80 shrink-0 border-l border-slate-200/60 bg-white flex flex-col h-full overflow-hidden shadow-sm">
                <div className="p-4 border-b border-slate-100 bg-slate-50/50 shrink-0 flex items-center gap-2">
                    <Database className="w-4.5 h-4.5 text-blue-600" />
                    <h3 className="text-xs font-black uppercase tracking-wider text-slate-800">Danh mục hư hại AI</h3>
                </div>
                
                <div className="flex-1 flex flex-col p-4 space-y-4 overflow-hidden">
                    {/* Metadata & Actions Summary */}
                    {selectedResult && (
                        <div className="p-3 bg-slate-50 rounded-2xl border border-slate-150 space-y-2">
                            <div className="flex justify-between items-center text-[10px] text-slate-400 font-bold uppercase">
                                <span>Mô hình</span>
                                <span className="text-slate-700">{selectedResult.model_type === 'bridge' ? '🏗️ Cầu' : '🚧 Đường bộ'}</span>
                            </div>
                            <div className="flex justify-between items-center text-[10px] text-slate-400 font-bold uppercase">
                                <span>Ngày xử lý</span>
                                <span className="text-slate-700">{selectedResult.created_at ? new Date(selectedResult.created_at).toLocaleDateString('vi-VN') : 'N/A'}</span>
                            </div>
                            {/* Đã loại bỏ Tổng lượt nhận diện AI theo yêu cầu */}
                        </div>
                    )}

                    {/* Defect List for Images */}
                    {selectedResult && !selectedResult.filename?.toLowerCase().match(/\.(mp4|avi|mov)$/) && (
                        <div className="flex-1 flex flex-col min-h-0 space-y-2.5">
                            <h4 className="text-[10px] font-black uppercase tracking-wider text-slate-400">Danh sách vết nứt</h4>
                            {(() => {
                                const dets = selectedResult.detections || selectedResult.best_frames?.[0]?.detections || [];
                                if (dets.length === 0) {
                                    return <div className="text-xs text-slate-400 italic font-medium p-4 text-center bg-slate-50 rounded-xl">Không phát hiện hư hại nào</div>;
                                }
                                return (
                                    <div className="flex-1 overflow-y-auto pr-1 custom-scrollbar space-y-1.5 min-h-0">
                                        {dets.map((b: any, idx: number) => (
                                            <div 
                                                key={idx}
                                                onClick={() => viewerRef.current?.focusOnBBox(b)}
                                                className="p-3 bg-white hover:bg-red-50/30 border border-slate-200/80 hover:border-red-200 rounded-xl cursor-pointer transition-all flex items-center justify-between group active:scale-[0.98]"
                                            >
                                                <div className="flex items-center gap-2">
                                                    <div className="w-2 h-2 rounded-full bg-red-500 shrink-0" />
                                                    <span className="text-xs font-bold text-slate-700 group-hover:text-red-700">{translateAIClass(b.class)}</span>
                                                </div>
                                                <span className="text-[10px] font-black bg-slate-100 text-slate-500 px-1.5 py-0.5 rounded-md group-hover:bg-red-100 group-hover:text-red-600">
                                                    {b.confidence <= 1 ? Math.round(b.confidence * 100) : Math.round(b.confidence)}%
                                                </span>
                                            </div>
                                        ))}
                                    </div>
                                );
                            })()}
                        </div>
                    )}

                    {/* Paginated defect review for very large image folders */}
                    {selectedResult &&
                      selectedResult.status === 'done' &&
                      !selectedResult.filename?.toLowerCase().match(/\.(mp4|avi|mov)$/) &&
                      (batchResultTotal > 0 || batchResultsLoading) && (
                        <div className="flex-1 flex flex-col min-h-0 space-y-2.5 border-t border-slate-100 pt-3.5">
                          <div className="flex items-center justify-between">
                            <h4 className="text-[10px] font-black uppercase tracking-wider text-slate-400">
                              Ảnh cần kiểm duyệt ({batchResultTotal})
                            </h4>
                            <span className="text-[9px] font-bold text-slate-400">
                              Trang {batchResultPage}/{Math.max(1, Math.ceil(batchResultTotal / batchResultPageSize))}
                            </span>
                          </div>

                          <div className="flex-1 overflow-y-auto pr-1 custom-scrollbar space-y-1.5 min-h-0">
                            {batchResultsLoading ? (
                              <div className="p-4 flex items-center justify-center text-xs text-slate-400">
                                <Loader2 className="w-4 h-4 animate-spin mr-2" /> Đang tải kết quả...
                              </div>
                            ) : (
                              batchResultItems.map((frame: any, index: number) => (
                                <button
                                  key={`${frame.id || frame.frameFilePath || index}`}
                                  onClick={() => setSelectedResult({
                                    ...selectedResult,
                                    ...frame,
                                    detections: frame.detections || [],
                                  })}
                                  className="w-full p-3 bg-white hover:bg-blue-50 border border-slate-200/80 hover:border-blue-200 rounded-xl text-left transition-all active:scale-[0.99]"
                                >
                                  <div className="flex items-center justify-between gap-2">
                                    <span className="text-[10px] font-bold text-slate-700 truncate">
                                      {frame.id || `Ảnh ${(batchResultPage - 1) * batchResultPageSize + index + 1}`}
                                    </span>
                                    <span className="shrink-0 text-[9px] font-black px-2 py-0.5 rounded-full bg-red-50 text-red-600 border border-red-100">
                                      {frame.detections?.length || 0} sự cố
                                    </span>
                                  </div>
                                </button>
                              ))
                            )}
                          </div>

                          <div className="grid grid-cols-2 gap-2 shrink-0">
                            <button
                              disabled={batchResultPage <= 1 || batchResultsLoading}
                              onClick={() => loadBatchResultPage(batchResultPage - 1)}
                              className="py-2 rounded-lg border border-slate-200 text-[10px] font-bold disabled:opacity-40"
                            >
                              Trang trước
                            </button>
                            <button
                              disabled={
                                batchResultPage * batchResultPageSize >= batchResultTotal ||
                                batchResultsLoading
                              }
                              onClick={() => loadBatchResultPage(batchResultPage + 1)}
                              className="py-2 rounded-lg border border-slate-200 text-[10px] font-bold disabled:opacity-40"
                            >
                              Trang sau
                            </button>
                          </div>
                        </div>
                      )}

                    {/* Defect List & Keyframes for Videos */}
                    {selectedResult && selectedResult.filename?.toLowerCase().match(/\.(mp4|avi|mov)$/) && (
                        <div className="flex-1 flex flex-col min-h-0 space-y-4">
                            {/* Keyframes list */}
                            <div className="flex-1 flex flex-col min-h-0 space-y-2.5">
                                <h4 className="text-[10px] font-black uppercase tracking-wider text-slate-400">Danh sách tiêu điểm sự cố ({selectedResult.best_frames?.length || 0})</h4>
                                {(() => {
                                    const frames = [...(selectedResult.best_frames || [])].sort((a: any, b: any) => a.frame_index - b.frame_index);
                                    if (frames.length === 0) {
                                        return <div className="text-xs text-slate-400 italic font-medium p-4 text-center bg-slate-50 rounded-xl">Chưa trích xuất khung hình sự cố</div>;
                                    }
                                    return (
                                        <div className="flex-1 overflow-y-auto pr-1 custom-scrollbar space-y-1.5 min-h-0">
                                            {frames.map((f: any, idx: number) => {
                                                const timeInSec = f.frame_index / videoFPS;
                                                const timeStr = `${Math.floor(timeInSec / 60)}:${String(Math.floor(timeInSec % 60)).padStart(2, '0')}`;
                                                const defectCount = f.detections?.length || 0;
                                                const isActive = activeKeyframeIndex === f.frame_index;
                                                
                                                return (
                                                    <div 
                                                        key={idx}
                                                        onClick={() => {
                                                            if (videoRef.current) {
                                                                videoRef.current.currentTime = timeInSec;
                                                                videoRef.current.pause();
                                                            }
                                                        }}
                                                        className={`p-3 border rounded-xl cursor-pointer transition-all flex items-center justify-between group active:scale-[0.98] ${
                                                            isActive 
                                                                ? "bg-blue-600 border-blue-700 text-white shadow-md shadow-blue-100" 
                                                                : "bg-white hover:bg-blue-50 border-slate-200/80 hover:border-blue-200 text-slate-700"
                                                        }`}
                                                    >
                                                        <div className="flex items-center gap-2">
                                                            <Clock className={`w-3.5 h-3.5 ${isActive ? "text-blue-200" : "text-slate-400 group-hover:text-blue-500"}`} />
                                                            <div className="flex flex-col">
                                                                 <span className={`text-xs font-bold ${isActive ? "text-white" : "text-slate-700 group-hover:text-blue-700"}`}>Khung hình {f.frame_index}</span>
                                                                 <span className={`text-[9px] font-bold mt-0.5 ${isActive ? "text-blue-200" : "text-slate-400"}`}>Thời điểm: {timeStr} ({timeInSec.toFixed(1)}s)</span>
                                                            </div>
                                                        </div>
                                                        <span className={`text-[10px] font-black px-2 py-0.5 rounded-full ${
                                                            isActive 
                                                                ? "bg-white text-blue-600 border border-white" 
                                                                : "bg-red-50 text-red-600 border border-red-100"
                                                        }`}>
                                                            {defectCount} sự cố
                                                        </span>
                                                    </div>
                                                );
                                            })}
                                        </div>
                                    );
                                })()}
                            </div>

                            {/* Detections on current frame */}
                            <div className="shrink-0 space-y-2.5 border-t border-slate-100 pt-3.5">
                                <h4 className="text-[10px] font-black uppercase tracking-wider text-slate-400">Chi tiết sự cố tại khung hình ({currentFrameDetections.length})</h4>
                                {currentFrameDetections.length === 0 ? (
                                    <div className="text-[10px] text-slate-400 font-bold p-3 text-center bg-slate-50 rounded-xl italic">Tua đến các khung hình sự cố ở trên để xem</div>
                                ) : (
                                    <div className="space-y-1.5 max-h-[160px] overflow-y-auto pr-1 custom-scrollbar">
                                        {currentFrameDetections.map((b: any, idx: number) => (
                                            <div 
                                                key={idx}
                                                onClick={() => viewerRef.current?.focusOnBBox(b)}
                                                className="p-3 bg-white hover:bg-red-50/30 border border-slate-200/80 hover:border-red-200 rounded-xl cursor-pointer transition-all flex items-center justify-between group active:scale-[0.98]"
                                            >
                                                <div className="flex items-center gap-2">
                                                    <div className="w-2 h-2 rounded-full bg-red-500 shrink-0" />
                                                    <span className="text-xs font-bold text-slate-700 group-hover:text-red-700">{translateAIClass(b.class)}</span>
                                                </div>
                                                <span className="text-[10px] font-black bg-slate-100 text-slate-500 px-1.5 py-0.5 rounded-md group-hover:bg-red-100 group-hover:text-red-600">
                                                    {b.confidence <= 1 ? Math.round(b.confidence * 100) : Math.round(b.confidence)}%
                                                </span>
                                            </div>
                                        ))}
                                    </div>
                                )}
                            </div>
                        </div>
                    )}
                </div>
            </div>
        </div>
      ) : (
        // ==========================================
        // WORKSPACE MODE: Spacious Full-Width Layout
        // ==========================================
        <div className="flex-1 flex flex-col p-6 overflow-y-auto bg-slate-50/50 gap-6 w-full custom-scrollbar">
            {/* PHÂN KHU 1: VÙNG PHÂN TÍCH & TẢI TỆP (ANALYSIS WORKSPACE) */}
            <div className="w-full bg-white border border-slate-200/85 rounded-3xl shadow-sm p-5 space-y-5 shrink-0 transition-shadow hover:shadow-md duration-300">
                {/* Header Row */}
                <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4 pb-4 border-b border-slate-100">
                    <div className="flex items-center gap-2.5">
                        <div className="w-8 h-8 rounded-xl bg-blue-600 flex items-center justify-center shadow-md shadow-blue-600/10">
                            <Cpu className="w-4 h-4 text-white" />
                        </div>
                        <div>
                            <h2 className="text-xs font-black text-slate-800 uppercase tracking-widest">Không gian Phân tích AI</h2>
                            <p className="text-[10px] text-slate-400 font-bold mt-0.5">Cấu hình mô hình và tải lên dữ liệu để phân tích hư hỏng</p>
                        </div>
                    </div>
                    
                    <div className="flex items-center gap-2 self-end sm:self-auto">
                        {isPolling ? (
                            <span className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-[9px] font-black uppercase tracking-wider bg-blue-50 border border-blue-100 text-blue-600 animate-pulse">
                                <Loader2 className="w-3.5 h-3.5 animate-spin" /> AI Server đang xử lý
                            </span>
                        ) : (
                            <span className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-[9px] font-black uppercase tracking-wider bg-emerald-50 border border-emerald-100 text-emerald-600">
                                <span className="w-2.5 h-2.5 rounded-full bg-emerald-500 animate-ping" /> AI Server sẵn sàng
                            </span>
                        )}
                    </div>
                </div>

                {/* Configuration controls */}
                <div className="grid grid-cols-1 md:grid-cols-3 gap-5">
                    {/* 1. Model Selection */}
                    <div className="space-y-2">
                         <label className="text-[9px] font-black text-slate-400 uppercase tracking-widest ml-0.5">1. AI Model</label>
                         <div className="flex items-center gap-2">
                              <button onClick={() => setModelType('road')} className={`flex-1 py-2.5 rounded-xl text-xs font-black border transition-all ${modelType === 'road' ? 'bg-blue-50 border-blue-200 text-blue-600 ring-2 ring-blue-100' : 'bg-white border-slate-200 text-slate-500 hover:bg-slate-50'}`}>🚧 Đường bộ</button>
                              <button onClick={() => setModelType('bridge')} className={`flex-1 py-2.5 rounded-xl text-xs font-black border transition-all ${modelType === 'bridge' ? 'bg-blue-50 border-blue-200 text-blue-600 ring-2 ring-blue-100' : 'bg-white border-slate-200 text-slate-500 hover:bg-slate-50'}`}>🏗️ Công trình Cầu</button>
                         </div>
                    </div>

                     {/* 2. Survey Selection */}
                    <div className="space-y-2">
                         <label className="text-[9px] font-black text-slate-400 uppercase tracking-widest ml-0.5">2. Đợt khảo sát</label>
                         <div className="flex items-center gap-2">
                              <select
                                  value={selectedSurveyId}
                                  onChange={e => setSelectedSurveyId(e.target.value)}
                                  className="flex-1 min-w-0 bg-white border border-slate-200 rounded-xl px-3 py-2.5 text-xs font-bold text-slate-700 focus:ring-2 focus:ring-blue-500/20 focus:outline-none text-ellipsis"
                              >
                                  <option value="">-- Không gắn đợt --</option>
                                  {surveys.map((s: any) => (
                                      <option key={s.id} value={s.id}>
                                          {s.name} {s.route_name ? `(${s.route_name})` : ''}
                                      </option>
                                  ))}
                              </select>
                              <button
                                  onClick={() => { setIsEditingSurvey(false); setNewSurvey({ name: '', route_name: '', route_km_start: null, route_km_end: null, surveyor: '', method: 'vehicle', notes: '', num_lanes: 2, has_emergency_lane: false }); setShowSurveyModal(true); }}
                                  className="p-2.5 shrink-0 bg-blue-50 text-blue-600 rounded-xl hover:bg-blue-100 transition-colors border border-blue-100"
                                  title="Tạo đợt khảo sát mới"
                              >
                                  <PlusCircle className="w-4.5 h-4.5" />
                              </button>
                              {selectedSurveyId && (
                                  <>
                                       <button onClick={handleEditSurvey} className="p-2.5 shrink-0 bg-amber-50 text-amber-600 rounded-xl hover:bg-amber-100 transition-colors border border-amber-100" title="Sửa đợt">
                                          <Edit2 className="w-4.5 h-4.5" />
                                      </button>
                                      <button onClick={handleDeleteSurvey} className="p-2.5 shrink-0 bg-red-50 text-red-650 rounded-xl hover:bg-red-100 transition-colors border border-red-100" title="Xoá đợt">
                                          <Trash2 className="w-4.5 h-4.5" />
                                      </button>
                                  </>
                              )}
                         </div>
                    </div>

                    {/* 3. Input Mode Selection */}
                    <div className="space-y-2">
                         <label className="text-[9px] font-black text-slate-400 uppercase tracking-widest ml-0.5">3. Chế độ phân tích</label>
                         <div className="flex items-center gap-1.5 p-1 bg-slate-100 rounded-xl border border-slate-200/50 h-[38px]">
                              <button onClick={() => { setActiveTab('image'); setUploadedFiles([]); }} className={`flex-1 flex items-center justify-center gap-1 py-1.5 rounded-lg transition-all ${activeTab !== 'stream' ? 'bg-white text-blue-600 shadow-sm' : 'text-slate-400 hover:text-slate-600'}`}>
                                  <Upload className="w-3.5 h-3.5" />
                                  <span className="text-[9px] font-black uppercase">Tập tin (Ảnh/Video)</span>
                              </button>
                              <button onClick={() => { setActiveTab('stream'); setUploadedFiles([]); }} className={`flex-1 flex items-center justify-center gap-1 py-1.5 rounded-lg transition-all ${activeTab === 'stream' ? 'bg-white text-blue-600 shadow-sm' : 'text-slate-400 hover:text-slate-600'}`}>
                                  <Wifi className="w-3.5 h-3.5" />
                                  <span className="text-[9px] font-black uppercase">Live stream</span>
                              </button>
                         </div>
                    </div>
                </div>

                {/* Media Input Zone */}
                <div className="border border-slate-200 rounded-2xl p-1.5 bg-slate-50/70">
                    {activeTab !== 'stream' ? (
                        <div {...getRootProps()} className="border border-dashed rounded-xl px-4 py-3 transition-all group cursor-pointer bg-white border-slate-300 hover:border-blue-400 hover:bg-blue-50/30">
                            <input {...getInputProps()} />
                            <div className="flex items-center gap-3 min-h-[48px]">
                                 <div className="p-2.5 bg-blue-50 rounded-xl group-hover:bg-blue-100 transition-colors shrink-0">
                                    {activeTab === 'image' ? <ImageIcon className="w-5 h-5 text-blue-600" /> : <Video className="w-5 h-5 text-blue-600" />}
                                 </div>
                                 <div className="min-w-0 flex-1 text-left">
                                     <p className="text-xs font-black text-slate-700">Chọn hoặc kéo thả dữ liệu vào đây</p>
                                     <p className="text-[10px] text-slate-450 font-bold mt-0.5 truncate">Ảnh, video hoặc thư mục lớn trên 500 ảnh</p>
                                 </div>
                                 <span className="hidden sm:inline-flex px-3 py-2 rounded-xl bg-blue-600 text-white text-[9px] font-black uppercase tracking-wider shadow-sm group-hover:bg-blue-500">
                                     Chọn dữ liệu
                                 </span>
                            </div>
                        </div>
                    ) : (
                        <div className="p-6 bg-white rounded-xl border border-slate-150 space-y-4 max-w-xl mx-auto">
                            <div className="flex items-center gap-2 text-slate-800 pb-1.5 border-b border-slate-100">
                                 <Wifi className="w-4.5 h-4.5 text-emerald-500 animate-pulse shrink-0" />
                                 <p className="text-[10px] font-black uppercase tracking-tight text-slate-600">KẾT NỐI CAMERA TRỰC TIẾP (LIVE STREAM)</p>
                            </div>
                            <div className="flex gap-2">
                                <input className="flex-1 bg-slate-50 border border-slate-200 rounded-xl px-4 py-2 text-xs font-mono focus:ring-2 focus:ring-blue-500/20 focus:outline-none transition-all placeholder:text-slate-400" placeholder="RTSP: rtsp://100.86.xxx.xxx/live" value={rtspUrl} onChange={e => setRtspUrl(e.target.value)} />
                                <button onClick={wsConnected ? wsDisconnect : wsConnect} className={`px-5 py-2.5 rounded-xl text-[10px] font-black transition-all ${wsConnected ? 'bg-red-500 text-white' : 'bg-blue-600 text-white hover:bg-blue-500 shadow-md shadow-blue-600/15'}`}>
                                    {wsConnected ? 'NGẮT KẾT NỐI' : 'THIẾT LẬP AI STREAM'}
                                </button>
                            </div>
                        </div>
                    )}
                </div>

                {/* Upload Queue and Execution Control */}
                {uploadedFiles.length > 0 && (
                    <div className="space-y-3 bg-slate-50 p-4 rounded-2xl border border-slate-150 max-w-2xl mx-auto">
                        <div className="flex items-center justify-between">
                            <span className="text-[9px] text-slate-400 font-black uppercase tracking-wider">Danh sách tệp đang chọn ({uploadedFiles.length})</span>
                            <button onClick={() => setUploadedFiles([])} className="text-[9px] text-red-500 hover:underline font-black uppercase">Xóa hết</button>
                        </div>
                        <div className="max-h-24 overflow-y-auto space-y-1.5 pr-1 custom-scrollbar">
                             {uploadedFiles.map((f, i) => (
                                 <div key={i} className="flex items-center justify-between p-2 rounded-xl bg-white border border-slate-100">
                                     <div className="flex items-center gap-2 min-w-0">
                                         {activeTab === 'image' ? <ImageIcon className="w-3.5 h-3.5 text-blue-500 shrink-0" /> : <Video className="w-3.5 h-3.5 text-blue-500 shrink-0" />}
                                         <span className="text-xs text-slate-700 truncate font-bold">{f.name}</span>
                                     </div>
                                     <button onClick={() => setUploadedFiles(prev => prev.filter((_, idx)=>idx!==i))} className="text-slate-400 hover:text-red-500"><X className="w-3.5 h-3.5"/></button>
                                 </div>
                             ))}
                        </div>
                        
                        <div className="flex flex-col gap-2">
                            {activeTab === 'video' && (
                                <div className="flex items-center gap-2 p-2 bg-blue-50/50 border border-blue-100 rounded-xl cursor-pointer hover:bg-blue-50 transition-colors" onClick={() => setGenerate3D(!generate3D)}>
                                    <input 
                                        type="checkbox" 
                                        id="generate3D" 
                                        checked={generate3D} 
                                        onChange={e => setGenerate3D(e.target.checked)}
                                        onClick={e => e.stopPropagation()}
                                        className="w-3.5 h-3.5 text-blue-600 rounded border-slate-300 focus:ring-blue-500 cursor-pointer"
                                    />
                                    <label htmlFor="generate3D" className="flex-1 text-[10px] font-bold text-slate-700 cursor-pointer tracking-tight pointer-events-none select-none">
                                        Dựng bản sao số 3D (Digital Twin)
                                    </label>
                                </div>
                            )}

                            {modelType === 'road' && (
                                <div className="flex items-center gap-2 p-2 bg-emerald-50/50 border border-emerald-100 rounded-xl cursor-pointer hover:bg-emerald-50 transition-colors" onClick={() => setSegmentationEnabled(!segmentationEnabled)}>
                                    <input 
                                        type="checkbox" 
                                        id="segmentationEnabled" 
                                        checked={segmentationEnabled} 
                                        onChange={e => setSegmentationEnabled(e.target.checked)}
                                        onClick={e => e.stopPropagation()}
                                        className="w-3.5 h-3.5 text-emerald-600 rounded border-slate-300 focus:ring-emerald-500 cursor-pointer"
                                    />
                                    <label htmlFor="segmentationEnabled" className="flex-1 text-[10px] font-bold text-slate-700 cursor-pointer tracking-tight pointer-events-none select-none">
                                        Nhận diện giới hạn mặt đường (Bật cho Flycam, tắt cho ảnh chụp gần)
                                    </label>
                                </div>
                            )}

                            {modelType === 'road' && (
                                <div className="flex items-center gap-2 p-2 bg-violet-50/50 border border-violet-100 rounded-xl cursor-pointer hover:bg-violet-50 transition-colors" onClick={() => setColorNormalizationEnabled(!colorNormalizationEnabled)}>
                                    <input
                                        type="checkbox"
                                        id="colorNormalizationEnabled"
                                        checked={colorNormalizationEnabled}
                                        onChange={e => setColorNormalizationEnabled(e.target.checked)}
                                        onClick={e => e.stopPropagation()}
                                        className="w-3.5 h-3.5 text-violet-600 rounded border-slate-300 focus:ring-violet-500 cursor-pointer"
                                    />
                                    <label htmlFor="colorNormalizationEnabled" className="flex-1 text-[10px] font-bold text-slate-700 cursor-pointer tracking-tight pointer-events-none select-none">
                                        Chuẩn hóa màu đầu vào về miền dữ liệu train (Ảnh &amp; Video)
                                    </label>
                                    <span className={`text-[9px] font-black uppercase ${colorNormalizationEnabled ? 'text-violet-600' : 'text-slate-400'}`}>
                                        {colorNormalizationEnabled ? 'Bật' : 'Tắt'}
                                    </span>
                                </div>
                            )}
                        </div>

                        <button onClick={handleStartDetection} disabled={isUploading || !modelType} className="w-full py-3 bg-emerald-500 hover:bg-emerald-600 text-white text-[10px] font-black rounded-xl shadow-md shadow-emerald-500/10 transition-all flex items-center justify-center gap-2 uppercase tracking-widest">
                            {isUploading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Play className="w-3.5 h-3.5 fill-current" />}
                            Phát hiện ngay
                        </button>
                    </div>
                )}
            </div>

            {/* PHÂN KHU 2: BẢNG QUẢN LÝ TÁC VỤ AI (TASK MANAGEMENT TABLE - FULL WIDTH) */}
            <div className="flex-1 bg-white border border-slate-200/80 rounded-3xl shadow-sm overflow-hidden flex flex-col min-h-[400px]">
                {/* Dashboard Header */}
                <div className="p-5 border-b border-slate-100 shrink-0 space-y-4">
                  <div className="flex flex-col md:flex-row md:items-center justify-between gap-3">
                    <div>
                        <h2 className="text-xs font-black text-slate-800 uppercase tracking-widest">Tiến trình Phân tích & Quản lý Tác vụ AI</h2>
                        <p className="text-[10px] text-slate-450 font-bold mt-0.5">
                            Hiển thị {filteredTasks.length}/{tasks.length} tác vụ · Chọn bộ lọc để xem các đợt, tuyến và người thực hiện hiện có
                        </p>
                    </div>
                    
                    <div className="relative w-full md:w-72">
                        <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-slate-400" />
                        <input 
                            type="text"
                            value={searchQuery}
                            onChange={(e) => setSearchQuery(e.target.value)}
                            placeholder="Tìm nhanh tên tệp hoặc mã tác vụ..."
                            className="w-full bg-slate-50 border border-slate-200 rounded-xl pl-9 pr-8 py-2 text-xs font-bold text-slate-700 focus:ring-2 focus:ring-blue-500/20 focus:outline-none transition-all placeholder:text-slate-400"
                        />
                        {searchQuery && (
                            <button onClick={() => setSearchQuery('')} className="absolute right-3 top-1/2 -translate-y-1/2 text-[9px] font-black text-slate-400 hover:text-slate-650">Xóa</button>
                        )}
                    </div>
                  </div>

                  <div className="grid grid-cols-2 lg:grid-cols-5 gap-2">
                    <label className="relative">
                        <span className="sr-only">Lọc theo đợt khảo sát</span>
                        <select value={taskSurveyFilter} onChange={e => setTaskSurveyFilter(e.target.value)} className="w-full appearance-none bg-white border border-slate-200 rounded-xl px-3 pr-8 py-2.5 text-[10px] font-bold text-slate-700 focus:ring-2 focus:ring-blue-500/20 focus:outline-none cursor-pointer">
                            <option value="all">Tất cả đợt ({tasks.length})</option>
                            {taskFilterOptions.surveys.map(option => <option key={option.value} value={option.value}>{option.label} ({option.count})</option>)}
                        </select>
                        <ChevronRight className="absolute right-2.5 top-1/2 -translate-y-1/2 rotate-90 w-3.5 h-3.5 text-slate-400 pointer-events-none" />
                    </label>
                    <label className="relative">
                        <span className="sr-only">Lọc theo tuyến đường</span>
                        <select value={taskRouteFilter} onChange={e => setTaskRouteFilter(e.target.value)} className="w-full appearance-none bg-white border border-slate-200 rounded-xl px-3 pr-8 py-2.5 text-[10px] font-bold text-slate-700 focus:ring-2 focus:ring-blue-500/20 focus:outline-none cursor-pointer">
                            <option value="all">Tất cả tuyến</option>
                            {taskFilterOptions.routes.map(option => <option key={option.value} value={option.value}>{option.label} ({option.count})</option>)}
                        </select>
                        <ChevronRight className="absolute right-2.5 top-1/2 -translate-y-1/2 rotate-90 w-3.5 h-3.5 text-slate-400 pointer-events-none" />
                    </label>
                    <label className="relative">
                        <span className="sr-only">Lọc theo người thực hiện</span>
                        <select value={taskSurveyorFilter} onChange={e => setTaskSurveyorFilter(e.target.value)} className="w-full appearance-none bg-white border border-slate-200 rounded-xl px-3 pr-8 py-2.5 text-[10px] font-bold text-slate-700 focus:ring-2 focus:ring-blue-500/20 focus:outline-none cursor-pointer">
                            <option value="all">Tất cả người thực hiện</option>
                            {taskFilterOptions.surveyors.map(option => <option key={option.value} value={option.value}>{option.label} ({option.count})</option>)}
                        </select>
                        <ChevronRight className="absolute right-2.5 top-1/2 -translate-y-1/2 rotate-90 w-3.5 h-3.5 text-slate-400 pointer-events-none" />
                    </label>
                    <label className="relative">
                        <span className="sr-only">Lọc theo trạng thái</span>
                        <select value={taskStatusFilter} onChange={e => setTaskStatusFilter(e.target.value)} className="w-full appearance-none bg-white border border-slate-200 rounded-xl px-3 pr-8 py-2.5 text-[10px] font-bold text-slate-700 focus:ring-2 focus:ring-blue-500/20 focus:outline-none cursor-pointer">
                            <option value="all">Tất cả trạng thái</option>
                            {taskFilterOptions.statuses.map(option => <option key={option.value} value={option.value}>{option.label} ({option.count})</option>)}
                        </select>
                        <ChevronRight className="absolute right-2.5 top-1/2 -translate-y-1/2 rotate-90 w-3.5 h-3.5 text-slate-400 pointer-events-none" />
                    </label>
                    <button
                        type="button"
                        onClick={clearTaskFilters}
                        disabled={activeTaskFilterCount === 0}
                        className="col-span-2 lg:col-span-1 inline-flex items-center justify-center gap-2 rounded-xl border border-slate-200 bg-slate-50 px-3 py-2.5 text-[10px] font-black text-slate-500 transition-all hover:bg-slate-100 hover:text-slate-700 disabled:cursor-not-allowed disabled:opacity-45"
                    >
                        <RefreshCcw className="w-3.5 h-3.5" />
                        Xóa bộ lọc {activeTaskFilterCount > 0 ? `(${activeTaskFilterCount})` : ''}
                    </button>
                  </div>
                </div>

                {/* Table Area */}
                <div className="overflow-x-auto flex-1 custom-scrollbar min-h-0">
                    {filteredTasks.length > 0 ? (
                        <table className="w-full text-left border-collapse min-w-[900px]">
                            <thead className="sticky top-0 bg-slate-55/65 backdrop-blur-sm border-b border-slate-200/60 z-10">
                                <tr className="text-[9px] text-slate-400 font-black uppercase tracking-widest">
                                    <th className="px-6 py-3 font-black">Tên tệp tin</th>
                                    <th className="px-6 py-3 font-black">Đợt khảo sát</th>
                                    <th className="px-6 py-3 font-black">Tuyến đường</th>
                                    <th className="px-6 py-3 font-black">Người thực hiện</th>
                                    <th className="px-6 py-3 font-black">Ngày tháng</th>
                                    <th className="px-6 py-3 font-black">AI Model</th>
                                    <th className="px-6 py-3 font-black text-center">Trạng thái AI</th>
                                    <th className="px-6 py-3 font-black text-right">Thao tác</th>
                                </tr>
                            </thead>
                            <tbody className="text-xs divide-y divide-slate-100 font-bold text-slate-600">
                                {filteredTasks.map((task) => {
                                    const survey = task.survey_id ? surveys.find((s: any) => s.id === task.survey_id) : null;
                                    const isVideo = task.filename?.toLowerCase().match(/\.(mp4|avi|mov)$/);
                                    const statusInfo = statusConfig[task.status] || { icon: Clock, label: task.status, class: 'text-slate-500 bg-slate-55 border-slate-100' };
                                    const StatusIcon = statusInfo.icon;
                                    const crackCount = getCrackCount(task);

                                    return (
                                        <tr key={task.task_id} className="hover:bg-slate-50/50 transition-colors cursor-pointer group" onClick={() => setSelectedResult(task)}>
                                            {/* Filename */}
                                            <td className="px-6 py-3">
                                                <div className="flex items-center gap-3">
                                                    <div className="p-2 bg-slate-50 text-slate-500 rounded-xl group-hover:bg-blue-50 group-hover:text-blue-600 transition-colors border border-slate-150">
                                                        {isVideo ? <Video className="w-3.5 h-3.5" /> : <ImageIcon className="w-3.5 h-3.5" />}
                                                    </div>
                                                    <div className="min-w-0">
                                                        <p className="font-bold text-slate-800 truncate max-w-[240px]" title={task.filename}>
                                                            {task.filename}
                                                        </p>
                                                        {task.message && (
                                                            <p className="text-[9px] text-blue-500 font-bold mt-0.5 animate-pulse">
                                                                {task.message}
                                                            </p>
                                                        )}
                                                    </div>
                                                </div>
                                            </td>
                                            {/* Survey name */}
                                            <td className="px-6 py-3">
                                                {survey ? (
                                                    <span className="text-slate-700 font-bold">{survey.name}</span>
                                                ) : (
                                                    <span className="text-slate-400 font-medium italic">— Không gắn đợt —</span>
                                                )}
                                            </td>
                                            {/* Route name */}
                                            <td className="px-6 py-3">
                                                {survey && survey.route_name ? (
                                                    <div className="flex flex-col">
                                                        <span className="text-slate-700 font-bold">{survey.route_name}</span>
                                                        <span className="text-[9px] text-slate-400 font-black tracking-wide mt-0.5">
                                                            Km {survey.route_km_start} - Km {survey.route_km_end}
                                                        </span>
                                                    </div>
                                                ) : (
                                                    <span className="text-slate-400 font-medium italic">—</span>
                                                )}
                                            </td>
                                            {/* Surveyor */}
                                            <td className="px-6 py-3">
                                                {survey && survey.surveyor ? (
                                                    <span className="text-slate-700 font-bold">{survey.surveyor}</span>
                                                ) : (
                                                    <span className="text-slate-400 font-medium italic">—</span>
                                                )}
                                            </td>
                                            {/* Date */}
                                            <td className="px-6 py-3 text-slate-500 font-medium">
                                                {task.created_at ? new Date(task.created_at).toLocaleString('vi-VN') : 'N/A'}
                                            </td>
                                            {/* AI Model */}
                                            <td className="px-6 py-3">
                                                <span className="px-2 py-1 rounded-md text-[8px] font-black uppercase tracking-wider bg-slate-100 border border-slate-200 text-slate-550">
                                                    {task.model_type === 'bridge' ? '🏗️ Cầu' : '🚧 Đường bộ'}
                                                </span>
                                            </td>
                                            {/* Status */}
                                            <td className="px-6 py-3 text-center">
                                                <span className={`inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-[8px] font-black uppercase tracking-wider border ${statusInfo.class}`}>
                                                    <StatusIcon className={`w-2.5 h-2.5 ${task.status === 'processing' ? 'animate-spin' : ''}`} />
                                                    {statusInfo.label}
                                                </span>
                                            </td>
                                            {/* Actions */}
                                            <td className="px-6 py-3 text-right" onClick={(e) => e.stopPropagation()}>
                                                <div className="flex items-center justify-end gap-2">
                                                    {task.status === 'done' ? (
                                                        <button 
                                                            onClick={() => setSelectedResult(task)}
                                                            className="px-3 py-1.5 rounded-xl bg-blue-600 hover:bg-blue-500 text-white text-[9px] font-black uppercase tracking-widest transition-all active:scale-95 flex items-center gap-1 shadow-sm shadow-blue-500/10"
                                                        >
                                                            <Play className="w-2.5 h-2.5 fill-current" /> Xem kết quả
                                                            {crackCount > 0 && (
                                                                <span className="ml-1 bg-red-500 text-white text-[8px] font-black px-1.5 py-0.5 rounded-full">
                                                                    {crackCount}
                                                                </span>
                                                            )}
                                                        </button>
                                                    ) : (
                                                        <button 
                                                            onClick={() => setSelectedResult(task)}
                                                            className="px-3 py-1.5 rounded-xl bg-slate-100 hover:bg-slate-205 text-slate-600 text-[9px] font-black uppercase tracking-widest transition-all active:scale-95"
                                                        >
                                                            Xem chi tiết
                                                        </button>
                                                    )}
                                                     {task.status === 'error' && (
                                                         <button 
                                                             onClick={() => retryTask(task.task_id)}
                                                             className="p-1.5 bg-amber-50 hover:bg-amber-100 text-amber-600 hover:text-amber-700 rounded-xl transition-colors border border-amber-200"
                                                             title="Phân tích lại"
                                                         >
                                                             <RefreshCcw className="w-3.5 h-3.5" />
                                                         </button>
                                                     )}
                                                    <button 
                                                        onClick={() => {
                                                            if (confirm("Bạn có chắc chắn muốn xóa tác vụ này?")) {
                                                                deleteTask(task.task_id);
                                                            }
                                                        }}
                                                        className="p-1.5 bg-slate-50 hover:bg-red-50 text-slate-400 hover:text-red-500 rounded-xl transition-colors border border-slate-200"
                                                        title="Xóa tác vụ"
                                                    >
                                                        <Trash2 className="w-3.5 h-3.5" />
                                                    </button>
                                                </div>
                                            </td>
                                        </tr>
                                    );
                                })}
                            </tbody>
                        </table>
                    ) : (
                        <div className="h-full flex flex-col items-center justify-center text-center p-12 opacity-50 grayscale flex-1">
                            <Clock className="w-12 h-12 text-slate-300 mx-auto mb-3 animate-pulse" />
                            <p className="text-xs font-black text-slate-400 uppercase tracking-widest">Không tìm thấy tác vụ nào</p>
                        </div>
                    )}
                </div>
            </div>
        </div>
      )}
      
      {showVerifyModal && (
          <div className="fixed inset-0 z-[99999] flex items-center justify-center bg-slate-900/40 backdrop-blur-md px-4 animate-fade-in text-white">
            <div className="bg-white border border-slate-200 p-6 rounded-2xl w-full max-w-lg shadow-2xl animate-scale-up relative max-h-[90vh] overflow-y-auto custom-scrollbar">
               <button onClick={() => setShowVerifyModal(false)} className="absolute top-4 right-4 p-1 hover:bg-slate-100 rounded-full text-slate-400 hover:text-slate-900 transition-colors"><X className="w-4 h-4"/></button>
               <h2 className="text-lg font-bold text-slate-900 mb-1 flex items-center gap-2"><CheckCircle2 className="w-5 h-5 text-emerald-500"/>Kiểm duyệt &amp; Ghi nhận Hư hỏng</h2>
               <p className="text-[11px] text-slate-500 mb-5 leading-relaxed">AI đã gợi ý thông tin. Vui lòng kiểm tra và bổ sung vị trí trước khi đẩy lên Bản đồ Giám sát.</p>
               <div className="space-y-4">
                  <div>
                     <label className="text-[10px] text-slate-500 font-bold uppercase tracking-wider mb-1.5 block">Tên sự cố ghi nhận</label>
                     <input value={verifyForm.title} onChange={e => setVerifyForm({...verifyForm, title: e.target.value})} className="input-field w-full text-xs" placeholder="VD: Nứt dọc mép đường quốc lộ" />
                  </div>
                  <div className="grid grid-cols-2 gap-3">
                     <div>
                        <label className="text-[10px] text-slate-500 font-bold uppercase tracking-wider mb-1.5 block">Mức độ cảnh báo</label>
                        <select value={verifyForm.severity} onChange={e => setVerifyForm({...verifyForm, severity: e.target.value})} className="input-field w-full text-xs">
                           <option value="critical">🔴 Nguy hiểm</option>
                           <option value="warning">🟡 Cảnh báo</option>
                        </select>
                     </div>
                     <div>
                        <label className="text-[10px] text-slate-500 font-bold uppercase tracking-wider mb-1.5 block">Phân hạng TCVN</label>
                        <div className="flex gap-1.5">
                           {(['A','B','C','D','E'] as const).map(g => (
                              <button key={g} onClick={() => setVerifyForm({...verifyForm, tcvn_grade: g})}
                                className={`flex-1 py-2 rounded-lg text-[10px] font-black border transition-all ${verifyForm.tcvn_grade === g ? 'bg-blue-50 border-blue-300 text-blue-700 ring-1 ring-blue-200' : 'bg-white border-slate-200 text-slate-400 hover:bg-slate-50'}`}
                              >{g}</button>
                           ))}
                        </div>
                        {verifyForm.tcvn_grade_auto && <p className="text-[9px] text-blue-500 mt-1">AI gợi ý: Hạng {verifyForm.tcvn_grade_auto} ({TCVN_GRADES[verifyForm.tcvn_grade_auto as TCVNGrade]?.label})</p>}
                     </div>
                  </div>
                  <div className="grid grid-cols-2 gap-3">
                     <div>
                        <label className="text-[10px] text-slate-500 font-bold uppercase tracking-wider mb-1.5 block">Tuyến đường</label>
                        <input value={verifyForm.route_name} onChange={e => setVerifyForm({...verifyForm, route_name: e.target.value})} className="input-field w-full text-xs" placeholder="QL1A" />
                     </div>
                     <div>
                        <label className="text-[10px] text-slate-500 font-bold uppercase tracking-wider mb-1.5 block">Lý trình (Km)</label>
                        <input type="number" step="0.001" value={verifyForm.route_km ?? ''} onChange={e => setVerifyForm({...verifyForm, route_km: e.target.value === '' ? null : Number(e.target.value)})} className="input-field w-full text-xs" placeholder="Chưa có dữ liệu" />
                     </div>
                  </div>

                  <div className="mt-2">
                     <label className="text-[10px] text-slate-500 font-bold uppercase tracking-wider mb-1.5 block">Làn đường (Có thể chọn nhiều)</label>
                     <div className="flex flex-wrap gap-1.5">
                        {([
                          { value: 'left', label: 'Làn trái (Left)' },
                          { value: 'center', label: 'Làn giữa (Center)' },
                          { value: 'right', label: 'Làn phải (Right)' },
                          { value: 'shoulder', label: 'Lề đường (Shoulder)' }
                        ] as const).map(lane => {
                           const selectedLanes = verifyForm.lane_position ? verifyForm.lane_position.split(',') : [];
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
                                    setVerifyForm({
                                       ...verifyForm,
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

                  {verifyForm.is_calibrated ? (
                      <div className="px-3 py-2 bg-emerald-50 border border-emerald-200 rounded-lg flex items-center gap-2 mt-2">
                        <CheckCircle2 className="w-4 h-4 text-emerald-500" />
                        <span className="text-[10px] font-bold text-emerald-700">
                          Kích thước hiệu chuẩn GSD tự động (TCVN 41:2019).
                        </span>
                      </div>
                  ) : (
                      <div className="px-3 py-2 bg-amber-50 border border-amber-200 rounded-lg flex items-center gap-2 mt-2">
                        <AlertCircle className="w-4 h-4 text-amber-500" />
                        <span className="text-[10px] font-bold text-amber-700">
                          Chưa có vật tham chiếu. Nhập kích thước thủ công.
                        </span>
                      </div>
                  )}

                  <div className="grid grid-cols-2 gap-3 mt-2">
                     <div>
                        <label className="text-[10px] text-slate-500 font-bold uppercase tracking-wider mb-1.5 block">Diện tích (m²)</label>
                        <input type="number" step="0.01" value={verifyForm.damage_area_m2 ?? ''} 
                          onChange={e => {
                            const val = e.target.value === '' ? null : Number(e.target.value);
                            setVerifyForm({...verifyForm, damage_area_m2: Number.isFinite(val) ? val : null});
                          }} 
                          className={`input-field w-full text-xs font-bold ${verifyForm.is_calibrated ? 'border-emerald-200 bg-emerald-50 text-emerald-700' : ''}`} placeholder="0.5" />
                     </div>
                     <div>
                        <label className="text-[10px] text-slate-500 font-bold uppercase tracking-wider mb-1.5 block">Bề rộng vết nứt (mm)</label>
                        <input type="number" step="0.1" value={verifyForm.damage_width_mm ?? ''} 
                          onChange={e => {
                            const val = e.target.value === '' ? null : Number(e.target.value);
                            setVerifyForm({...verifyForm, damage_width_mm: Number.isFinite(val) ? val : null});
                          }} 
                          className={`input-field w-full text-xs font-bold ${verifyForm.is_calibrated ? 'border-emerald-200 bg-emerald-50 text-emerald-700' : ''}`} placeholder="2.5" />
                     </div>
                  </div>
                  <div>
                     <label className="text-[10px] text-slate-500 font-bold uppercase tracking-wider mb-1.5 block">Phương pháp sửa chữa (AI gợi ý)</label>
                     <input value={verifyForm.repair_method} onChange={e => setVerifyForm({...verifyForm, repair_method: e.target.value})} className="input-field w-full text-xs text-blue-600 font-medium" />
                  </div>
                  <div>
                     <label className="text-[10px] text-slate-500 font-bold uppercase tracking-wider mb-1.5 block">Chi tiết kỹ thuật</label>
                     <textarea value={verifyForm.description} onChange={e => setVerifyForm({...verifyForm, description: e.target.value})} className="input-field w-full h-16 resize-none text-xs" placeholder="Ghi chú thêm..." />
                  </div>
                  <div>
                     <label className="text-[10px] text-slate-500 font-bold uppercase tracking-wider mb-1.5 block">Vị trí trên bản đồ (click để chọn)</label>
                     <div className="h-40 rounded-xl overflow-hidden border border-slate-200">
                        <MiniMapPicker lat={verifyForm.lat} lng={verifyForm.lng} onLocationSelect={async (lat: number, lng: number) => {
                            setVerifyForm((prev: any) => ({ ...prev, lat, lng }));
                            try {
                               const addr = await fetchReverseGeocode(lat, lng, verifyForm.route_name, verifyForm.route_km);
                               setVerifyForm((prev: any) => ({ ...prev, lat, lng, address: addr }));
                            } catch (e) {
                               console.error(e);
                            }
                         }} />
                     </div>
                     <div className="flex items-center gap-2 mt-1.5">
                        <input value={verifyForm.address} onChange={e => setVerifyForm({...verifyForm, address: e.target.value})} className="input-field flex-1 text-xs" placeholder="Địa chỉ / Mô tả vị trí" />
                        <span className="text-[9px] text-blue-500 font-mono whitespace-nowrap">
                          {Number.isFinite(verifyForm.lat) && Number.isFinite(verifyForm.lng)
                            ? `${verifyForm.lat.toFixed(5)}, ${verifyForm.lng.toFixed(5)}`
                            : 'Chưa có tọa độ'}
                        </span>
                     </div>
                  </div>
               </div>
               <div className="grid grid-cols-2 gap-3 mt-6">
                  <button onClick={() => setShowVerifyModal(false)} className="py-2.5 rounded-xl text-[11px] font-bold text-slate-400 bg-slate-50 hover:bg-slate-100 transition-colors">Hủy Bỏ</button>
                  <button onClick={handleVerify} disabled={isConfirming} className="btn-gradient py-2.5 rounded-xl text-[11px] font-bold flex justify-center items-center gap-2 shadow-lg shadow-blue-500/20 active:scale-95 transition-transform text-white">
                     {isConfirming ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <PlusCircle className="w-3.5 h-3.5" />} 
                     Xác nhận Đăng
                  </button>
               </div>
            </div>
          </div>
       )}

       {showSurveyModal && (
          <div className="fixed inset-0 z-[100] flex items-center justify-center bg-slate-900/40 backdrop-blur-md px-4 animate-fade-in">
            <div className="bg-white border border-slate-200 p-6 rounded-2xl w-full max-w-md shadow-2xl animate-scale-up relative">
               <button onClick={() => setShowSurveyModal(false)} className="absolute top-4 right-4 p-1 hover:bg-slate-100 rounded-full text-slate-400 hover:text-slate-900 transition-colors"><X className="w-4 h-4"/></button>
               <h2 className="text-lg font-bold text-slate-900 mb-1 flex items-center gap-2"><ClipboardList className="w-5 h-5 text-blue-500"/>{isEditingSurvey ? 'Sửa Đợt Khảo sát' : 'Tạo Đợt Khảo sát mới'}</h2>
               <p className="text-[11px] text-slate-500 mb-5">Nhóm nhiều file phân tích vào cùng 1 đợt khảo sát.</p>
               <div className="space-y-3">
                  <div>
                     <label className="text-[10px] text-slate-500 font-bold uppercase tracking-wider mb-1 block">Tên đợt khảo sát *</label>
                     <input value={newSurvey.name} onChange={e => setNewSurvey({...newSurvey, name: e.target.value})} className="input-field w-full text-xs" placeholder="VD: Khảo sát QL1A Km120-Km135" />
                  </div>
                  <div className="grid grid-cols-3 gap-3">
                     <div>
                        <label className="text-[10px] text-slate-500 font-bold uppercase tracking-wider mb-1 block">Tuyến đường</label>
                        <input value={newSurvey.route_name} onChange={e => setNewSurvey({...newSurvey, route_name: e.target.value})} className="input-field w-full text-xs" placeholder="QL1A" />
                     </div>
                     <div>
                        <label className="text-[10px] text-slate-550 font-bold uppercase tracking-wider mb-1 block">Km bắt đầu</label>
                        <input type="number" value={newSurvey.route_km_start ?? ''} onChange={e => setNewSurvey({...newSurvey, route_km_start: e.target.value === '' ? null : Number(e.target.value)})} className="input-field w-full text-xs" placeholder="Km bắt đầu đã đo" />
                     </div>
                     <div>
                        <label className="text-[10px] text-slate-500 font-bold uppercase tracking-wider mb-1 block">Km kết thúc</label>
                        <input type="number" value={newSurvey.route_km_end ?? ''} onChange={e => setNewSurvey({...newSurvey, route_km_end: e.target.value === '' ? null : Number(e.target.value)})} className="input-field w-full text-xs" placeholder="Km kết thúc đã đo" />
                     </div>
                  </div>
                  <div>
                     <label className="text-[10px] text-slate-500 font-bold uppercase tracking-wider mb-1 block">Người khảo sát</label>
                     <input value={newSurvey.surveyor} onChange={e => setNewSurvey({...newSurvey, surveyor: e.target.value})} className="input-field w-full text-xs" placeholder="Họ tên người thực hiện khảo sát" />
                  </div>
                  <div className="grid grid-cols-2 gap-3">
                     <div>
                        <label className="text-[10px] text-slate-500 font-bold uppercase tracking-wider mb-1 block">Số làn đường</label>
                        <input type="number" min="1" max="10" value={newSurvey.num_lanes || 2} onChange={e => setNewSurvey({...newSurvey, num_lanes: parseInt(e.target.value) || 2})} className="input-field w-full text-xs" />
                     </div>
                     <div className="flex flex-col justify-end">
                        <div 
                           className="flex items-center gap-2 p-2.5 bg-slate-50 border border-slate-200 rounded-xl cursor-pointer hover:bg-slate-100 transition-colors h-[34px]" 
                           onClick={() => setNewSurvey({...newSurvey, has_emergency_lane: !newSurvey.has_emergency_lane})}
                        >
                            <input 
                                type="checkbox" 
                                id="has_emergency_lane" 
                                checked={!!newSurvey.has_emergency_lane} 
                                onChange={e => setNewSurvey({...newSurvey, has_emergency_lane: e.target.checked})}
                                onClick={e => e.stopPropagation()}
                                className="w-3.5 h-3.5 text-blue-600 rounded border-slate-300 focus:ring-blue-500 cursor-pointer"
                            />
                            <label htmlFor="has_emergency_lane" className="flex-1 text-[10px] font-bold text-slate-700 cursor-pointer tracking-tight pointer-events-none select-none">
                                Làn khẩn cấp rìa phải
                            </label>
                        </div>
                     </div>
                  </div>
                  <div>
                     <label className="text-[10px] text-slate-500 font-bold uppercase tracking-wider mb-1 block">Ghi chú</label>
                     <textarea value={newSurvey.notes} onChange={e => setNewSurvey({...newSurvey, notes: e.target.value})} className="input-field w-full h-16 resize-none text-xs" placeholder="Ghi chú thêm..." />
                  </div>
               </div>
               <div className="grid grid-cols-2 gap-3 mt-6">
                  <button onClick={() => setShowSurveyModal(false)} className="py-2.5 rounded-xl text-[11px] font-bold text-slate-400 bg-slate-50 hover:bg-slate-100 transition-colors">Hủy</button>
                  <button onClick={handleSaveSurvey} disabled={!newSurvey.name} className="btn-gradient py-2.5 rounded-xl text-[11px] font-bold flex justify-center items-center gap-2 shadow-lg shadow-blue-500/20 active:scale-95 transition-transform text-white disabled:opacity-50">
                     {isEditingSurvey ? <Edit2 className="w-3.5 h-3.5" /> : <PlusCircle className="w-3.5 h-3.5" />} {isEditingSurvey ? 'Lưu đợt' : 'Tạo đợt'}
                  </button>
               </div>
            </div>
          </div>
       )}
    </div>
  );
}

const DetectionViewer = forwardRef<any, { 
  src: string, 
  bboxes: any[], 
  roadContour?: any[], 
  isVideo?: boolean, 
  videoRef?: any,
  showRoadMask?: boolean,
  setShowRoadMask?: any,
  showAILayer?: boolean,
  setShowAILayer?: any
}>(
  function DetectionViewer({ 
    src, 
    bboxes, 
    roadContour = [], 
    isVideo = false, 
    videoRef,
    showRoadMask: propShowRoadMask,
    setShowRoadMask: propSetShowRoadMask,
    showAILayer: propShowAILayer,
    setShowAILayer: propSetShowAILayer
  }, ref) {
    const imgRef = useRef<HTMLImageElement>(null);
    const containerRef = useRef<HTMLDivElement>(null);
    const [scale, setScale] = useState({ x: 1, y: 1 });
    const [mediaOffset, setMediaOffset] = useState({ x: 0, y: 0 }); // v35.0: For object-contain bar compensation
    const [imgLoaded, setImgLoaded] = useState(false);
    const [isFullScreen, setIsFullScreen] = useState(false);
    const [localShowAILayer, setLocalShowAILayer] = useState(true);
    const [localShowRoadMask, setLocalShowRoadMask] = useState(true);

    const showAILayer = propShowAILayer !== undefined ? propShowAILayer : localShowAILayer;
    const setShowAILayer = propSetShowAILayer || setLocalShowAILayer;
    const showRoadMask = propShowRoadMask !== undefined ? propShowRoadMask : localShowRoadMask;
    const setShowRoadMask = propSetShowRoadMask || setLocalShowRoadMask;

    // v73: True HTML5 Fullscreen API for the entire container (Video + AI Layer)
    useEffect(() => {
        const handleFullscreenChange = () => {
            const isFull = !!(document.fullscreenElement || (document as any).webkitFullscreenElement || (document as any).mozFullScreenElement || (document as any).msFullscreenElement);
            setIsFullScreen(isFull);
            // Trigger a slight delay scale update because fullscreen transition takes a moment
            setTimeout(updateScale, 100);
        };
        document.addEventListener('fullscreenchange', handleFullscreenChange);
        document.addEventListener('webkitfullscreenchange', handleFullscreenChange);
        document.addEventListener('mozfullscreenchange', handleFullscreenChange);
        document.addEventListener('MSFullscreenChange', handleFullscreenChange);
        return () => {
            document.removeEventListener('fullscreenchange', handleFullscreenChange);
            document.removeEventListener('webkitfullscreenchange', handleFullscreenChange);
            document.removeEventListener('mozfullscreenchange', handleFullscreenChange);
            document.removeEventListener('MSFullscreenChange', handleFullscreenChange);
        };
    }, []);

    const toggleFullScreen = () => {
        const el = containerRef.current as any;
        const doc = document as any;
        if (!doc.fullscreenElement && !doc.webkitFullscreenElement && !doc.mozFullScreenElement && !doc.msFullscreenElement) {
            if (el?.requestFullscreen) el.requestFullscreen().catch(console.error);
            else if (el?.webkitRequestFullscreen) el.webkitRequestFullscreen();
            else if (el?.mozRequestFullScreen) el.mozRequestFullScreen();
            else if (el?.msRequestFullscreen) el.msRequestFullscreen();
        } else {
            if (doc.exitFullscreen) doc.exitFullscreen();
            else if (doc.webkitExitFullscreen) doc.webkitExitFullscreen();
            else if (doc.mozCancelFullScreen) doc.mozCancelFullScreen();
            else if (doc.msExitFullscreen) doc.msExitFullscreen();
        }
    };
    
    // Zoom & Pan State (v35.0 Professional)
    const [zoom, setZoom] = useState(1);
    const [offset, setOffset] = useState({ x: 0, y: 0 });
    const [isDragging, setIsDragging] = useState(false);
    const [dragStart, setDragStart] = useState({ x: 0, y: 0 });

    const updateScale = () => {
        const el = isVideo ? videoRef?.current : imgRef.current;
        const container = containerRef.current;
        if (el && container) {
            const nw = isVideo ? (el as HTMLVideoElement).videoWidth : (el as HTMLImageElement).naturalWidth;
            const nh = isVideo ? (el as HTMLVideoElement).videoHeight : (el as HTMLImageElement).naturalHeight;
            const cw = container.clientWidth;
            const ch = container.clientHeight;

            if (nw && nh && cw && ch) {
                // v35.0 Object-contain math: find actual rendered media size & offsets
                const containerAR = cw / ch;
                const mediaAR = nw / nh;
                let renderedWidth, renderedHeight, offX = 0, offY = 0;

                if (mediaAR > containerAR) {
                    renderedWidth = cw;
                    renderedHeight = cw / mediaAR;
                    offY = (ch - renderedHeight) / 2;
                } else {
                    renderedHeight = ch;
                    renderedWidth = ch * mediaAR;
                    offX = (cw - renderedWidth) / 2;
                }

                setScale({ x: renderedWidth / nw, y: renderedHeight / nh });
                setMediaOffset({ x: offX, y: offY });
            }
        }
    };

    useEffect(() => {
        updateScale();
        window.addEventListener('resize', updateScale);
        return () => window.removeEventListener('resize', updateScale);
    }, [imgLoaded, isFullScreen]);

    const focusOnBBox = (b: any) => {
        console.log("[focusOnBBox] Target:", b);
        if (!b || !b.bbox || !Array.isArray(b.bbox) || b.bbox.length < 4) {
            console.warn("[focusOnBBox] Invalid bbox data:", b);
            return;
        }
        let [xmin, ymin, xmax, ymax] = b.bbox;
        const isNormalized = Math.max(xmin, ymin, xmax, ymax) <= 1.05;
        
        const container = containerRef.current;
        const el = isVideo ? videoRef?.current : imgRef.current;
        if (!container || !el) return;
        
        const nw = isVideo ? (el as HTMLVideoElement).videoWidth : (el as HTMLImageElement).naturalWidth;
        const nh = isVideo ? (el as HTMLVideoElement).videoHeight : (el as HTMLImageElement).naturalHeight;
        const cw = container.clientWidth;
        const ch = container.clientHeight;

        let bx, by;
        if (isNormalized) {
            const renderedWidth = nw * scale.x;
            const renderedHeight = nh * scale.y;
            bx = (xmin + (xmax - xmin) / 2) * renderedWidth + mediaOffset.x;
            by = (ymin + (ymax - ymin) / 2) * renderedHeight + mediaOffset.y;
        } else {
            bx = (xmin + (xmax - xmin) / 2) * scale.x + mediaOffset.x;
            by = (ymin + (ymax - ymin) / 2) * scale.y + mediaOffset.y;
        }

        const targetZoom = 3;
        setZoom(targetZoom);
        setOffset({ x: (cw / 2 - bx) * targetZoom, y: (ch / 2 - by) * targetZoom });
    };

    useImperativeHandle(ref, () => ({
        focusOnBBox
    }));

    const handleWheel = (e: React.WheelEvent) => {
        const delta = e.deltaY > 0 ? 0.95 : 1.05;
        const newZoom = Math.min(Math.max(zoom * delta, 1), 10);
        setZoom(newZoom);
        if (newZoom <= 1) setOffset({ x: 0, y: 0 });
    };

    const handleMouseDown = (e: React.MouseEvent) => {
        if (zoom <= 1) return;
        setIsDragging(true);
        setDragStart({ x: e.clientX - offset.x, y: e.clientY - offset.y });
    };

    const handleMouseMove = (e: React.MouseEvent) => {
        if (!isDragging) return;
        setOffset({ x: e.clientX - dragStart.x, y: e.clientY - dragStart.y });
    };

    const renderBBox = (b: any, i: number) => {
        if (!b.bbox || b.bbox.length < 4) return null;
        
        let [xmin, ymin, xmax, ymax] = b.bbox;
        const isNormalized = Math.max(xmin, ymin, xmax, ymax) <= 1.05;
        
        const el = isVideo ? videoRef?.current : imgRef.current;
        if (!el) return null;
        
        const nw = isVideo ? (el as HTMLVideoElement).videoWidth : (el as HTMLImageElement).naturalWidth;
        const nh = isVideo ? (el as HTMLVideoElement).videoHeight : (el as HTMLImageElement).naturalHeight;

        let w, h, l, t;

        const formatConfidence = (val: any) => {
            if (val === undefined || val === null) return 0;
            const num = parseFloat(val);
            if (isNaN(num)) return 0;
            return num <= 1 ? Math.round(num * 100) : Math.round(num);
        };

        if (isNormalized) {
            const renderedWidth = nw * scale.x;
            const renderedHeight = nh * scale.y;
            w = (xmax - xmin) * renderedWidth;
            h = (ymax - ymin) * renderedHeight;
            l = xmin * renderedWidth + mediaOffset.x;
            t = ymin * renderedHeight + mediaOffset.y;
        } else {
            w = (xmax - xmin) * scale.x;
            h = (ymax - ymin) * scale.y;
            l = xmin * scale.x + mediaOffset.x;
            t = ymin * scale.y + mediaOffset.y;
        }

        const isNearTop = t < 25;
        const labelStyle = isNearTop ? "top-0 rounded-b-sm rounded-t-none" : "-top-[22px] rounded-t-sm";

        const hasPolygon = b.polygon && Array.isArray(b.polygon) && b.polygon.length > 0;

        return (
            <div key={i} 
                onClick={(e) => { e.stopPropagation(); focusOnBBox(b); }}
                className={`absolute transition-all cursor-crosshair group/bbox pointer-events-auto ${
                    hasPolygon 
                        ? "border-none bg-transparent" 
                        : "border-2 border-red-500 bg-red-500/10 hover:bg-red-500/30 shadow-lg shadow-red-500/20"
                }`}
                style={{ left: l, top: t, width: w, height: h }}>
                <div className={`absolute left-0 bg-red-600 text-white text-[9px] px-2 py-0.5 font-black whitespace-nowrap uppercase tracking-tighter shadow-xl transform group-hover/bbox:scale-125 transition-transform origin-left pointer-events-none ${labelStyle}`}>
                    {translateAIClass(b.class)} {formatConfidence(b.confidence)}%
                </div>
            </div>
        );
    };

    return (
        <div 
            ref={containerRef}
            className={`relative w-full h-full flex flex-col items-center justify-center bg-black overflow-hidden transition-all duration-700`}
            onWheel={handleWheel}
        >
            {/* FLOATING CONTROL BAR (v35.0 Glassmorphism) */}
            <div className="absolute top-6 left-6 z-[10000] flex items-center gap-3">
                 <button 
                     onClick={(e) => { e.stopPropagation(); setShowAILayer(!showAILayer); }}
                     className={`flex items-center gap-2 px-5 py-2.5 rounded-full text-[10px] font-black uppercase tracking-widest transition-all border shadow-2xl ${showAILayer ? 'bg-emerald-500 border-emerald-400 text-white' : 'bg-slate-800 border-white/10 text-slate-400'}`}
                 >
                     <Cpu className="w-3.5 h-3.5" />
                     AI LAYER {showAILayer ? 'ON' : 'OFF'}
                 </button>
                 <button 
                     onClick={(e) => { e.stopPropagation(); setShowRoadMask(!showRoadMask); }}
                     className={`flex items-center gap-2 px-5 py-2.5 rounded-full text-[10px] font-black uppercase tracking-widest transition-all border shadow-2xl ${showRoadMask ? 'bg-blue-500 border-blue-400 text-white' : 'bg-slate-800 border-white/10 text-slate-400'}`}
                 >
                     <Route className="w-3.5 h-3.5" />
                     LÀN ĐƯỜNG {showRoadMask ? 'ON' : 'OFF'}
                 </button>
            </div>
            <div className="absolute top-6 right-6 z-[10000] flex items-center gap-3">
                <div className="flex items-center gap-2 p-2 bg-black/40 backdrop-blur-2xl rounded-2xl border border-white/10 shadow-2xl">
                    <button onClick={() => setZoom(z => Math.max(z - 1, 1))} className="p-2.5 hover:bg-white/10 rounded-xl transition-all text-white active:scale-90"><X className="w-4 h-4 rotate-45" /></button>
                    <div className="h-5 w-[1px] bg-white/10" />
                    <input 
                        type="range" min="1" max="10" step="0.5" 
                        value={zoom} onChange={(e) => setZoom(parseFloat(e.target.value))}
                        className="w-32 h-1 bg-white/20 rounded-full appearance-none cursor-pointer accent-blue-500"
                    />
                    <div className="h-5 w-[1px] bg-white/10" />
                    <button onClick={() => setZoom(z => Math.min(z + 1, 10))} className="p-2.5 hover:bg-white/10 rounded-xl transition-all text-white active:scale-90"><PlusCircle className="w-4 h-4" /></button>
                    <button onClick={() => { setZoom(1); setOffset({x:0,y:0}); }} className="px-4 py-2 bg-blue-600 text-white text-[10px] font-black rounded-xl hover:bg-blue-500 transition-all uppercase tracking-widest ml-1 active:scale-95 shadow-lg shadow-blue-600/30">Reset</button>
                </div>
                <button onClick={toggleFullScreen} className="p-4 bg-white/10 hover:bg-white/20 text-white rounded-2xl backdrop-blur-2xl border border-white/10 transition-all active:scale-90 group">
                    {isFullScreen ? <X className="w-5 h-5 group-hover:rotate-90 transition-transform" /> : <Maximize2 className="w-5 h-5" />}
                </button>
            </div>

            <div 
                className={`relative w-full h-full flex items-center justify-center transition-all duration-300 ease-[cubic-bezier(0.23, 1, 0.32, 1)] ${isDragging ? 'cursor-grabbing select-none' : zoom > 1 ? 'cursor-grab' : ''}`}
                style={{ transform: `scale(${zoom}) translate(${offset.x / zoom}px, ${offset.y / zoom}px)` }}
                onMouseDown={handleMouseDown}
                onMouseMove={handleMouseMove}
                onMouseUp={() => setIsDragging(false)}
                onMouseLeave={() => setIsDragging(false)}
            >
                {isVideo ? (
                    <>
                        <video 
                            ref={videoRef} 
                            src={src} 
                            className="w-full h-full object-contain pointer-events-auto" 
                            controls={true}
                            controlsList="nofullscreen nodownload"
                            onLoadedMetadata={updateScale} 
                            onWaiting={() => setImgLoaded(false)}
                            onCanPlayThrough={() => setImgLoaded(true)}
                            onPlaying={() => setImgLoaded(true)}
                            preload="metadata"
                            autoPlay 
                        />
                        {!imgLoaded && (
                            <div className="absolute inset-0 flex items-center justify-center bg-black/60 backdrop-blur-sm z-[50]">
                                <Loader2 className="w-12 h-12 text-blue-500 animate-spin" />
                                <span className="absolute mt-20 text-white text-xs font-black uppercase tracking-widest">Đang tải luồng dữ liệu...</span>
                            </div>
                        )}
                    </>
                ) : (
                    <img ref={imgRef} src={src} alt="AI Detection" className="w-full h-full object-contain pointer-events-none" onLoad={() => { setImgLoaded(true); updateScale(); }} draggable={false} />
                )}
                
                <div className="absolute inset-0 pointer-events-none z-[100]">
                    {showRoadMask && roadContour && roadContour.length > 0 && (
                        <svg className="absolute inset-0 w-full h-full pointer-events-none">
                            <polygon
                                points={roadContour
                                    .map((p: any) => {
                                        const px = p[0];
                                        const py = p[1];
                                        const sx = px * scale.x + mediaOffset.x;
                                        const sy = py * scale.y + mediaOffset.y;
                                        return `${sx},${sy}`;
                                    })
                                    .join(" ")}
                                className="stroke-blue-500 stroke-2 fill-blue-500/15 pointer-events-none shadow-lg"
                                vectorEffect="non-scaling-stroke"
                            />
                        </svg>
                    )}
                    {showAILayer && (
                        <div className="relative w-full h-full pointer-events-none">
                            <svg className="absolute inset-0 w-full h-full pointer-events-none">
                                {bboxes.map((b: any, idx: number) => {
                                    if (!b.polygon || !Array.isArray(b.polygon) || b.polygon.length === 0) return null;
                                    
                                    const pointsStr = b.polygon
                                        .map((p: any) => {
                                            const px = p[0];
                                            const py = p[1];
                                            let sx, sy;
                                            
                                            const firstPt = b.polygon[0];
                                            const isPolyNormalized = firstPt && Math.max(firstPt[0], firstPt[1]) <= 1.05;
                                            if (isPolyNormalized) {
                                                const el = isVideo ? videoRef?.current : imgRef.current;
                                                if (el) {
                                                    const nw = isVideo ? (el as HTMLVideoElement).videoWidth : (el as HTMLImageElement).naturalWidth;
                                                    const nh = isVideo ? (el as HTMLVideoElement).videoHeight : (el as HTMLImageElement).naturalHeight;
                                                    sx = px * nw * scale.x + mediaOffset.x;
                                                    sy = py * nh * scale.y + mediaOffset.y;
                                                } else {
                                                    sx = 0;
                                                    sy = 0;
                                                }
                                            } else {
                                                sx = px * scale.x + mediaOffset.x;
                                                sy = py * scale.y + mediaOffset.y;
                                            }
                                            return `${sx},${sy}`;
                                        })
                                        .join(" ");
                                        
                                    return (
                                        <polygon
                                            key={idx}
                                            points={pointsStr}
                                            className="stroke-red-500 stroke-2 fill-red-500/25 hover:fill-red-500/45 transition-colors pointer-events-auto cursor-crosshair shadow-lg"
                                            onClick={(e) => { e.stopPropagation(); focusOnBBox(b); }}
                                            vectorEffect="non-scaling-stroke"
                                        />
                                    );
                                })}
                            </svg>
                            {bboxes.map(renderBBox)}
                        </div>
                    )}
                </div>
            </div>
            
            <div className="absolute bottom-6 left-1/2 -translate-x-1/2 z-[1000] flex items-center gap-6 px-8 py-3 bg-white/10 backdrop-blur-3xl border border-white/20 rounded-2xl shadow-2xl transition-all opacity-0 group-hover/viewer:opacity-100 hover:bg-white/20">
                <div className="flex flex-col items-center">
                    <p className="text-[8px] text-white/60 font-black uppercase tracking-widest mb-0.5">Phát hiện</p>
                    <p className="text-xl font-black text-white leading-none">{bboxes.length}</p>
                </div>
                <div className="w-[1px] h-6 bg-white/20" />
                <div className="flex flex-col items-center">
                    <p className="text-[8px] text-white/60 font-black uppercase tracking-widest mb-0.5">Tỉ lệ tin cậy</p>
                    <p className="text-xl font-black text-emerald-400 leading-none">
                        {bboxes.length > 0 ? (bboxes.reduce((acc, b) => {
                            let val = b.confidence || 0;
                            return acc + (val <= 1 ? val * 100 : val);
                        }, 0) / bboxes.length).toFixed(0) : 0}%
                    </p>
                </div>
            </div>
        </div>
    );
  }
);
