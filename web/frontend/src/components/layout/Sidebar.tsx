'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import {
  LayoutDashboard,
  ScanSearch,
  MessageSquareCode,
  Box,
  Settings,
  ChevronLeft,
  ChevronRight,
  Activity,
  Map,
  BookOpen,
  Users,
  ClipboardList,
  FileBarChart,
  UserCircle,
  Archive,
  Trash2,
} from 'lucide-react';
import { useAuth } from '@/hooks/useAuth';

interface NavItem {
  label: string;
  href: string;
  icon: any;
  adminOnly?: boolean;
}

const mainNav: NavItem[] = [
  { label: 'Tổng quan', href: '/', icon: LayoutDashboard },
  { label: 'Nhận diện Hư hỏng AI', href: '/crack-detection', icon: ScanSearch },
  { label: 'Giám sát 3D & Duyệt', href: '/digital-twin', icon: Box },
  { label: 'Bản đồ & Bình đồ', href: '/incidents-map', icon: Map },
  { label: 'Trợ lý AI', href: '/chatbot', icon: MessageSquareCode },
  { label: 'Báo cáo kỹ thuật', href: '/reports', icon: FileBarChart },
  { label: 'Thùng rác', href: '/trash', icon: Trash2 },
];

const adminNav: NavItem[] = [
  { label: 'Tri thức AI', href: '/knowledge', icon: BookOpen, adminOnly: true },
  { label: 'Người dùng', href: '/user-management', icon: Users, adminOnly: true },
  { label: 'Nhật ký', href: '/audit-log', icon: ClipboardList, adminOnly: true },
  { label: 'Cài đặt', href: '/settings', icon: Settings, adminOnly: true },
];

interface SidebarProps {
  collapsed: boolean;
  onToggle: () => void;
}

export default function Sidebar({ collapsed, onToggle }: SidebarProps) {
  const pathname = usePathname();
  const { user } = useAuth();
  const isAdmin = user?.role === 'admin';

  const renderNavItem = (item: NavItem) => {
    const Icon = item.icon;
    const isActive =
      item.href === '/' ? pathname === '/' : pathname.startsWith(item.href);

    return (
      <li key={item.href}>
        <Link
          href={item.href}
          className={`sidebar-link ${isActive ? 'active' : ''}`}
          title={collapsed ? item.label : undefined}
        >
          <Icon className="w-5 h-5 shrink-0" />
          {!collapsed && <span className="truncate">{item.label}</span>}
          {isActive && !collapsed && (
            <div className="ml-auto w-1.5 h-1.5 rounded-full bg-blue-400 animate-glow-pulse" />
          )}
        </Link>
      </li>
    );
  };

  return (
    <aside className={`sidebar ${collapsed ? 'collapsed' : ''}`}>
      {/* Logo */}
      <div className="flex items-center gap-3 px-6 py-5 border-b border-slate-100">
        <div className="flex items-center justify-center w-10 h-10 rounded-xl bg-gradient-to-br from-blue-500 to-purple-600 shrink-0 shadow-lg shadow-blue-500/20">
          <Activity className="w-5 h-5 text-white" />
        </div>
        {!collapsed && (
          <div className="animate-fade-in">
            <h1 className="text-sm font-bold text-slate-800 tracking-tight">Digital Twin</h1>
            <p className="text-[10px] text-slate-400 font-medium">Giám sát Công trình</p>
          </div>
        )}
      </div>

      {/* Navigation */}
      <nav className="flex-1 py-4 overflow-y-auto">
        {/* ── Module chính ─────────────────── */}
        <div className="mb-3 px-6">
          {!collapsed && (
            <span className="text-[10px] font-semibold uppercase tracking-wider text-slate-400">
              Module chính
            </span>
          )}
        </div>
        <ul className="space-y-1">
          {mainNav.map(renderNavItem)}
        </ul>

        {/* ── Quản trị (Admin only) ──────── */}
        {isAdmin && (
          <>
            <div className="my-3 mx-6 border-t border-slate-100" />
            <div className="mb-3 px-6">
              {!collapsed && (
                <span className="text-[10px] font-semibold uppercase tracking-wider text-amber-600">
                  Quản trị
                </span>
              )}
            </div>
            <ul className="space-y-1">
              {adminNav.map(renderNavItem)}
            </ul>
          </>
        )}

        {/* ── Hồ sơ (tất cả users) ──────── */}
        <div className="my-3 mx-6 border-t border-slate-100" />
        <ul className="space-y-1">
          {renderNavItem({
            label: 'Hồ sơ cá nhân',
            href: '/profile',
            icon: UserCircle,
          })}
        </ul>
      </nav>

      {/* Collapse Toggle */}
      <div className="border-t border-slate-100 p-3">
        <button
          onClick={onToggle}
          className="flex items-center justify-center w-full py-2.5 rounded-xl text-slate-400 hover:text-slate-900 hover:bg-slate-100 transition-all duration-200"
          aria-label={collapsed ? 'Mở rộng sidebar' : 'Thu gọn sidebar'}
        >
          {collapsed ? (
            <ChevronRight className="w-5 h-5" />
          ) : (
            <div className="flex items-center gap-2 text-xs font-medium">
              <ChevronLeft className="w-4 h-4" />
              Thu gọn
            </div>
          )}
        </button>
      </div>

      {/* Version */}
      {!collapsed && (
        <div className="px-6 pb-4">
          <p className="text-[10px] text-slate-700 text-center">v2.0.0 • 2026</p>
        </div>
      )}
    </aside>
  );
}
