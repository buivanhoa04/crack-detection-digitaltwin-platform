import { useState } from 'react';
import { Box, CheckCircle2, Clock, Map, Activity, Edit2, Check, X, ChevronLeft, ChevronRight, Trash2, User } from 'lucide-react';
import { ProjectModel } from '../types';

interface ModelListSidebarProps {
  projects: ProjectModel[];
  selectedProjectId: string | null;
  onSelectProject: (id: string) => void;
  onEditProject: (id: string, newName: string) => void;
  onDeleteProject: (id: string) => void;
  isCollapsed: boolean;
  onToggleCollapse: () => void;
}

export function ModelListSidebar({
  projects,
  selectedProjectId,
  onSelectProject,
  onEditProject,
  onDeleteProject,
  isCollapsed,
  onToggleCollapse,
}: ModelListSidebarProps) {
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editValue, setEditValue] = useState('');

  const handleStartEdit = (e: React.MouseEvent, project: ProjectModel) => {
    e.stopPropagation();
    setEditingId(project.id);
    setEditValue(project.surveyName || project.name);
  };

  const handleSaveEdit = (e: React.MouseEvent, id: string) => {
    e.stopPropagation();
    if (editValue.trim()) {
      onEditProject(id, editValue.trim());
    }
    setEditingId(null);
  };

  const handleCancelEdit = (e: React.MouseEvent) => {
    e.stopPropagation();
    setEditingId(null);
  };

  return (
    <div className="glass-card flex flex-col h-full overflow-hidden border border-slate-200 shadow-sm w-full text-slate-800">
      {isCollapsed ? (
        <div className="flex flex-col items-center h-full py-4 w-full">
          <button
            onClick={onToggleCollapse}
            className="p-1.5 rounded-lg bg-slate-50 text-slate-600 hover:bg-slate-100 transition-colors mb-4"
            title="Mở rộng danh sách"
          >
            <ChevronRight className="w-4 h-4" />
          </button>
          <div className="w-full flex-1 overflow-y-auto px-2 space-y-2 flex flex-col items-center custom-scrollbar">
            {projects.map((project) => {
              const isSelected = selectedProjectId === project.id;
              const displayName = project.surveyName || project.name;
              return (
                <button
                  key={project.id}
                  onClick={() => onSelectProject(project.id)}
                  className={`w-10 h-10 rounded-xl flex items-center justify-center border transition-all duration-200 relative group ${
                    isSelected
                      ? 'bg-blue-50 border-blue-300 text-blue-600 shadow-sm'
                      : 'bg-white border-transparent text-slate-600 hover:bg-slate-50'
                  }`}
                  title={displayName}
                >
                  <span className="font-semibold text-xs">
                    {displayName.charAt(0).toUpperCase()}
                  </span>
                  
                  {/* Status Dot Overlay */}
                  <span className="absolute bottom-1 right-1 block w-2 h-2 rounded-full ring-1 ring-white">
                    {project.status === 'completed' ? (
                      <span className="absolute inset-0 rounded-full bg-emerald-500" />
                    ) : project.status === 'processing' ? (
                      <span className="absolute inset-0 rounded-full bg-amber-500 animate-pulse" />
                    ) : (
                      <span className="absolute inset-0 rounded-full bg-slate-400" />
                    )}
                  </span>

                  {/* Floating Tooltip */}
                  <div className="absolute left-14 bg-slate-900 text-white text-xs py-1.5 px-3 rounded-lg opacity-0 pointer-events-none group-hover:opacity-100 transition-opacity whitespace-nowrap z-50 shadow-md">
                    {displayName}
                  </div>
                </button>
              );
            })}
          </div>
        </div>
      ) : (
        <div className="flex flex-col h-full overflow-hidden w-full">
          <div className="p-4 border-b border-slate-200 flex items-center justify-between">
            <h3 className="text-sm font-semibold text-slate-800 flex items-center gap-2">
              <Map className="w-4 h-4 text-blue-500" />
              Đợt Khảo sát AI 3D/4D
            </h3>
            <button
              onClick={onToggleCollapse}
              className="p-1.5 rounded-lg bg-slate-50 text-slate-600 hover:bg-slate-100 transition-colors"
              title="Thu nhỏ danh sách"
            >
              <ChevronLeft className="w-4 h-4" />
            </button>
          </div>
          
          <div className="flex-1 overflow-y-auto p-2 space-y-2 custom-scrollbar">
            {projects.map((project) => {
              const isSelected = selectedProjectId === project.id;
              const isEditing = editingId === project.id;
              const displayName = project.surveyName || project.name;

              return (
                <div
                  key={project.id}
                  onClick={() => !isEditing && onSelectProject(project.id)}
                  className={`w-full p-3 rounded-xl border transition-all duration-200 relative group ${
                    isEditing ? 'cursor-default border-blue-300 bg-blue-50/20' : 'cursor-pointer'
                  } ${
                    isSelected && !isEditing
                      ? 'bg-blue-50 border-blue-200 shadow-sm'
                      : !isEditing ? 'bg-white border-transparent hover:bg-slate-50' : ''
                  }`}
                >
                  {isEditing ? (
                    <div className="flex items-center gap-1.5">
                      <input
                        type="text"
                        value={editValue}
                        onChange={(e) => setEditValue(e.target.value)}
                        className="flex-1 text-xs bg-white border border-slate-300 rounded px-2 py-1 focus:outline-none focus:border-blue-500"
                        autoFocus
                        onClick={(e) => e.stopPropagation()}
                      />
                      <button
                        onClick={(e) => handleSaveEdit(e, project.id)}
                        className="p-1 rounded bg-emerald-50 text-emerald-600 hover:bg-emerald-100 transition-colors"
                        title="Lưu"
                      >
                        <Check className="w-3.5 h-3.5" />
                      </button>
                      <button
                        onClick={handleCancelEdit}
                        className="p-1 rounded bg-red-50 text-red-600 hover:bg-red-100 transition-colors"
                        title="Hủy"
                      >
                        <X className="w-3.5 h-3.5" />
                      </button>
                    </div>
                  ) : (
                    <>
                      <div className="flex items-start justify-between mb-1.5">
                        <div className="font-bold text-xs text-slate-800 leading-tight pr-6 group-hover:pr-14 transition-all">
                          {displayName}
                        </div>
                        
                        {/* Hover actions */}
                        <div className="absolute right-3 top-3 opacity-0 group-hover:opacity-100 flex items-center gap-1 transition-opacity">
                          {/* Nút sửa */}
                          <button
                            onClick={(e) => handleStartEdit(e, project)}
                            className="p-1 rounded text-slate-400 hover:bg-white hover:text-blue-600 transition-colors shadow-sm bg-slate-50"
                            title="Sửa tên"
                          >
                            <Edit2 className="w-3 h-3" />
                          </button>
                          
                          {/* Nút xoá */}
                          <button
                            onClick={(e) => {
                              e.stopPropagation();
                              if (confirm(`Bạn có chắc muốn đẩy "${displayName}" vào thùng rác?`)) {
                                onDeleteProject(project.id);
                              }
                            }}
                            className="p-1 rounded text-slate-400 hover:bg-red-50 hover:text-red-500 transition-colors shadow-sm bg-slate-50"
                            title="Đẩy vào thùng rác"
                          >
                            <Trash2 className="w-3 h-3" />
                          </button>
                        </div>

                        {!isEditing && (
                          <div className="shrink-0 pt-0.5">
                            {project.status === 'completed' ? (
                              <CheckCircle2 className="w-3.5 h-3.5 text-emerald-500" />
                            ) : project.status === 'processing' ? (
                              <Activity className="w-3.5 h-3.5 text-amber-500 shrink-0" />
                            ) : (
                              <Box className="w-3.5 h-3.5 text-slate-400 shrink-0" />
                            )}
                          </div>
                        )}
                      </div>

                      {/* Survey Details Panel inside list card */}
                      {project.routeName ? (
                        <div className="space-y-1 mt-2 text-[10px] text-slate-500 bg-slate-50/50 p-2 rounded-lg border border-slate-100">
                          <div className="flex justify-between">
                            <span className="font-semibold text-slate-400">Tuyến đường:</span>
                            <span className="font-bold text-slate-700">{project.routeName}</span>
                          </div>
                          <div className="flex justify-between">
                            <span className="font-semibold text-slate-400">Lý trình:</span>
                            <span className="font-bold text-slate-700">Km{project.routeKmStart} - Km{project.routeKmEnd}</span>
                          </div>
                          {project.surveyor && (
                            <div className="flex justify-between items-center pt-0.5 border-t border-slate-200/50 mt-1">
                              <span className="flex items-center gap-1 text-slate-400"><User className="w-2.5 h-2.5" /> Khảo sát:</span>
                              <span className="font-bold text-slate-700">{project.surveyor}</span>
                            </div>
                          )}
                        </div>
                      ) : (
                        <div className="flex items-center justify-between text-[10px] text-slate-500 mt-2">
                          <span className="flex items-center gap-1">
                            <Clock className="w-3 h-3" />
                            {project.date}
                          </span>
                          <span className="italic text-[9px] text-slate-400">Bản sao 3D lẻ</span>
                        </div>
                      )}
                    </>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
