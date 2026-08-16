'use client';

import React, { useState, useEffect } from 'react';
import { 
  Folder, 
  ChevronRight, 
  Calendar, 
  Image as ImageIcon, 
  XCircle, 
  Search, 
  ArrowLeft,
  Loader2,
  Trash2,
  CheckCircle,
  Bot,
  Play
} from 'lucide-react';
import { archiveAPI } from '@/lib/api';
import { translateAIClass } from '@/lib/translate';
import { withAccessToken } from '@/lib/mediaAuth';

interface ArchiveBrowserProps {
  onApproveClick: (task: any, frameIndex: number | null) => void;
  onRefreshTrigger?: number;
}

export default function ArchiveBrowser({
  onApproveClick,
  onRefreshTrigger = 0
}: ArchiveBrowserProps) {
  const [categoryFilter, setCategoryFilter] = useState<'all' | 'road' | 'bridge'>('all');
  const [expandedYears, setExpandedYears] = useState<string[]>([]);
  const [expandedMonths, setExpandedMonths] = useState<string[]>([]);
  const [tree, setTree] = useState<any>({});
  const [loading, setLoading] = useState(true);
  const [snapshots, setSnapshots] = useState<any[]>([]);
  const [activeDate, setActiveDate] = useState<{year: string; month: string; day: string} | null>(null);
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
  const [viewMode, setViewMode] = useState<'list' | 'files'>('list');
  const [individualBBoxVisibility, setIndividualBBoxVisibility] = useState<Record<number, boolean>>({});
  const [naturalSizes, setNaturalSizes] = useState<Record<string, { width: number, height: number }>>({});

  const getDefectCount = (task: any) => {
    let count = 0;
    if (task.best_frames) {
      task.best_frames.forEach((frame: any) => {
        if (frame.detections) {
          frame.detections.forEach((det: any) => {
            const cls = det.class || det.raw_class_name || '';
            if (cls && cls !== 'road' && cls !== 'bridge' && cls !== 'unknown') {
              count++;
            }
          });
        }
      });
    }
    if (count === 0 && typeof task.total_detections === 'number' && task.total_detections > 0) {
      return task.total_detections;
    }
    return count;
  };

  useEffect(() => {
    fetchTree();
    fetchAllPending();
  }, [onRefreshTrigger]);

  const toggleYear = (year: string) => {
    setExpandedYears(prev => prev.includes(year) ? prev.filter(y => y !== year) : [...prev, year]);
  };

  const toggleMonth = (monthKey: string) => {
    setExpandedMonths(prev => prev.includes(monthKey) ? prev.filter(m => m !== monthKey) : [...prev, monthKey]);
  };

  const fetchTree = async () => {
    setLoading(true);
    try {
      const { data } = await archiveAPI.getTree();
      if (data && !data.detail) {
        setTree(data);
        const years = Object.keys(data).sort().reverse();
        if (years.length > 0) {
          const latestYear = years[0];
          setExpandedYears([latestYear]);
          
          const months = Object.keys(data[latestYear]).sort().reverse();
          if (months.length > 0) {
            const latestMonth = months[0];
            const monthKey = `${latestYear}-${latestMonth}`;
            setExpandedMonths([monthKey]);
            
            const days = Object.keys(data[latestYear][latestMonth]).sort().reverse();
            if (days.length > 0) {
              const latestDay = days[0];
              browseDay(latestYear, latestMonth, latestDay);
            }
          }
        }
      }
    } catch (e) {
      console.error("Failed to fetch archive tree", e);
    } finally {
      setLoading(false);
    }
  };

  const fetchAllPending = async () => {
    setLoading(true);
    try {
      const { data } = await archiveAPI.getAllSnapshots('pending');
      setSnapshots(data?.tasks || []);
      setActiveDate(null);
      setViewMode('list');
    } catch (e) {
      console.error("Failed to fetch all snapshots", e);
    } finally {
      setLoading(false);
    }
  };

  const browseDay = async (year: string, month: string, day: string) => {
    setLoading(true);
    setActiveDate({ year, month, day });
    setSelectedTaskId(null);
    setViewMode('files');
    try {
      const { data } = await archiveAPI.getSnapshots(year, month, day);
      setSnapshots(data.tasks || []);
    } catch (e) {
      console.error("Failed to fetch snapshots", e);
    } finally {
      setLoading(false);
    }
  };

  const getImageUrl = (framePath: string) => {
    if (!framePath) return '';
    const cleanPath = framePath
      .replace(/\\/g, '/')
      .replace(/^(\/)?api\/v1\/files\//, '')
      .replace(/^(\/)?files\//, '')
      .replace(/^\//, '');
    return withAccessToken(`/api/v1/files/${cleanPath}`);
  };
  const handleFrameAction = async (taskId: string, frameIdx: number, newStatus: string) => {
    try {
      setSnapshots(prev => prev.map(task => {
        if (task.task_id.toLowerCase() === taskId.toLowerCase() || `task_${task.task_id.toLowerCase()}` === taskId.toLowerCase()) {
          const newFrames = [...(task.best_frames || [])];
          if (newFrames[frameIdx]) {
            newFrames[frameIdx] = { ...newFrames[frameIdx], status: newStatus };
          }
          return { ...task, best_frames: newFrames };
        }
        return task;
      }));

      await archiveAPI.postAction('/snapshot/action', {
        task_id: taskId,
        frame_index: frameIdx,
        status: newStatus
      });
    } catch (e) {
      console.error(e);
    }
  };
  const handleApproveClean = async (taskId: string) => {
    try {
      await archiveAPI.approve(taskId, 'approved');
      setSnapshots(prev => prev.filter(t => t.task_id !== taskId));
      if (selectedTaskId === taskId) {
        setSelectedTaskId(null);
      }
    } catch (e) {
      console.error("Lỗi duyệt task an toàn", e);
    }
  };

  const handleReject = async (taskId: string) => {
    if (!confirm("⚠️ CẢNH BÁO: Bạn có chắc chắn muốn xóa vĩnh viễn dữ liệu của tác vụ này?")) return;
    try {
      await archiveAPI.deleteTask(taskId);
      setSnapshots(prev => prev.filter(s => s.task_id !== taskId));
      if (selectedTaskId === taskId) setSelectedTaskId(null);
    } catch (e) {
      alert("Lỗi khi xóa tác vụ");
    }
  };

  const handleDeleteTask = async (taskId: string) => {
    if (!confirm("⚠️ CẢNH BÁO: Bạn có chắc chắn muốn xóa vĩnh viễn dữ liệu của tác vụ này?")) return;
    try {
      await archiveAPI.deleteTask(taskId);
      setSnapshots(prev => prev.filter(s => s.task_id !== taskId));
      if (selectedTaskId === taskId) setSelectedTaskId(null);
    } catch (e) {
      alert("Lỗi khi xóa tác vụ");
    }
  };

  const filteredSnapshots = snapshots.filter(task => {
    if (categoryFilter === 'all') return true;
    return task.infrastructure_category === categoryFilter;
  });

  const selectedTaskData = selectedTaskId 
    ? snapshots.find(s => 
        s.task_id.toLowerCase() === selectedTaskId.toLowerCase() || 
        s.task_id.toLowerCase() === `task_${selectedTaskId.toLowerCase()}`
      ) 
    : null;

  // Flatten tree to a list of days sorted newest to oldest
  const daysList: { year: string; month: string; day: string; tasks: any[] }[] = [];
  Object.keys(tree).forEach(year => {
    Object.keys(tree[year]).forEach(month => {
      Object.keys(tree[year][month]).forEach(day => {
        daysList.push({
          year,
          month,
          day,
          tasks: tree[year][month][day] || []
        });
      });
    });
  });
  daysList.sort((a, b) => {
    const dateA = new Date(parseInt(a.year, 10), parseInt(a.month, 10) - 1, parseInt(a.day, 10));
    const dateB = new Date(parseInt(b.year, 10), parseInt(b.month, 10) - 1, parseInt(b.day, 10));
    return dateB.getTime() - dateA.getTime();
  });

  const activeDayTasks = activeDate ? (tree[activeDate.year]?.[activeDate.month]?.[activeDate.day] || []) : [];

  return (
    <div className="flex flex-1 gap-4 min-h-0 text-slate-800">
      {/* ── Center Content: List or Detail Grid ─────────────────────────── */}
      <div className="flex-1 bg-white border border-slate-100 rounded-xl shadow-sm flex flex-col overflow-hidden">
        {/* Toolbar Header */}
        <div className="p-4 border-b border-slate-100 bg-slate-50/30 flex items-center justify-between shrink-0">
          <div>
            <div className="text-[9px] font-bold text-slate-400 uppercase tracking-widest">
              {activeDate ? `KẾT QUẢ NGÀY ${activeDate.day}/${activeDate.month}/${activeDate.year}` : 'TẤT CẢ SỰ CỐ CHỜ DUYỆT'}
            </div>
            <h2 className="text-sm font-bold text-slate-800">
              {selectedTaskId ? 'Chi tiết sự cố' : 'Kho lưu trữ AI'}
            </h2>
          </div>
          
          <div className="flex items-center gap-2">
            {!selectedTaskId && (
              <select
                value={activeDate ? `${activeDate.year}-${activeDate.month}-${activeDate.day}` : 'pending'}
                onChange={(e) => {
                  const val = e.target.value;
                  if (val === 'pending') {
                    fetchAllPending();
                  } else {
                    const [year, month, day] = val.split('-');
                    browseDay(year, month, day);
                  }
                }}
                className="text-xs bg-slate-100 border border-slate-200 rounded-lg px-3 py-1.5 cursor-pointer hover:bg-slate-200 transition-colors font-bold text-slate-700 focus:outline-none"
              >
                <option value="pending">📂 Sự cố chờ duyệt (Tất cả)</option>
                {daysList.map(d => (
                  <option key={`${d.year}-${d.month}-${d.day}`} value={`${d.year}-${d.month}-${d.day}`}>
                    📅 Ngày {d.day}/{d.month}/{d.year} ({d.tasks.length} tác vụ)
                  </option>
                ))}
              </select>
            )}

            <div className="flex bg-slate-100 p-1 rounded-lg">
              {(['all', 'road', 'bridge'] as const).map((cat) => (
                  <button
                    key={cat}
                    onClick={() => setCategoryFilter(cat)}
                    className={`px-3 py-1 rounded-md text-[9px] font-bold uppercase tracking-wider transition-all ${categoryFilter === cat ? 'bg-white text-blue-600 shadow-sm' : 'text-slate-400 hover:text-slate-600'}`}
                  >
                    {cat === 'all' ? 'Tất cả' : cat === 'road' ? 'Đường' : 'Cầu'}
                  </button>
              ))}
            </div>
            {selectedTaskId && (
              <button 
                onClick={() => setSelectedTaskId(null)}
                className="flex items-center gap-1.5 text-[9px] font-bold text-slate-600 hover:text-blue-600 uppercase px-3 py-2 rounded-lg bg-slate-100 hover:bg-blue-50 transition-colors border border-slate-200"
              >
                <ArrowLeft className="w-3.5 h-3.5" /> Quay lại
              </button>
            )}
          </div>
        </div>

        {/* Content Body */}
        <div className="flex-1 overflow-y-auto p-4 custom-scrollbar bg-slate-50/20">
          {loading ? (
             <div className="h-full flex flex-col items-center justify-center space-y-2 opacity-50">
                <Loader2 className="w-8 h-8 text-blue-500 animate-spin" />
                <p className="text-xs font-bold text-slate-400 uppercase tracking-widest">Đang tải dữ liệu...</p>
             </div>
          ) : filteredSnapshots.length === 0 ? (
             <div className="h-full flex flex-col items-center justify-center opacity-40 grayscale py-12">
                <Search className="w-12 h-12 text-slate-300 mb-2" />
                <p className="text-xs font-bold text-slate-400 uppercase tracking-wider">Không tìm thấy dữ liệu phù hợp</p>
             </div>
          ) : (
             /* 🌟 Grouped Data Table View */
             <div className="flex flex-col gap-6 animate-fade-in pb-10">
               {Object.entries(
                 filteredSnapshots.reduce((acc, task) => {
                   const route = task.route_name || "Khác";
                   if (!acc[route]) acc[route] = [];
                   acc[route].push(task);
                   return acc;
                 }, {} as Record<string, typeof filteredSnapshots>)
               ).map(([route, routeTasks]: [string, any]) => (
                 <div key={route} className="bg-white rounded-xl shadow-sm border border-slate-200 overflow-hidden">
                   <div className="bg-slate-50 border-b border-slate-200 px-4 py-3 flex items-center justify-between">
                     <div className="flex items-center gap-2">
                       <div className="w-2 h-4 bg-blue-500 rounded-sm"></div>
                       <h3 className="text-xs font-bold text-slate-800 uppercase tracking-wide">
                         Tuyến: {route}
                       </h3>
                     </div>
                     <span className="text-[10px] font-bold text-slate-500 bg-white px-2 py-1 rounded-md border border-slate-200 shadow-sm">
                       {routeTasks.length} Đợt khảo sát
                     </span>
                   </div>
                   
                   <div className="overflow-x-auto">
                     <table className="w-full text-left border-collapse">
                       <thead>
                         <tr className="bg-white border-b border-slate-100 text-[10px] text-slate-400 font-bold uppercase tracking-wider">
                           <th className="px-4 py-3 font-semibold">Tên đợt / File</th>
                           <th className="px-4 py-3 font-semibold">Lần quét</th>
                           <th className="px-4 py-3 font-semibold">Phân loại</th>
                           <th className="px-4 py-3 font-semibold text-center">Số lượng ảnh</th>
                           <th className="px-4 py-3 font-semibold text-center">Số lượng hư hại</th>
                           <th className="px-4 py-3 font-semibold">Ngày tạo</th>
                           <th className="px-4 py-3 font-semibold text-right">Thao tác</th>
                         </tr>
                       </thead>
                       <tbody className="text-xs divide-y divide-slate-50">
                         {routeTasks.map((task: any) => {
                           const frames = task.best_frames || [];
                           const dateObj = task.created_at ? new Date(task.created_at) : null;
                           const dateStr = dateObj ? `${dateObj.getDate()}/${dateObj.getMonth() + 1}/${dateObj.getFullYear()}` : "N/A";
                           
                           return (
                             <tr 
                               key={task.task_id} 
                               onClick={() => onApproveClick(task, 0)}
                               className="hover:bg-blue-50/50 transition-colors cursor-pointer group"
                             >
                               <td className="px-4 py-3">
                                 <div className="flex flex-col">
                                   <span className="font-bold text-slate-700 group-hover:text-blue-600 transition-colors">
                                     {task.survey_name || 'Nhiệm vụ kiểm định'}
                                   </span>
                                   <span className="text-[9px] text-slate-400 truncate max-w-[200px]" title={task.filename}>
                                     {task.filename}
                                   </span>
                                 </div>
                               </td>
                               <td className="px-4 py-3">
                                 <span className="inline-flex items-center justify-center px-2 py-0.5 rounded-full bg-indigo-50 text-indigo-600 font-bold text-[9px] border border-indigo-100">
                                   Lần {task.iteration || 1}
                                 </span>
                               </td>
                               <td className="px-4 py-3">
                                 <span className={`px-2 py-0.5 rounded text-[9px] font-bold text-white uppercase ${task.infrastructure_category === 'bridge' ? 'bg-purple-600' : 'bg-blue-600'}`}>
                                   {task.infrastructure_category === 'bridge' ? 'CẦU' : 'ĐƯỜNG'}
                                 </span>
                               </td>
                               <td className="px-4 py-3 text-center">
                                 <span className="font-bold text-slate-600 bg-slate-100 px-2 py-0.5 rounded-md">
                                   {frames.length}
                                 </span>
                               </td>
                               <td className="px-4 py-3 text-center">
                                 <span className={`font-bold px-2.5 py-0.5 rounded-md text-[10px] ${
                                   getDefectCount(task) > 0 
                                     ? 'bg-rose-50 border border-rose-200 text-rose-600' 
                                     : 'bg-emerald-50 border border-emerald-200 text-emerald-600'
                                 }`}>
                                   {getDefectCount(task)}
                                 </span>
                               </td>
                               <td className="px-4 py-3 text-slate-500 font-medium">
                                 {dateStr}
                               </td>
                               <td className="px-4 py-3 text-right">
                                 <div className="flex items-center justify-end gap-2">
                                   <button 
                                     onClick={(e) => { e.stopPropagation(); onApproveClick(task, 0); }}
                                     className="px-3 py-1.5 rounded-md bg-blue-600 hover:bg-blue-700 text-white text-[10px] font-bold shadow-sm transition-all active:scale-95"
                                   >
                                     Soát Duyệt
                                   </button>
                                   <button 
                                     onClick={(e) => { e.stopPropagation(); handleReject(task.task_id); }}
                                     className="p-1.5 rounded-md bg-white border border-slate-200 hover:bg-red-50 hover:border-red-200 hover:text-red-600 text-slate-400 transition-all active:scale-95"
                                     title="Từ chối"
                                   >
                                     <XCircle className="w-3.5 h-3.5" />
                                   </button>
                                 </div>
                               </td>
                             </tr>
                           );
                         })}
                       </tbody>
                     </table>
                   </div>
                 </div>
               ))}
             </div>
          )}
        </div>
      </div>
    </div>
  );
}
