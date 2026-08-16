'use client';

import { useState, useEffect } from 'react';
import { useAuth } from '@/hooks/useAuth';
import { auditAPI } from '@/lib/api';
import type { AuditLogEntry, PaginatedResponse } from '@/types';
import {
  ClipboardList,
  Search,
  Filter,
  User,
  Clock,
  Activity,
  Globe,
  Loader2,
  ChevronLeft,
  ChevronRight,
} from 'lucide-react';
import { format } from 'date-fns';

export default function AuditLogPage() {
  const { user } = useAuth();
  const isAdmin = user?.role === 'admin';

  const [logs, setLogs] = useState<PaginatedResponse<AuditLogEntry> | null>(null);
  const [loading, setLoading] = useState(true);
  const [actionTypes, setActionTypes] = useState<string[]>([]);
  
  // Filters
  const [page, setPage] = useState(1);
  const [filterAction, setFilterAction] = useState('');
  const [filterUser, setFilterUser] = useState('');

  useEffect(() => {
    const fetchActionTypes = async () => {
      try {
        const { data } = await auditAPI.getActionTypes();
        setActionTypes(data.actions || []);
      } catch (err) {
        console.error('Error fetching action types:', err);
      }
    };
    fetchActionTypes();
  }, []);

  useEffect(() => {
    const fetchLogs = async () => {
      setLoading(true);
      try {
        const { data } = await auditAPI.getLogs({
          page,
          limit: 20,
          action: filterAction || undefined,
          user: filterUser || undefined,
        });
        setLogs(data);
      } catch (err) {
        console.error('Error fetching logs:', err);
      } finally {
        setLoading(false);
      }
    };

    // Debounce the fetch when typing in user filter
    const timeout = setTimeout(() => {
      fetchLogs();
    }, 300);

    return () => clearTimeout(timeout);
  }, [page, filterAction, filterUser]);

  const formatAction = (action: string) => {
    const actionMap: Record<string, { label: string, color: string }> = {
      login: { label: 'Đăng nhập', color: 'text-blue-400' },
      create_user: { label: 'Tạo User', color: 'text-emerald-400' },
      update_user: { label: 'Sửa User', color: 'text-yellow-400' },
      delete_user: { label: 'Xóa User', color: 'text-red-400' },
      upload_doc: { label: 'Tải tài liệu', color: 'text-indigo-400' },
      create_incident: { label: 'Báo lỗi', color: 'text-orange-400' },
    };
    
    if (actionMap[action]) {
       return <span className={`font-semibold ${actionMap[action].color}`}>{actionMap[action].label}</span>;
    }
    
    return <span className="font-semibold text-slate-300">{action.replace('_', ' ').toUpperCase()}</span>;
  };

  return (
    <div className="space-y-6 max-w-7xl mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-white mb-1 flex items-center gap-3">
            <ClipboardList className="w-6 h-6 text-blue-500" />
            Nhật ký Hoạt động
          </h1>
          <p className="text-slate-400 text-sm">
            Theo dõi thao tác của tất cả người dùng trong hệ thống (Audit Log).
          </p>
        </div>
      </div>

      {/* Filters */}
      <div className="bg-slate-900 border border-white/[0.05] p-4 rounded-2xl flex flex-wrap gap-4 items-center shadow-lg">
        <div className="flex-1 min-w-[200px] relative">
          <Search className="w-4 h-4 text-slate-500 absolute left-3 top-1/2 -translate-y-1/2" />
          <input
            type="text"
            placeholder="Tìm theo email người dùng..."
            value={filterUser}
            onChange={(e) => {
              setFilterUser(e.target.value);
              setPage(1); // Reset page on filter
            }}
            className="w-full bg-slate-800 border-none rounded-xl pl-10 pr-4 py-2.5 text-sm text-white placeholder:text-slate-500 focus:ring-2 focus:ring-blue-500 transition"
          />
        </div>
        
        <div className="w-[200px] relative">
           <Filter className="w-4 h-4 text-slate-500 absolute left-3 top-1/2 -translate-y-1/2" />
           <select
             value={filterAction}
             onChange={(e) => {
               setFilterAction(e.target.value);
               setPage(1);
             }}
             className="w-full bg-slate-800 border-none rounded-xl pl-10 pr-4 py-2.5 text-sm text-white focus:ring-2 focus:ring-blue-500 appearance-none"
           >
             <option value="">Tất cả thao tác</option>
             {actionTypes.map(type => (
               <option key={type} value={type}>{type.replace('_', ' ').toUpperCase()}</option>
             ))}
           </select>
        </div>
      </div>

      {/* Log Table */}
      <div className="bg-slate-900 border border-white/[0.05] rounded-2xl overflow-hidden shadow-xl">
        <div className="overflow-x-auto">
          <table className="w-full text-left border-collapse">
            <thead>
              <tr className="border-b border-white/[0.05] text-[11px] uppercase tracking-wider text-slate-500 bg-slate-800/50">
                <th className="px-6 py-4 font-semibold">Tài khoản</th>
                <th className="px-6 py-4 font-semibold">Hành động</th>
                <th className="px-6 py-4 font-semibold">Mục tiêu</th>
                <th className="px-6 py-4 font-semibold">Chi tiết</th>
                <th className="px-6 py-4 font-semibold">IP Address</th>
                <th className="px-6 py-4 font-semibold text-right">Thời gian</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-white/[0.05]">
              {loading ? (
                <tr>
                  <td colSpan={6} className="px-6 py-20 text-center">
                    <Loader2 className="w-6 h-6 text-slate-500 animate-spin mx-auto" />
                  </td>
                </tr>
              ) : logs?.items.length === 0 ? (
                 <tr>
                  <td colSpan={6} className="px-6 py-20 text-center text-slate-500">
                    Không tìm thấy nhật ký hoạt động nào phù hợp.
                  </td>
                </tr>
              ) : (
                logs?.items.map((log) => (
                  <tr key={log.id} className="hover:bg-white/[0.02] transition-colors text-sm">
                    <td className="px-6 py-4">
                      <div className="flex items-center gap-2 text-slate-200">
                        <User className="w-4 h-4 text-slate-500" />
                        {log.user_email}
                      </div>
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap">
                       {formatAction(log.action)}
                    </td>
                    <td className="px-6 py-4">
                      <span className="px-2 py-1 rounded bg-slate-800 text-slate-300 font-mono text-xs">
                        {log.target || '-'}
                      </span>
                    </td>
                    <td className="px-6 py-4 text-slate-400">
                      {log.details || '-'}
                    </td>
                    <td className="px-6 py-4 text-slate-500 whitespace-nowrap flex items-center gap-2">
                      <Globe className="w-3.5 h-3.5" />
                      {log.ip_address || 'Local'}
                    </td>
                    <td className="px-6 py-4 text-right text-slate-400 whitespace-nowrap">
                       <div className="flex items-center justify-end gap-2">
                          <Clock className="w-3.5 h-3.5" />
                          {format(new Date(log.timestamp), 'HH:mm:ss dd/MM/yyyy')}
                       </div>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>

        {/* Pagination */}
        {logs && logs.total_pages > 1 && (
          <div className="p-4 border-t border-white/[0.05] flex items-center justify-between bg-slate-800/30">
            <span className="text-sm text-slate-500">
              Trang <span className="font-semibold text-slate-300">{logs.page}</span> / {logs.total_pages}
              <span className="mx-2">•</span>
              Tổng: {logs.total} bản ghi
            </span>
            <div className="flex items-center gap-2">
              <button
                onClick={() => setPage(p => Math.max(1, p - 1))}
                disabled={logs.page === 1}
                className="p-1.5 rounded-lg border border-slate-700 text-slate-400 hover:text-white hover:bg-slate-700 disabled:opacity-50 disabled:cursor-not-allowed"
              >
                <ChevronLeft className="w-5 h-5" />
              </button>
              <button
                onClick={() => setPage(p => Math.min(logs.total_pages, p + 1))}
                disabled={logs.page === logs.total_pages}
                className="p-1.5 rounded-lg border border-slate-700 text-slate-400 hover:text-white hover:bg-slate-700 disabled:opacity-50 disabled:cursor-not-allowed"
              >
                <ChevronRight className="w-5 h-5" />
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
