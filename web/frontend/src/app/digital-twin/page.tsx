'use client';

import { useState, useEffect, useCallback, useMemo } from 'react';
import { useRouter } from 'next/navigation';
import { Box, Archive, Info, ChevronLeft, ChevronRight } from 'lucide-react';
import { ModelListSidebar } from './components/ModelListSidebar';
import AdaptiveViewer from './components/AdaptiveViewer';
import { AIDefectCatalog } from './components/AIDefectCatalog';
import { TimelineSlider, TimelineScan } from './components/TimelineSlider';
import ArchiveBrowser from '@/components/ArchiveBrowser';
import ApprovalWorkflow from './components/ApprovalWorkflow';
import { ProjectModel, DefectMarkerData } from './types';

import { crackAPI, surveysAPI } from '@/lib/api';

export default function DigitalTwinPage() {
  const router = useRouter();
  const [activeTab, setActiveTab] = useState<'analysis' | 'review'>('review');
  const [projects, setProjects] = useState<ProjectModel[]>([]);
  const [selectedProjectId, setSelectedProjectId] = useState<string | null>(null);
  const [layers, setLayers] = useState({ mesh: true, texture: true });
  const [isSidebarCollapsed, setIsSidebarCollapsed] = useState(false);
  const [isRightSidebarCollapsed, setIsRightSidebarCollapsed] = useState(false);

  // Two-way binding & Detections fallback
  const [focusedTrackId, setFocusedTrackId] = useState<number | null>(null);
  const [defectMarkers, setDefectMarkers] = useState<DefectMarkerData[]>([]);
  const [activeFilters, setActiveFilters] = useState<{ classes: string[]; severities: string[] }>({ classes: [], severities: [] });
  const [selectedTaskData, setSelectedTaskData] = useState<any>(null);
  const [isLoadingTaskData, setIsLoadingTaskData] = useState(false);

  // Timeline
  const [timelineScans, setTimelineScans] = useState<TimelineScan[]>([]);
  const [currentSurveyName, setCurrentSurveyName] = useState('');

  // Approval Workflow Integration
  const [refreshTrigger, setRefreshTrigger] = useState(0);
  const [approvalModal, setApprovalModal] = useState({
    show: false,
    taskId: '',
    frameIndex: null as number | null,
  });

  // Reset defect state when project changes
  useEffect(() => {
    setFocusedTrackId(null);
    setDefectMarkers([]);
    setActiveFilters({ classes: [], severities: [] });
    setSelectedTaskData(null);
  }, [selectedProjectId]);

  // Load project's detailed detections to pass to 2D Fallback Viewer
  useEffect(() => {
    if (!selectedProjectId) return;
    setIsLoadingTaskData(true);
    crackAPI.getStatus(selectedProjectId)
      .then(res => {
        if (res.data) {
          setSelectedTaskData(res.data);
        }
      })
      .catch(err => console.error("Load task detections details failed", err))
      .finally(() => setIsLoadingTaskData(false));
  }, [selectedProjectId]);

  // Load projects from API and map them with Survey information
  const loadProjects = useCallback(() => {
    Promise.all([
      crackAPI.getHistory(1, 100),
      surveysAPI.getAll().catch(() => ({ data: { surveys: [] } }))
    ]).then(([historyRes, surveysRes]) => {
      const data = historyRes.data;
      const surveysList = surveysRes.data?.surveys || [];
      const surveysMap = new Map<string, any>(surveysList.map((s: any) => [s.id, s]));

      if (data && data.tasks) {
        const trash = JSON.parse(localStorage.getItem('trash_items') || '[]');
        const deletedIds = trash.map((t: any) => t.id);
        const renamed = JSON.parse(localStorage.getItem('renamed_projects') || '{}');
        const manualProjects = JSON.parse(localStorage.getItem('manual_projects') || '[]');

        const realProjects: ProjectModel[] = data.tasks
          .filter((t: any) => t.twin_job_id && !deletedIds.includes(t.task_id))
          .map((t: any) => {
            const survey = t.survey_id ? (surveysMap.get(t.survey_id) as any) : null;
            return {
              id: t.task_id,
              name: renamed[t.task_id] || t.filename || 'Dự án không tên',
              jobId: t.twin_job_id,
              date: t.created_at ? t.created_at.split('T')[0] : new Date().toISOString().split('T')[0],
              status: t.status === 'done' ? 'completed' : 'processing',
              surveyId: t.survey_id || undefined,
              
              // Mapped Survey metadata fields
              surveyName: survey ? survey.name : undefined,
              routeName: survey ? survey.route_name : undefined,
              routeKmStart: survey ? survey.route_km_start : undefined,
              routeKmEnd: survey ? survey.route_km_end : undefined,
              surveyor: survey ? survey.surveyor : undefined,
            };
          });

        const activeManualProjects = manualProjects.filter((p: ProjectModel) => !deletedIds.includes(p.id));
        const allProjects = [...activeManualProjects, ...realProjects];
        setProjects(allProjects);
        if (allProjects.length > 0 && !selectedProjectId) {
          setSelectedProjectId(allProjects[0].id);
        }
      }
      }).catch(err => console.error("Error loading projects or surveys", err));
  }, [selectedProjectId]);

  useEffect(() => {
    loadProjects();
  }, [loadProjects]);

  const selectedProject = projects.find((p) => p.id === selectedProjectId);

  // Load timeline when survey changes
  useEffect(() => {
    if (!selectedProject?.surveyId) {
      setTimelineScans([]);
      setCurrentSurveyName('');
      return;
    }

    surveysAPI.get(selectedProject.surveyId).then(({ data }) => {
      if (data?.survey) {
        setCurrentSurveyName(data.survey.name || '');
        const tasks: TimelineScan[] = (data.survey.tasks || [])
          .filter((t: any) => t.twin_job_id)
          .map((t: any) => ({
            task_id: t.task_id,
            filename: t.filename || '',
            created_at: t.created_at || '',
            status: t.status || '',
            twin_job_id: t.twin_job_id,
            route_name: data.survey.route_name || '',
            survey_name: data.survey.name || '',
            route_km_start: data.survey.route_km_start,
            route_km_end: data.survey.route_km_end,
            model_type: t.model_type || data.survey.model_type || 'road',
            surveyor: data.survey.surveyor || '',
          }));
        setTimelineScans(tasks);
      }
    }).catch(() => {
      setTimelineScans([]);
    });
  }, [selectedProject?.surveyId]);

  // Switch scan version
  const handleTimelineScanSelect = useCallback((taskId: string) => {
    const existing = projects.find(p => p.id === taskId);
    if (existing) {
      setSelectedProjectId(taskId);
      return;
    }

    const scan = timelineScans.find(s => s.task_id === taskId);
    if (scan && scan.twin_job_id) {
      const tempProject: ProjectModel = {
        id: scan.task_id,
        name: scan.filename || `Scan ${scan.created_at.split('T')[0]}`,
        jobId: scan.twin_job_id,
        date: scan.created_at.split('T')[0],
        status: scan.status === 'done' ? 'completed' : 'processing',
        surveyId: selectedProject?.surveyId,
      };
      setProjects(prev => {
        if (prev.find(p => p.id === taskId)) return prev;
        return [...prev, tempProject];
      });
      setSelectedProjectId(taskId);
    }
  }, [projects, timelineScans, selectedProject?.surveyId]);



  // Two-way binding
  const handleFocusDefect = useCallback((trackId: number) => { setFocusedTrackId(trackId); }, []);
  const handleMarkerClick = useCallback((trackId: number) => { setFocusedTrackId(trackId); }, []);
  const handleDefectsLoaded = useCallback((markers: DefectMarkerData[]) => { setDefectMarkers(markers); }, []);
  const handleFiltersChange = useCallback((filters: { classes: string[]; severities: string[] }) => { setActiveFilters(filters); }, []);

  // Open Approval Modal
  const handleOpenApproveModal = (task: any, frameIndex: number | null) => {
    setApprovalModal({
      show: true,
      taskId: task.task_id,
      frameIndex: frameIndex,
    });
    setSelectedTaskData(task);
  };

  // Callback when approval is saved successfully
  const handleApprovedCallback = (updatedTask: any, isFrameLevel: boolean) => {
    setRefreshTrigger(prev => prev + 1);
    loadProjects();
    
    // Update local task data cache
    if (selectedProjectId === updatedTask.task_id) {
      setSelectedTaskData(updatedTask);
    }
  };

  // Project CRUD
  const handleEditProjectName = (id: string, newName: string) => {
    setProjects(prev => prev.map(p => p.id === id ? { ...p, name: newName } : p));
    const renamed = JSON.parse(localStorage.getItem('renamed_projects') || '{}');
    renamed[id] = newName;
    localStorage.setItem('renamed_projects', JSON.stringify(renamed));
  };

  const handleDeleteProject = async (id: string) => {
    if (!confirm('Bạn có chắc chắn muốn chuyển dự án này vào thùng rác?')) return;
    try {
      if (id.startsWith('p_')) {
        const manual = JSON.parse(localStorage.getItem('manual_projects') || '[]');
        const projectToDelete = manual.find((p: any) => p.id === id);
        if (projectToDelete) {
          localStorage.setItem('manual_projects', JSON.stringify(manual.filter((p: any) => p.id !== id)));
          
          // Put it in trash_items in localStorage
          const trash = JSON.parse(localStorage.getItem('trash_items') || '[]');
          trash.push({
            id: projectToDelete.id,
            name: projectToDelete.surveyName || projectToDelete.name,
            type: 'digital-twin',
            deleted_at: new Date().toISOString(),
            data: projectToDelete
          });
          localStorage.setItem('trash_items', JSON.stringify(trash));
        }
      } else {
        await crackAPI.deleteTask(id);
      }
      setProjects(prev => prev.filter(p => p.id !== id));
      if (selectedProjectId === id) {
        const remaining = projects.filter(p => p.id !== id);
        setSelectedProjectId(remaining.length > 0 ? remaining[0].id : null);
      }
    } catch (err) {
      console.error("Delete project failed", err);
      alert('Không thể xóa. Vui lòng thử lại.');
    }
  };

  return (
    <div className="flex flex-col h-[calc(100vh-var(--topbar-height)-3rem)] w-full max-w-full min-w-0 overflow-hidden animate-fade-in space-y-4 relative text-slate-800">
      
      {/* Header Panel */}
      <div className="flex items-center justify-between shrink-0">
        <div>
          <h1 className="text-xl font-bold text-slate-800 tracking-tight">Bản sao số Công trình</h1>
          <p className="text-xs text-slate-500 mt-0.5">Quản lý Tài sản Số và Hồ sơ Hiện trạng Không gian 3D/4D</p>
        </div>

        {/* Tab Selector */}
        <div className="flex bg-slate-100 p-1 rounded-xl gap-1 border border-slate-200/50">
          <button
            onClick={() => setActiveTab('review')}
            className={`flex items-center gap-1.5 px-4 py-1.5 rounded-lg text-xs font-bold transition-all ${activeTab === 'review' ? 'bg-white text-blue-600 shadow-sm' : 'text-slate-500 hover:text-slate-700'}`}
          >
            <Archive className="w-4 h-4" />
            Duyệt sự cố
          </button>
          <button
            onClick={() => setActiveTab('analysis')}
            className={`flex items-center gap-1.5 px-4 py-1.5 rounded-lg text-xs font-bold transition-all ${activeTab === 'analysis' ? 'bg-white text-blue-600 shadow-sm' : 'text-slate-500 hover:text-slate-700'}`}
          >
            <Box className="w-4 h-4" />
            Bản sao số 3D
          </button>
        </div>

        <div className="w-10"></div> {/* Balanced offset spacing */}
      </div>

      {/* Main Interactive Screen */}
      <div className="flex-1 min-h-0 w-full min-w-0 flex gap-4 overflow-hidden">
        
        {/* ── CASE 1: 3D / 2D ANALYSIS VIEW ───────────────── */}
        {activeTab === 'analysis' && (
          <>
            {/* Sidebar Left: Project List */}
            <div className={`${isSidebarCollapsed ? 'w-0' : 'w-72'} shrink-0 transition-all duration-300 overflow-hidden`}>
              <ModelListSidebar
                projects={projects} selectedProjectId={selectedProjectId} onSelectProject={setSelectedProjectId}
                onEditProject={handleEditProjectName} onDeleteProject={handleDeleteProject}
                isCollapsed={isSidebarCollapsed} onToggleCollapse={() => setIsSidebarCollapsed(!isSidebarCollapsed)}
              />
            </div>

            {/* Center: Smart Adaptive Viewer */}
            <div className="flex-1 min-w-0 glass-card overflow-hidden border border-slate-200 rounded-xl flex flex-col relative shadow-sm">
              {/* Sleek Edge Handles for Collapsing/Expanding Sidebars */}
              <button
                onClick={() => setIsSidebarCollapsed(!isSidebarCollapsed)}
                className="absolute left-0 top-[40%] -translate-y-1/2 z-20 p-0.5 py-4 rounded-r-xl bg-slate-900/80 hover:bg-slate-900 text-white border-y border-r border-white/10 shadow-lg transition-all hover:pr-1.5 flex items-center justify-center cursor-pointer"
                title={isSidebarCollapsed ? "Mở danh sách dự án" : "Đóng danh sách dự án"}
              >
                {isSidebarCollapsed ? <ChevronRight className="w-3.5 h-3.5" /> : <ChevronLeft className="w-3.5 h-3.5" />}
              </button>

              <button
                onClick={() => setIsRightSidebarCollapsed(!isRightSidebarCollapsed)}
                className="absolute right-0 top-[40%] -translate-y-1/2 z-20 p-0.5 py-4 rounded-l-xl bg-slate-900/80 hover:bg-slate-900 text-white border-y border-l border-white/10 shadow-lg transition-all hover:pl-1.5 flex items-center justify-center cursor-pointer"
                title={isRightSidebarCollapsed ? "Mở danh mục vết nứt" : "Đóng danh mục vết nứt"}
              >
                {isRightSidebarCollapsed ? <ChevronLeft className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
              </button>

              <div className="flex-1 min-w-0 w-full relative">
                {selectedProjectId ? (
                  <AdaptiveViewer
                    jobId={selectedProject?.jobId}
                    layers={layers}
                    defects={defectMarkers}
                    focusTrackId={focusedTrackId}
                    onMarkerClick={handleMarkerClick}
                    activeFilters={activeFilters}
                    taskData={selectedTaskData}
                    isLoadingTask={isLoadingTaskData}
                  />
                ) : (
                  <div className="flex-1 h-full flex items-center justify-center text-slate-500 text-xs">Vui lòng chọn một công trình/tác vụ để hiển thị</div>
                )}
                
                {selectedProject && selectedProject.status === 'processing' && (
                  <div className="absolute inset-0 bg-white/85 backdrop-blur-sm flex flex-col items-center justify-center z-10 text-slate-800">
                    <div className="w-8 h-8 rounded-full border-4 border-blue-500/20 border-t-blue-500 animate-spin mb-3" />
                    <div className="font-semibold text-sm">Mô hình 3D đang được tái tạo...</div>
                    <div className="text-slate-500 text-xs mt-0.5">Job ID: {selectedProject.jobId}</div>
                  </div>
                )}
              </div>

              {/* Timeline Slider */}
              {timelineScans.length > 1 && (
                <TimelineSlider
                  scans={timelineScans}
                  activeTaskId={selectedProjectId}
                  onSelectScan={handleTimelineScanSelect}
                  surveyName={currentSurveyName}
                />
              )}
            </div>

            {/* Sidebar Right: AI Defect Catalog */}
            <div className={`${isRightSidebarCollapsed ? 'w-0' : 'w-80'} shrink-0 flex flex-col overflow-hidden transition-all duration-300`}>
              <AIDefectCatalog
                taskId={selectedProjectId}
                projectName={selectedProject?.surveyName || selectedProject?.name}
                assetType={selectedTaskData?.infrastructure_category || 'road'}
                onFocusDefect={handleFocusDefect}
                focusedTrackId={focusedTrackId}
                onFiltersChange={handleFiltersChange}
                onDefectsLoaded={handleDefectsLoaded}
                isCollapsed={isRightSidebarCollapsed}
                onToggleCollapse={() => setIsRightSidebarCollapsed(!isRightSidebarCollapsed)}
              />
            </div>
          </>
        )}

        {/* ── CASE 2: REVIEW QUEUE (ARCHIVE INTEGRATION) ── */}
        {activeTab === 'review' && (
          <ArchiveBrowser 
            onApproveClick={handleOpenApproveModal}
            onRefreshTrigger={refreshTrigger}
          />
        )}



      </div>

      {/* Global Approval Workflow Modal */}
      {approvalModal.show && selectedTaskData && (
        <ApprovalWorkflow
          show={approvalModal.show}
          taskId={approvalModal.taskId}
          frameIndex={approvalModal.frameIndex}
          taskData={selectedTaskData}
          onClose={() => setApprovalModal(prev => ({ ...prev, show: false }))}
          onApproved={handleApprovedCallback}
        />
      )}
    </div>
  );
}
