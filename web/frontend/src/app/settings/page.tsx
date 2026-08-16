'use client';

import { useState, useEffect } from 'react';
import {
  Settings as SettingsIcon,
  Server,
  Shield,
  Key,
  Save,
  CheckCircle2,
  Eye,
  EyeOff,
  Lock,
  Loader2,
  AlertCircle,
  X,
  Link2
} from 'lucide-react';
import { useAuth } from '@/hooks/useAuth';
import { settingsAPI, healthAPI } from '@/lib/api';

export default function SettingsPage() {
  const { user } = useAuth();
  const [activeTab, setActiveTab] = useState('connections');
  const [config, setConfig] = useState<any>(null);
  const [services, setServices] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Modal & Password State
  const [showConfirmModal, setShowConfirmModal] = useState(false);
  const [adminPassword, setAdminPassword] = useState('');
  const [showKeys, setShowKeys] = useState<Record<string, boolean>>({});

  useEffect(() => {
    loadSettings();
  }, []);

  const loadSettings = async () => {
    try {
      setLoading(true);
      const [settingsRes, healthRes] = await Promise.all([
        settingsAPI.get(),
        healthAPI.getSystemHealth()
      ]);
      setConfig(settingsRes.data);
      if (healthRes.data && healthRes.data.services) {
        setServices(healthRes.data.services);
      }
    } catch (err: any) {
      setError(err.response?.data?.detail || "Không thể tải cấu hình");
    } finally {
      setLoading(false);
    }
  };

  const toggleKey = (key: string) => {
    setShowKeys(prev => ({ ...prev, [key]: !prev[key] }));
  };

  const handleUpdate = async () => {
    if (!adminPassword) {
      alert("Vui lòng nhập mật khẩu Admin để xác nhận thay đổi.");
      return;
    }

    setSaving(true);
    setError(null);
    try {
      const { data: result } = await settingsAPI.update({
        ...config,
        admin_password: adminPassword
      });
      setShowConfirmModal(false);
      setAdminPassword('');
      
      // Show sync status
      const syncStatus = result?.middleware_sync;
      if (syncStatus === 'synced') {
        alert("✅ Cập nhật cấu hình thành công!\n\n🔄 Middleware đã được đồng bộ tự động.");
      } else if (syncStatus && syncStatus !== null) {
        alert(`⚠️ Cấu hình đã lưu, nhưng đồng bộ Middleware gặp sự cố:\n${syncStatus}\n\nHãy kiểm tra Middleware (port 8088) đang chạy.`);
      } else {
        alert("✅ Cập nhật cấu hình thành công!");
      }
      loadSettings();
    } catch (err: any) {
      const msg = err.response?.data?.detail || "Lỗi hệ thống";
      alert(`Thất bại: ${msg}`);
    } finally {
      setSaving(false);
    }
  };

  const tabs = [
    { id: 'connections', label: 'Kết nối Dịch vụ', icon: Server },
    { id: 'security', label: 'Bảo mật & Token', icon: Shield },
    { id: 'general', label: 'Hệ thống', icon: SettingsIcon },
  ];

  const ragflowHealthy = services.some(s => s.service?.includes("RAGFlow") && s.status === 'healthy');
  const crackApiHealthy = services.some(s => s.service?.includes("Crack") && s.status === 'healthy');

  if (loading) return (
    <div className="flex flex-col items-center justify-center h-[60vh]">
        <Loader2 className="w-10 h-10 text-blue-500 animate-spin mb-4" />
        <p className="text-slate-400 animate-pulse">Đang nạp cấu hình hệ thống...</p>
    </div>
  );

  return (
    <div className="space-y-6 animate-fade-in pb-10">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-slate-800 tracking-tight">Cấu hình Kết nối Dịch vụ</h1>
          <p className="text-sm text-slate-500 mt-1">Cấu hình API lõi và các dịch vụ AI tích hợp.</p>
        </div>
        <button
          onClick={() => setShowConfirmModal(true)}
          className="btn-gradient flex items-center gap-2 shadow-lg shadow-blue-500/20"
        >
          <Save className="w-4 h-4" /> Lưu thay đổi
        </button>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Left Columns - Editable Configurations */}
        <div className="lg:col-span-2 space-y-6">
          {/* RAGFlow Section */}
          <div className="glass-card overflow-hidden bg-white border border-slate-200">
            <div className="px-5 py-4 border-b border-slate-100 bg-slate-50/50 flex items-center justify-between">
               <h3 className="text-xs font-bold text-slate-700 flex items-center gap-2 uppercase tracking-wide">
                 <span className="w-2 h-2 rounded-full bg-purple-500 shadow-[0_0_8px_#a855f7]" />
                 Cấu hình RAGFlow ( AI Chatbot )
               </h3>
               <div className={`px-2 py-0.5 rounded-full text-[9px] font-bold uppercase border ${
                 ragflowHealthy 
                   ? "bg-emerald-50 text-emerald-600 border-emerald-200" 
                   : "bg-rose-50 text-rose-600 border-rose-200"
               }`}>
                 {ragflowHealthy ? "Connected" : "Disconnected"}
               </div>
            </div>
            <div className="p-6 space-y-8">
               
               {/* Chat Connection (Client-facing) */}
               <div className="space-y-4">
                  <h4 className="text-[10px] font-bold text-slate-500 uppercase tracking-widest flex items-center gap-2">
                     <Link2 className="w-3 h-3" /> Thông tin Chatbot API
                  </h4>
                  <div className="grid grid-cols-1 gap-4">
                      <div>
                          <label className="text-[11px] font-bold text-slate-600 mb-1.5 block">RAGFlow Base URL (Chat Endpoint)</label>
                          <input 
                            value={config?.ragflow_base_url || ''} 
                            onChange={e => setConfig({...config, ragflow_base_url: e.target.value})}
                            className="input-field font-mono text-xs text-slate-800 bg-slate-50 border border-slate-200 focus:bg-white transition-all" 
                            placeholder="http://host.docker.internal:9380/api/v1/..."
                          />
                      </div>
                      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                          <div>
                            <label className="text-[11px] font-bold text-slate-600 mb-1.5 block">Dataset ID</label>
                            <input 
                                value={config?.dataset_id || ''} 
                                onChange={e => setConfig({...config, dataset_id: e.target.value})}
                                className="input-field font-mono text-xs text-slate-800 bg-slate-50 border border-slate-200 focus:bg-white transition-all" 
                                placeholder="0d2cbca8..."
                            />
                          </div>
                          <div className="relative">
                            <label className="text-[11px] font-bold text-slate-600 mb-1.5 block">RAGFlow API Key</label>
                            <div className="relative group">
                                <input 
                                    type={showKeys['ragflow_key'] ? 'text' : 'password'}
                                    value={config?.ragflow_api_key || ''} 
                                    onChange={e => setConfig({...config, ragflow_api_key: e.target.value})}
                                    className="input-field font-mono text-xs pr-10 text-slate-800 bg-slate-50 border border-slate-200 focus:bg-white transition-all" 
                                    placeholder="ragflow-..."
                                />
                                <button 
                                    onClick={() => toggleKey('ragflow_key')}
                                    className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600 transition-colors"
                                >
                                    {showKeys['ragflow_key'] ? <EyeOff className="w-4 h-4"/> : <Eye className="w-4 h-4"/>}
                                </button>
                            </div>
                          </div>
                      </div>
                  </div>
               </div>

               {/* Middleware Connection (Internal) */}
               <div className="pt-6 border-t border-slate-100 space-y-4">
                  <h4 className="text-[10px] font-bold text-slate-500 uppercase tracking-widest flex items-center gap-2">
                     <Server className="w-3 h-3" /> Kết nối Hệ thống (Middleware)
                  </h4>
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                      <div>
                          <label className="text-[11px] font-bold text-slate-600 mb-1.5 block">Middleware API Endpoint</label>
                          <input 
                            value={config?.ragflow_api_url || ''} 
                            onChange={e => setConfig({...config, ragflow_api_url: e.target.value})}
                            className="input-field font-mono text-xs text-slate-800 bg-slate-50 border border-slate-200 focus:bg-white transition-all" 
                            placeholder="http://host.docker.internal:8088"
                          />
                      </div>
                      <div className="relative">
                          <label className="text-[11px] font-bold text-slate-600 mb-1.5 block">System Auth Token</label>
                          <div className="relative group">
                              <input 
                                  type={showKeys['ragflow_token'] ? 'text' : 'password'}
                                  value={config?.ragflow_api_token || ''} 
                                  onChange={e => setConfig({...config, ragflow_api_token: e.target.value})}
                                  className="input-field font-mono text-xs pr-10 text-slate-800 bg-slate-50 border border-slate-200 focus:bg-white transition-all"
                              />
                              <button 
                                  onClick={() => toggleKey('ragflow_token')}
                                  className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600 transition-colors"
                              >
                                  {showKeys['ragflow_token'] ? <EyeOff className="w-4 h-4"/> : <Eye className="w-4 h-4"/>}
                              </button>
                          </div>
                      </div>
                  </div>
               </div>

            </div>
          </div>

          {/* Crack API Section */}
          <div className="glass-card overflow-hidden bg-white border border-slate-200">
            <div className="px-5 py-4 border-b border-slate-100 bg-slate-50/50 flex items-center justify-between">
               <h3 className="text-xs font-bold text-slate-700 flex items-center gap-2 uppercase tracking-wide">
                 <span className="w-2 h-2 rounded-full bg-blue-500 shadow-[0_0_8px_#3b82f6]" />
                 Cấu hình Crack Detection AI
               </h3>
               <div className={`px-2 py-0.5 rounded-full text-[9px] font-bold uppercase border ${
                 crackApiHealthy 
                   ? "bg-emerald-50 text-emerald-600 border-emerald-200" 
                   : "bg-rose-50 text-rose-600 border-rose-200"
               }`}>
                 {crackApiHealthy ? "Online" : "Offline"}
               </div>
            </div>
            <div className="p-6 grid grid-cols-1 md:grid-cols-2 gap-5">
                <div>
                    <label className="text-[11px] font-bold text-slate-600 mb-1.5 block">Crack API Endpoint</label>
                    <input 
                        value={config?.crack_api_url || ''} 
                        onChange={e => setConfig({...config, crack_api_url: e.target.value})}
                        className="input-field font-mono text-xs text-slate-800 bg-slate-50 border border-slate-200 focus:bg-white transition-all" 
                    />
                </div>
                <div className="relative">
                    <label className="text-[11px] font-bold text-slate-600 mb-1.5 block">API Auth Token</label>
                    <div className="relative group">
                        <input 
                            type={showKeys['crack'] ? 'text' : 'password'}
                            value={config?.crack_api_token || ''} 
                            onChange={e => setConfig({...config, crack_api_token: e.target.value})}
                            className="input-field font-mono text-xs pr-10 text-slate-800 bg-slate-50 border border-slate-200 focus:bg-white transition-all"
                        />
                        <button 
                            onClick={() => toggleKey('crack')}
                            className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600 transition-colors"
                        >
                            {showKeys['crack'] ? <EyeOff className="w-4 h-4"/> : <Eye className="w-4 h-4"/>}
                        </button>
                    </div>
                </div>
            </div>
          </div>
        </div>

        {/* Right Column - Warnings & Notes */}
        <div className="lg:col-span-1 space-y-6">
          <div className="glass-card p-5 bg-white border border-slate-200">
            <h4 className="text-[11px] font-bold text-amber-600 uppercase tracking-wider flex items-center gap-2 mb-3">
              <Shield className="w-4 h-4 text-amber-500" /> Lưu ý Bảo mật
            </h4>
            <p className="text-xs text-slate-600 leading-relaxed">
              Mọi thông tin kết nối API chứa các khoá bí mật (Secret Key). Chỉ những thay đổi được xác thực và thay mặt bởi tài khoản quản trị viên (Admin) mới có hiệu lực trên máy chủ.
            </p>
          </div>
          
          <div className="glass-card p-5 bg-white border border-slate-200">
            <h4 className="text-[11px] font-bold text-slate-700 uppercase tracking-wider flex items-center gap-2 mb-3">
              <SettingsIcon className="w-4 h-4 text-slate-400" /> Phiên bản Hệ thống
            </h4>
            <p className="text-xs text-slate-600 leading-relaxed font-semibold">
              v2.1.0 - Digital Twin Platform
            </p>
            <p className="text-[11px] text-slate-500 mt-1">
              Phần mềm liên tục tự động kiểm tra trạng thái sức khoẻ dịch vụ ở các thẻ kết nối bên trái.
            </p>
          </div>
        </div>
      </div>

      {/* Confirmation Modal */}
      {showConfirmModal && (
         <div className="fixed inset-0 z-[100] flex items-center justify-center bg-slate-900/60 backdrop-blur-sm px-4">
            <div className="bg-white border border-slate-200 p-8 rounded-3xl w-full max-w-md shadow-2xl animate-scale-up relative overflow-hidden">
                <div className="absolute top-0 left-0 w-full h-[3px] bg-gradient-to-r from-blue-500 to-indigo-600" />
                <button onClick={() => setShowConfirmModal(false)} className="absolute top-4 right-4 p-2 hover:bg-slate-100 rounded-full text-slate-400 transition-colors"><X className="w-4 h-4"/></button>
                
                <div className="w-14 h-14 rounded-2xl bg-amber-500/10 flex items-center justify-center mb-6 mx-auto">
                    <Lock className="w-7 h-7 text-amber-500" />
                </div>
                
                <h2 className="text-lg font-bold text-center text-slate-800 mb-2">Xác nhận Bảo mật</h2>
                <p className="text-xs text-slate-500 text-center mb-8 px-4 leading-relaxed">
                    Bạn đang chuẩn bị thay đổi cấu hình kết nối cốt lõi. Vui lòng nhập mật khẩu tài khoản Admin để tiếp tục.
                </p>
                
                <div className="space-y-5">
                    <div className="relative">
                        <input 
                            type="password"
                            autoFocus
                            value={adminPassword}
                            onChange={(e) => setAdminPassword(e.target.value)}
                            className="input-field text-center font-mono tracking-widest text-slate-800 bg-slate-50 border border-slate-200 focus:bg-white"
                            placeholder="Mật khẩu của bạn..."
                        />
                    </div>
                    
                    <button 
                        onClick={handleUpdate}
                        disabled={saving}
                        className="btn-gradient w-full py-3.5 rounded-2xl font-bold text-xs flex items-center justify-center gap-2 shadow-xl shadow-blue-500/20 active:scale-95 transition-transform"
                    >
                        {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <CheckCircle2 className="w-4 h-4" />}
                        {saving ? "Đang xử lý..." : "Xác nhận & Lưu Thay đổi"}
                    </button>
                    
                    <button onClick={() => setShowConfirmModal(false)} className="w-full py-3 text-[11px] font-bold text-slate-400 hover:text-slate-600 transition-colors">
                        Hủy bỏ
                    </button>
                </div>
            </div>
         </div>
      )}
    </div>
  );
}
