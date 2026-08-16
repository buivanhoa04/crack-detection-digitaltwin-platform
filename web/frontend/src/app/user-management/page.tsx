'use client';

import { useState, useEffect } from 'react';
import { useAuth } from '@/hooks/useAuth';
import { usersAPI } from '@/lib/api';
import type { User } from '@/types';
import {
  Users,
  Shield,
  User as UserIcon,
  CheckCircle2,
  XCircle,
  MoreVertical,
  Plus,
  Loader2,
  Trash2,
  Lock,
} from 'lucide-react';
import { format } from 'date-fns';

export default function UserManagementPage() {
  const { user: currentUser } = useAuth();
  const isAdmin = currentUser?.role === 'admin';

  const [users, setUsers] = useState<User[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  
  // Custom Modal
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [formData, setFormData] = useState({
    email: '',
    password: '',
    full_name: '',
    role: 'user',
  });
  const [saving, setSaving] = useState(false);

  const fetchUsers = async () => {
    try {
      setLoading(true);
      const { data } = await usersAPI.getAll();
      setUsers(data.users || []);
    } catch (err: any) {
      setError(err.response?.data?.detail || 'Lỗi tải danh sách người dùng');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (isAdmin) fetchUsers();
  }, [isAdmin]);

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    setSaving(true);
    setError('');
    try {
      await usersAPI.create(formData);
      setIsModalOpen(false);
      setFormData({ email: '', password: '', full_name: '', role: 'user' });
      fetchUsers();
    } catch (err: any) {
      setError(err.response?.data?.detail || 'Không thể tạo người dùng');
    } finally {
      setSaving(false);
    }
  };

  const handleToggle = async (userId: string) => {
    try {
      await usersAPI.toggleActive(userId);
      fetchUsers();
    } catch (err: any) {
      alert(err.response?.data?.detail || 'Lỗi vô hiệu hóa user');
    }
  };

  const handleDelete = async (userId: string) => {
    if (!confirm('Bạn có chắc chắn muốn xóa vĩnh viễn user này?')) return;
    try {
      await usersAPI.delete(userId);
      fetchUsers();
    } catch (err: any) {
      alert(err.response?.data?.detail || 'Lỗi xóa user');
    }
  };

  if (!isAdmin) {
    return <div className="p-10 text-center text-slate-500">Không có quyền truy cập</div>;
  }

  return (
    <div className="space-y-6 max-w-7xl mx-auto pb-10">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-slate-800 mb-1 flex items-center gap-3">
            <Users className="w-6 h-6 text-blue-500" />
            Quản lý Người dùng
          </h1>
          <p className="text-slate-500 text-sm">
             Cấp tài khoản, phân quyền thao tác trên hệ thống.
          </p>
        </div>
        <button
          onClick={() => setIsModalOpen(true)}
          className="btn-gradient flex items-center gap-2 shadow-lg shadow-blue-500/20 bg-blue-600 text-white px-4 py-2 rounded-xl hover:bg-blue-700 transition"
        >
          <Plus className="w-5 h-5" /> Thêm Tài khoản
        </button>
      </div>

      {error && <div className="p-4 bg-red-50 text-red-600 border border-red-200 rounded-xl">{error}</div>}

      <div className="bg-white border border-slate-200 rounded-2xl overflow-hidden shadow-sm">
        <div className="overflow-x-auto">
          <table className="w-full text-left border-collapse">
            <thead>
              <tr className="border-b border-slate-100 text-[11px] uppercase tracking-wider text-slate-500 bg-slate-50/50">
                <th className="px-6 py-4 font-semibold">Tên / Email</th>
                <th className="px-6 py-4 font-semibold">Vai trò</th>
                <th className="px-6 py-4 font-semibold whitespace-nowrap">Đăng nhập lần cuối</th>
                <th className="px-6 py-4 font-semibold text-center">Trạng thái</th>
                <th className="px-6 py-4 font-semibold text-right">Thao tác</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {loading ? (
                <tr>
                   <td colSpan={5} className="py-10 text-center"><Loader2 className="w-6 h-6 animate-spin text-slate-400 mx-auto" /></td>
                </tr>
              ) : users.map(u => (
                <tr key={u.id} className="hover:bg-slate-50/50 transition-colors">
                  <td className="px-6 py-4">
                    <div className="flex items-center gap-3">
                      <div className="w-10 h-10 rounded-full bg-slate-100 flex items-center justify-center shrink-0">
                         {u.role === 'admin' ? <Shield className="w-5 h-5 text-purple-500" /> : <UserIcon className="w-5 h-5 text-blue-500" />}
                      </div>
                      <div>
                        <p className="font-semibold text-slate-800">{u.full_name}</p>
                        <p className="text-xs text-slate-500">{u.email}</p>
                      </div>
                    </div>
                  </td>
                  <td className="px-6 py-4">
                    <span className={`px-2.5 py-1 rounded-full text-xs font-bold uppercase ${
                       u.role === 'admin' ? 'bg-purple-50 text-purple-600 border border-purple-200' : 'bg-blue-50 text-blue-600 border border-blue-200'
                    }`}>
                       {u.role}
                    </span>
                  </td>
                  <td className="px-6 py-4 text-xs text-slate-500 whitespace-nowrap">
                    {u.last_login ? format(new Date(u.last_login), 'HH:mm dd/MM/yyyy') : 'Chưa đăng nhập'}
                  </td>
                  <td className="px-6 py-4 text-center">
                    <button
                        onClick={() => handleToggle(u.id)}
                        disabled={u.id === currentUser?.id}
                        className={`inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-semibold border transition ${
                           u.is_active ? 'bg-emerald-50 text-emerald-600 border-emerald-200 hover:bg-emerald-100' : 'bg-slate-100 text-slate-600 border-slate-200 hover:bg-slate-200'
                        } disabled:opacity-50`}
                    >
                       {u.is_active ? <CheckCircle2 className="w-3.5 h-3.5" /> : <XCircle className="w-3.5 h-3.5" />}
                       {u.is_active ? 'Đang hoạt động' : 'Vô hiệu hóa'}
                    </button>
                  </td>
                  <td className="px-6 py-4 text-right">
                    <div className="flex justify-end gap-2 text-slate-400">
                        <button 
                            onClick={() => {
                               const newPw = prompt('Nhập mật khẩu mới cho user này:');
                               if(newPw && newPw.length >= 6) {
                                   usersAPI.resetPassword(u.id, newPw)
                                     .then(() => alert('Đã reset mật khẩu'))
                                     .catch(e => alert(e.response?.data?.detail));
                               } else if (newPw) {
                                   alert('Mật khẩu quá ngắn');
                               }
                            }}
                            className="p-1.5 hover:text-slate-800 hover:bg-slate-100 rounded transition" title="Reset Mật khẩu">
                            <Lock className="w-4 h-4" />
                        </button>
                        <button onClick={() => handleDelete(u.id)} disabled={u.id === currentUser?.id} className="p-1.5 hover:text-red-600 hover:bg-red-50 rounded transition disabled:opacity-50" title="Xóa tài khoản">
                            <Trash2 className="w-4 h-4" />
                        </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Create Modal */}
      {isModalOpen && (
        <div className="fixed inset-0 bg-slate-900/60 backdrop-blur-sm flex items-center justify-center z-[500] animate-fade-in px-4">
            <div className="bg-white border border-slate-200 rounded-3xl p-6 w-full max-w-md shadow-2xl scale-100 transition-all relative overflow-hidden">
               <div className="absolute top-0 left-0 w-full h-[3px] bg-gradient-to-r from-blue-500 to-indigo-600" />
               <button onClick={() => setIsModalOpen(false)} className="absolute top-4 right-4 p-2 hover:bg-slate-100 rounded-full text-slate-400 transition-colors"><XCircle className="w-5 h-5"/></button>
               <h2 className="text-lg font-bold text-slate-800 mb-6">Thêm Tài khoản mới</h2>
               <form onSubmit={handleCreate} className="space-y-4">
                  <div>
                     <label className="block text-sm font-medium text-slate-600 mb-1.5">Họ và Tên</label>
                     <input type="text" required value={formData.full_name} onChange={e => setFormData({...formData, full_name: e.target.value})} className="w-full bg-slate-50 border border-slate-200 rounded-xl px-4 py-2.5 text-slate-800 focus:bg-white transition-all outline-none" />
                  </div>
                  <div>
                     <label className="block text-sm font-medium text-slate-600 mb-1.5">Email</label>
                     <input type="email" required value={formData.email} onChange={e => setFormData({...formData, email: e.target.value})} className="w-full bg-slate-50 border border-slate-200 rounded-xl px-4 py-2.5 text-slate-800 focus:bg-white transition-all outline-none" />
                  </div>
                  <div>
                     <label className="block text-sm font-medium text-slate-600 mb-1.5">Mật khẩu (tối thiểu 6 ký tự)</label>
                     <input type="password" required value={formData.password} onChange={e => setFormData({...formData, password: e.target.value})} className="w-full bg-slate-50 border border-slate-200 rounded-xl px-4 py-2.5 text-slate-800 focus:bg-white transition-all outline-none" />
                  </div>
                  <div>
                     <label className="block text-sm font-medium text-slate-600 mb-1.5">Vai trò hệ thống</label>
                     <div className="grid grid-cols-2 gap-3">
                         <button type="button" onClick={() => setFormData({...formData, role: 'user'})} className={`py-2.5 rounded-xl text-sm font-semibold border transition ${formData.role === 'user' ? 'bg-blue-50 text-blue-600 border-blue-200' : 'bg-slate-50 text-slate-500 border-slate-200 hover:bg-slate-100'}`}>
                            Người dùng thường
                         </button>
                         <button type="button" onClick={() => setFormData({...formData, role: 'admin'})} className={`py-2.5 rounded-xl text-sm font-semibold border transition ${formData.role === 'admin' ? 'bg-purple-50 text-purple-600 border-purple-200' : 'bg-slate-50 text-slate-500 border-slate-200 hover:bg-slate-100'}`}>
                            Quản trị viên
                         </button>
                     </div>
                  </div>

                  <div className="flex justify-end gap-3 pt-6 border-t border-slate-100 mt-6">
                     <button type="button" onClick={() => setIsModalOpen(false)} className="px-5 py-2.5 rounded-xl font-medium text-slate-500 hover:bg-slate-100 transition">
                        Hủy
                     </button>
                     <button type="submit" disabled={saving} className="bg-blue-600 text-white px-5 py-2.5 rounded-xl font-medium flex items-center gap-2 shadow-lg shadow-blue-500/20 active:scale-95 transition-transform">
                        {saving && <Loader2 className="w-4 h-4 animate-spin" />}
                        Tạo tài khoản
                     </button>
                  </div>
               </form>
            </div>
        </div>
      )}
    </div>
  );
}
