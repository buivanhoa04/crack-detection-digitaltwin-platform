'use client';

import { useState } from 'react';
import { useAuth } from '@/hooks/useAuth';
import { authAPI, usersAPI } from '@/lib/api';
import { UserCircle, Shield, Key, Loader2, LogOut, Mail, Clock } from 'lucide-react';
import { format } from 'date-fns';

export default function ProfilePage() {
  const { user, logout } = useAuth();
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');

  // Password Form
  const [currentPassword, setCurrentPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');

  if (!user) return null;

  const handlePasswordChange = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    setMessage('');

    if (newPassword !== confirmPassword) {
      setError('Mật khẩu mới không khớp!');
      return;
    }

    if (newPassword.length < 6) {
      setError('Mật khẩu mới phải từ 6 ký tự trở lên.');
      return;
    }

    try {
      setLoading(true);
      await usersAPI.changeMyPassword(currentPassword, newPassword);
      setMessage('Đổi mật khẩu thành công! Yêu cầu đăng nhập lại.');
      setCurrentPassword('');
      setNewPassword('');
      setConfirmPassword('');
      
      // Force logout after 2 secs
      setTimeout(() => {
        logout();
      }, 2000);
      
    } catch (err: any) {
      setError(err.response?.data?.detail || 'Thao tác thất bại. Vui lòng kiểm tra lại mật khẩu cũ.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="max-w-4xl mx-auto space-y-6">
      <h1 className="text-xl font-bold text-white mb-6">Hồ sơ Cá nhân</h1>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
        
        {/* Profile Card */}
        <div className="md:col-span-1 bg-slate-900 border border-white/[0.05] rounded-2xl p-6 shadow-xl flex flex-col items-center">
          <div className="w-24 h-24 rounded-full bg-gradient-to-br from-blue-500 to-purple-600 flex items-center justify-center mb-4 shadow-lg">
            <span className="text-2xl font-bold text-white uppercase">
              {user.full_name.charAt(0)}
            </span>
          </div>
          <h2 className="text-lg font-bold text-white mb-1">{user.full_name}</h2>
          
          <span className={`px-3 py-1 rounded-full text-xs font-bold uppercase tracking-wider mb-6 flex items-center gap-1.5 ${
            user.role === 'admin' 
              ? 'bg-purple-500/20 text-purple-400 border border-purple-500/20' 
              : 'bg-blue-500/20 text-blue-400 border border-blue-500/20'
          }`}>
             {user.role === 'admin' && <Shield className="w-3.5 h-3.5" />}
             {user.role}
          </span>

          <div className="w-full space-y-4">
             <div className="flex flex-col">
                <span className="text-xs text-slate-500 font-semibold uppercase mb-1 flex items-center gap-2"><Mail className="w-3.5 h-3.5"/> Email</span>
                <span className="text-sm text-slate-300 bg-slate-800/50 p-2.5 rounded-lg border border-white/5">{user.email}</span>
             </div>
             {user.last_login && (
             <div className="flex flex-col">
                <span className="text-xs text-slate-500 font-semibold uppercase mb-1 flex items-center gap-2"><Clock className="w-3.5 h-3.5"/> Đăng nhập lần cuối</span>
                <span className="text-sm text-slate-300 bg-slate-800/50 p-2.5 rounded-lg border border-white/5">
                   {format(new Date(user.last_login), 'HH:mm dd/MM/yyyy')}
                </span>
             </div>
             )}
          </div>

          <button
            onClick={logout}
            className="mt-8 w-full py-2.5 rounded-xl border border-red-500/20 text-red-400 hover:bg-red-500/10 font-medium transition flex items-center justify-center gap-2"
          >
            <LogOut className="w-4 h-4" />
            Đăng xuất
          </button>
        </div>

        {/* Change Password Form */}
        <div className="md:col-span-2 bg-slate-900 border border-white/[0.05] rounded-2xl shadow-xl overflow-hidden flex flex-col">
          <div className="p-6 border-b border-white/[0.05] bg-slate-800/30">
            <h3 className="text-base font-bold text-white flex items-center gap-2">
              <Key className="w-5 h-5 text-blue-400" /> Đổi Mật khẩu
            </h3>
            <p className="text-sm text-slate-500 mt-1">Đảm bảo tài khoản của bạn đang sử dụng mật khẩu mạnh.</p>
          </div>
          
          <form onSubmit={handlePasswordChange} className="p-6 space-y-5 flex-1">
            {error && (
              <div className="p-3 bg-red-500/10 border border-red-500/20 rounded-xl text-red-400 text-sm">
                {error}
              </div>
            )}
            {message && (
              <div className="p-3 bg-emerald-500/10 border border-emerald-500/20 rounded-xl text-emerald-400 text-sm">
                {message}
              </div>
            )}

            <div className="space-y-4 max-w-md">
                <div>
                    <label className="block text-sm font-medium text-slate-300 mb-1.5">Mật khẩu hiện tại</label>
                    <input
                        type="password"
                        required
                        value={currentPassword}
                        onChange={(e) => setCurrentPassword(e.target.value)}
                        className="w-full bg-slate-800 border border-slate-700 rounded-xl px-4 py-2.5 text-white placeholder:text-slate-500 focus:outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500 transition"
                    />
                </div>
                <div>
                    <label className="block text-sm font-medium text-slate-300 mb-1.5">Mật khẩu mới</label>
                    <input
                        type="password"
                        required
                        value={newPassword}
                        onChange={(e) => setNewPassword(e.target.value)}
                        className="w-full bg-slate-800 border border-slate-700 rounded-xl px-4 py-2.5 text-white placeholder:text-slate-500 focus:outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500 transition"
                    />
                </div>
                <div>
                    <label className="block text-sm font-medium text-slate-300 mb-1.5">Xác nhận mật khẩu mới</label>
                    <input
                        type="password"
                        required
                        value={confirmPassword}
                        onChange={(e) => setConfirmPassword(e.target.value)}
                        className="w-full bg-slate-800 border border-slate-700 rounded-xl px-4 py-2.5 text-white placeholder:text-slate-500 focus:outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500 transition"
                    />
                </div>
            </div>

            <div className="pt-4">
                <button
                    type="submit"
                    disabled={loading}
                    className="px-6 py-2.5 bg-blue-600 hover:bg-blue-500 text-white font-medium rounded-xl transition flex items-center justify-center gap-2 disabled:opacity-50"
                >
                    {loading && <Loader2 className="w-4 h-4 animate-spin" />}
                    Cập nhật mật khẩu
                </button>
            </div>
          </form>
        </div>

      </div>
    </div>
  );
}
