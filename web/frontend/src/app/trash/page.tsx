'use client';

import { useState, useEffect, useCallback } from 'react';
import { Trash2, RefreshCw, XCircle, Search, Calendar, Database, Loader2, AlertTriangle, Shield, MapPin, ClipboardList } from 'lucide-react';
import { trashAPI, crackAPI } from '@/lib/api';

interface TrashItem {
  item_id: string;
  item_name: string;
  item_type: string;
  deleted_by: string;
  deleted_at: string;
  original_collection: string;
  data?: any;
}

const TAB_CONFIG: { key: string; label: string; icon: typeof Trash2 }[] = [
  { key: 'digital-twin', label: 'Quét AI & Bản sao 3D', icon: Database },
  { key: 'incidents', label: 'Sự cố / Hư hỏng', icon: AlertTriangle },
  { key: 'surveys', label: 'Đợt khảo sát', icon: ClipboardList },
];

export default function TrashPage() {
  const [trashItems, setTrashItems] = useState<TrashItem[]>([]);
  const [activeTab, setActiveTab] = useState('digital-twin');
  const [searchTerm, setSearchTerm] = useState('');
  const [loading, setLoading] = useState(false);
  const [actionLoading, setActionLoading] = useState<string | null>(null);

  const fetchTrash = useCallback(async () => {
    setLoading(true);
    try {
      // Lấy từ API (MongoDB trash collection)
      const { data } = await trashAPI.getAll();
      const apiItems: TrashItem[] = data?.trash || [];

      // Merge với localStorage (backward compatible cho digital-twin cũ)
      const localItems: TrashItem[] = JSON.parse(localStorage.getItem('trash_items') || '[]').map((item: any) => ({
        item_id: item.id,
        item_name: item.name,
        item_type: item.type || 'digital-twin',
        deleted_by: 'localStorage',
        deleted_at: item.deleted_at,
        original_collection: 'local',
        data: item.data,
      }));

      // Gộp, ưu tiên API
      const apiIds = new Set(apiItems.map(i => i.item_id));
      const merged = [...apiItems, ...localItems.filter(l => !apiIds.has(l.item_id))];
      setTrashItems(merged);
    } catch (err) {
      // Fallback: chỉ dùng localStorage
      const localItems = JSON.parse(localStorage.getItem('trash_items') || '[]').map((item: any) => ({
        item_id: item.id,
        item_name: item.name,
        item_type: item.type || 'digital-twin',
        deleted_by: 'localStorage',
        deleted_at: item.deleted_at,
        original_collection: 'local',
        data: item.data,
      }));
      setTrashItems(localItems);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchTrash(); }, [fetchTrash]);

  const handleRestore = async (item: TrashItem) => {
    setActionLoading(item.item_id);
    try {
      if (item.original_collection === 'local') {
        // localStorage item → chỉ xóa khỏi localStorage
        const local = JSON.parse(localStorage.getItem('trash_items') || '[]');
        localStorage.setItem('trash_items', JSON.stringify(local.filter((l: any) => l.id !== item.item_id)));
      } else {
        // API item → gọi restore endpoint
        await trashAPI.restore(item.item_id);
      }
      setTrashItems(prev => prev.filter(i => i.item_id !== item.item_id));
    } catch (err) {
      alert('Khôi phục thất bại. Vui lòng thử lại.');
    } finally {
      setActionLoading(null);
    }
  };

  const handlePermanentDelete = async (item: TrashItem) => {
    if (!confirm('Hành động này sẽ XÓA VĨNH VIỄN dữ liệu khỏi hệ thống. Bạn có chắc chắn?')) return;
    setActionLoading(item.item_id);
    try {
      if (item.original_collection === 'local') {
        if (item.item_type === 'digital-twin') {
          // Soft delete first to put it in MongoDB trash, then call permanentDelete to clean up files and DB
          await crackAPI.deleteTask(item.item_id).catch(() => {});
          await trashAPI.permanentDelete(item.item_id).catch(() => {});
        }
        const local = JSON.parse(localStorage.getItem('trash_items') || '[]');
        localStorage.setItem('trash_items', JSON.stringify(local.filter((l: any) => l.id !== item.item_id)));
      } else {
        await trashAPI.permanentDelete(item.item_id);
      }
      setTrashItems(prev => prev.filter(i => i.item_id !== item.item_id));
    } catch (err) {
      alert('Xóa thất bại. Vui lòng thử lại.');
    } finally {
      setActionLoading(null);
    }
  };

  const handleEmptyTrash = async () => {
    if (!confirm(`Xóa vĩnh viễn toàn bộ thùng rác mục "${TAB_CONFIG.find(t => t.key === activeTab)?.label}"?`)) return;
    setLoading(true);
    try {
      await trashAPI.empty(activeTab);
      // Xóa localStorage cho digital-twin
      if (activeTab === 'digital-twin') {
        const local = JSON.parse(localStorage.getItem('trash_items') || '[]');
        // Dọn dẹp cả DB & tệp vật lý cho các item trong localStorage
        for (const item of local) {
          if (item.type === 'digital-twin' || !item.type) {
            await crackAPI.deleteTask(item.id).catch(() => {});
            await trashAPI.permanentDelete(item.id).catch(() => {});
          }
        }
        localStorage.setItem('trash_items', JSON.stringify(local.filter((l: any) => l.type !== 'digital-twin')));
      }
      setTrashItems(prev => prev.filter(i => i.item_type !== activeTab));
    } catch (err) {
      alert('Làm sạch thất bại.');
    } finally {
      setLoading(false);
    }
  };

  const filteredItems = trashItems.filter(item =>
    item.item_type === activeTab &&
    (item.item_name?.toLowerCase() || '').includes(searchTerm.toLowerCase())
  );

  const getTabCount = (tabKey: string) => trashItems.filter(i => i.item_type === tabKey).length;

  const getItemIcon = (type: string) => {
    if (type === 'incidents') return <AlertTriangle className="w-5 h-5 text-amber-500" />;
    if (type === 'surveys') return <ClipboardList className="w-5 h-5 text-blue-500" />;
    return <Database className="w-5 h-5 text-slate-400" />;
  };

  const getItemDetails = (item: TrashItem) => {
    const d = item.data || {};
    if (item.item_type === 'incidents') {
      return [
        d.severity && `Mức độ: ${d.severity}`,
        d.address && `Vị trí: ${d.address}`,
        d.classification && `Phân loại: ${d.classification}`,
      ].filter(Boolean).join(' • ');
    }
    if (item.item_type === 'surveys') {
      return [
        d.route_name && `Tuyến: ${d.route_name}`,
        d.route_km_start && d.route_km_end && `Km${d.route_km_start}–Km${d.route_km_end}`,
        d.task_count && `${d.task_count} tasks`,
      ].filter(Boolean).join(' • ');
    }
    return d.jobId ? `Job: ${d.jobId}` : '';
  };

  return (
    <div className="flex flex-col h-[calc(100vh-var(--topbar-height)-3rem)] bg-slate-50 p-6 rounded-2xl border border-slate-200">
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-xl font-black tracking-tight text-slate-800 flex items-center gap-3">
            <div className="p-3 bg-red-100 text-red-600 rounded-2xl">
              <Trash2 className="w-8 h-8" />
            </div>
            Thùng rác hệ thống
          </h1>
          <p className="text-xs text-slate-500 mt-2 font-medium">
            Quản lý dữ liệu đã xoá — có thể khôi phục hoặc xóa vĩnh viễn
          </p>
        </div>

        <div className="flex items-center gap-4">
          <div className="relative">
            <input
              type="text"
              placeholder="Tìm kiếm dữ liệu xoá..."
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              className="pl-10 pr-4 py-2.5 rounded-xl border border-slate-200 w-64 focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 transition-all text-sm"
            />
            <Search className="w-4 h-4 text-slate-400 absolute left-3.5 top-1/2 -translate-y-1/2" />
          </div>
          <button
            onClick={fetchTrash}
            disabled={loading}
            className="p-2.5 rounded-xl border border-slate-200 text-slate-500 hover:bg-slate-100 transition-colors"
          >
            <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
          </button>
          <button
            onClick={handleEmptyTrash}
            disabled={filteredItems.length === 0 || loading}
            className="px-5 py-2.5 bg-red-50 text-red-600 font-bold rounded-xl hover:bg-red-100 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            Làm sạch thùng rác
          </button>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex gap-2 p-1.5 bg-white border border-slate-200 rounded-xl mb-6 w-max">
        {TAB_CONFIG.map(tab => {
          const count = getTabCount(tab.key);
          return (
            <button
              key={tab.key}
              onClick={() => setActiveTab(tab.key)}
              className={`px-5 py-2 text-sm font-bold rounded-lg transition-all flex items-center gap-2 ${
                activeTab === tab.key
                  ? 'bg-blue-50 text-blue-600 shadow-sm'
                  : 'text-slate-500 hover:text-slate-800 hover:bg-slate-50'
              }`}
            >
              <tab.icon className="w-4 h-4" />
              {tab.label}
              {count > 0 && (
                <span className={`px-1.5 py-0.5 rounded-full text-[10px] font-bold ${
                  activeTab === tab.key ? 'bg-blue-100 text-blue-700' : 'bg-slate-100 text-slate-500'
                }`}>
                  {count}
                </span>
              )}
            </button>
          );
        })}
      </div>

      {/* List */}
      <div className="flex-1 bg-white border border-slate-200 rounded-2xl shadow-sm overflow-hidden flex flex-col">
        {loading ? (
          <div className="flex-1 flex flex-col items-center justify-center text-slate-400 p-8">
            <Loader2 className="w-10 h-10 animate-spin text-blue-500 mb-4" />
            <p className="text-sm font-medium">Đang tải thùng rác...</p>
          </div>
        ) : filteredItems.length === 0 ? (
          <div className="flex-1 flex flex-col items-center justify-center text-slate-400 p-8 text-center">
            <Database className="w-16 h-16 mb-4 text-slate-200" />
            <p className="text-lg font-bold text-slate-500">Thùng rác trống</p>
            <p className="text-sm mt-1">Chưa có dữ liệu nào bị xoá trong mục này.</p>
          </div>
        ) : (
          <div className="overflow-y-auto w-full p-4 space-y-3">
            {filteredItems.map(item => (
              <div key={item.item_id} className="flex items-center justify-between p-5 rounded-xl border border-slate-100 hover:border-slate-200 hover:shadow-md transition-all group bg-slate-50/50">
                <div className="flex items-start gap-4">
                  <div className="w-12 h-12 bg-slate-100 rounded-lg flex items-center justify-center shrink-0">
                    {getItemIcon(item.item_type)}
                  </div>
                  <div>
                    <h3 className="text-base font-bold text-slate-800 leading-tight mb-1">{item.item_name}</h3>
                    <div className="flex items-center gap-3 text-xs text-slate-500 font-medium mb-0.5">
                      <span className="flex items-center gap-1.5">
                        <Calendar className="w-3.5 h-3.5" />
                        Xoá lúc: {new Date(item.deleted_at).toLocaleString('vi-VN')}
                      </span>
                      {item.deleted_by && item.deleted_by !== 'localStorage' && (
                        <span>Bởi: {item.deleted_by}</span>
                      )}
                      <span>ID: {item.item_id}</span>
                    </div>
                    {getItemDetails(item) && (
                      <p className="text-[11px] text-slate-400 mt-0.5">{getItemDetails(item)}</p>
                    )}
                  </div>
                </div>

                <div className="flex items-center gap-2 opacity-0 group-hover:opacity-100 transition-opacity">
                  <button
                    onClick={() => handleRestore(item)}
                    disabled={actionLoading === item.item_id}
                    className="px-4 py-2 bg-emerald-50 text-emerald-600 hover:bg-emerald-500 hover:text-white font-bold rounded-lg transition-colors flex items-center gap-2 disabled:opacity-50"
                  >
                    {actionLoading === item.item_id ? (
                      <Loader2 className="w-4 h-4 animate-spin" />
                    ) : (
                      <RefreshCw className="w-4 h-4" />
                    )}
                    Khôi phục
                  </button>
                  <button
                    onClick={() => handlePermanentDelete(item)}
                    disabled={actionLoading === item.item_id}
                    className="px-4 py-2 bg-slate-100 text-slate-500 hover:bg-red-500 hover:text-white font-bold rounded-lg transition-colors flex items-center gap-2 disabled:opacity-50"
                  >
                    <XCircle className="w-4 h-4" />
                    Xoá vĩnh viễn
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
