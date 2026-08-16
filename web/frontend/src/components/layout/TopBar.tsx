'use client';

import { useAuth } from '@/hooks/useAuth';
import {
  Bell,
  Search,
  LogOut,
  User,
  ChevronDown,
} from 'lucide-react';
import { useState, useRef, useEffect } from 'react';
import { useCrack } from '@/hooks/useCrack';
import { useRouter } from 'next/navigation';

interface TopBarProps {
  collapsed: boolean;
}

export default function TopBar({ collapsed }: TopBarProps) {
  const { user, logout } = useAuth();
  const { alerts } = useCrack();
  const router = useRouter();
  
  const [showUserMenu, setShowUserMenu] = useState(false);
  const [showNotifications, setShowNotifications] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const [readAlertIds, setReadAlertIds] = useState<string[]>([]);
  
  const menuRef = useRef<HTMLDivElement>(null);
  const notifRef = useRef<HTMLDivElement>(null);
  const searchInputRef = useRef<HTMLInputElement>(null);

  // Load read alert IDs
  useEffect(() => {
    const saved = localStorage.getItem('read_alert_ids');
    if (saved) {
      try {
        setReadAlertIds(JSON.parse(saved));
      } catch (e) {}
    }
  }, []);

  // Filter out read alerts
  const unreadAlerts = alerts.filter(a => !readAlertIds.includes(a.id));

  // Mark all read handler
  const handleMarkAllRead = () => {
    const allIds = alerts.map(a => a.id);
    setReadAlertIds(allIds);
    localStorage.setItem('read_alert_ids', JSON.stringify(allIds));
  };

  // Close menus on outside click & register Ctrl+K shortcut
  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setShowUserMenu(false);
      }
      if (notifRef.current && !notifRef.current.contains(e.target as Node)) {
        setShowNotifications(false);
      }
    };
    
    const handleKeyDown = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
        e.preventDefault();
        searchInputRef.current?.focus();
      }
    };

    document.addEventListener('mousedown', handleClickOutside);
    window.addEventListener('keydown', handleKeyDown);
    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
      window.removeEventListener('keydown', handleKeyDown);
    };
  }, []);

  return (
    <header className={`topbar ${collapsed ? 'sidebar-collapsed' : ''}`}>
      {/* Search Bar */}
      <div className="flex items-center gap-3 flex-1 max-w-lg">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-500" />
          <input
            type="text"
            placeholder="Tìm kiếm sự cố... (Ctrl+K)"
            className="input-field pl-10 py-2 text-xs"
            id="global-search"
            ref={searchInputRef}
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && searchQuery.trim()) {
                router.push(`/incidents-map?search=${encodeURIComponent(searchQuery)}`);
              }
            }}
          />
        </div>
      </div>

      {/* Right Side */}
      <div className="flex items-center gap-2">
        {/* Notifications */}
        <div className="relative" ref={notifRef}>
          <button
            onClick={() => setShowNotifications(!showNotifications)}
            className="relative flex items-center justify-center w-10 h-10 rounded-xl hover:bg-slate-100 transition-colors"
            id="notifications-btn"
          >
            <Bell className="w-5 h-5 text-slate-600" />
            {unreadAlerts.length > 0 && (
              <span className="absolute top-1.5 right-1.5 w-2 h-2 bg-red-500 rounded-full shadow-[0_0_8px_rgba(239,68,68,0.4)]" />
            )}
          </button>

          {showNotifications && (
            <div className="absolute right-0 top-12 w-80 glass-card p-0 animate-slide-in z-50">
              <div className="px-4 py-3 border-b border-slate-100 flex justify-between items-center">
                <h3 className="text-sm font-semibold text-slate-900">Thông báo</h3>
                <span className="text-[10px] bg-blue-50 text-blue-600 px-1.5 py-0.5 rounded-full font-bold">{unreadAlerts.length}</span>
              </div>
              <div className="max-h-64 overflow-y-auto">
                {unreadAlerts.length === 0 ? (
                  <div className="p-8 text-center">
                    <p className="text-[10px] text-slate-400 font-medium italic">Không có thông báo mới</p>
                  </div>
                ) : (
                  unreadAlerts.slice(0, 10).map((notif) => (
                    <div
                      key={notif.id}
                      onClick={() => {
                        setShowNotifications(false);
                        if (notif.task_id) {
                          router.push(`/crack-detection?task_id=${notif.task_id}`);
                        } else {
                          router.push('/incidents-map');
                        }
                      }}
                      className="flex items-start gap-3 px-4 py-3 hover:bg-slate-50 transition-colors border-b border-slate-100 last:border-0 cursor-pointer"
                    >
                      <div
                        className={`glow-dot mt-1.5 shrink-0 ${
                          notif.type === 'critical' ? 'red' : notif.type === 'warning' ? 'yellow' : 'green'
                        }`}
                      />
                      <div className="flex-1 min-w-0">
                        <p className="text-xs text-slate-800 font-bold truncate">{notif.title}</p>
                        <p className="text-[10px] text-slate-500 mt-0.5 line-clamp-1">{notif.message}</p>
                        <p className="text-[9px] text-slate-400 mt-1 font-medium">
                          {(() => {
                            const date = new Date(notif.timestamp);
                            const utcDate = notif.timestamp.includes('Z') || notif.timestamp.includes('+') ? date : new Date(notif.timestamp + 'Z');
                            return utcDate.toLocaleTimeString('vi-VN', { hour: '2-digit', minute: '2-digit', timeZone: 'Asia/Ho_Chi_Minh' });
                          })()}
                        </p>
                      </div>
                    </div>
                  ))
                )}
              </div>
              {unreadAlerts.length > 0 && (
                <div className="px-4 py-2 border-t border-slate-100">
                  <button 
                    onClick={handleMarkAllRead}
                    className="text-xs text-blue-600 hover:text-blue-700 font-bold"
                  >
                    Đánh dấu đã đọc tất cả
                  </button>
                </div>
              )}
            </div>
          )}
        </div>

        {/* Separator */}
        <div className="h-8 w-px bg-slate-200" />

        {/* User Menu */}
        <div className="relative" ref={menuRef}>
          <button
            onClick={() => setShowUserMenu(!showUserMenu)}
            className="flex items-center gap-2.5 px-3 py-2 rounded-xl hover:bg-slate-100 transition-colors"
            id="user-menu-btn"
          >
            <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-blue-500 to-purple-600 flex items-center justify-center shadow-lg shadow-blue-500/10">
              <span className="text-xs font-bold text-white">
                {user?.full_name?.charAt(0) || 'A'}
              </span>
            </div>
            <div className="hidden md:block text-left">
              <p className="text-xs font-semibold text-slate-800">
                {user?.full_name || 'Admin'}
              </p>
              <p className="text-[10px] text-slate-400 font-medium">{user?.role || 'admin'}</p>
            </div>
            <ChevronDown className="w-3.5 h-3.5 text-slate-400" />
          </button>

          {showUserMenu && (
            <div className="absolute right-0 top-12 w-52 glass-card p-1.5 animate-slide-in z-50">
              <button 
                onClick={() => {
                  setShowUserMenu(false);
                  router.push('/profile');
                }}
                className="flex items-center gap-2.5 w-full px-3 py-2.5 rounded-lg text-xs text-slate-600 hover:bg-slate-50 hover:text-slate-900 transition-colors"
              >
                <User className="w-4 h-4" />
                Hồ sơ cá nhân
              </button>
              <div className="h-px bg-slate-100 my-1" />
              <button
                onClick={logout}
                className="flex items-center gap-2.5 w-full px-3 py-2.5 rounded-lg text-xs text-red-600 hover:bg-red-50 transition-colors"
                id="logout-btn"
              >
                <LogOut className="w-4 h-4" />
                Đăng xuất
              </button>
            </div>
          )}
        </div>
      </div>
    </header>
  );
}
