'use client';

import { useState } from 'react';
import { usePathname } from 'next/navigation';
import { AuthProvider } from '@/hooks/useAuth';
import AuthGuard from '@/components/layout/AuthGuard';
import Sidebar from '@/components/layout/Sidebar';
import TopBar from '@/components/layout/TopBar';
import { CrackProvider } from '@/hooks/useCrack';
import { ChatProvider } from '@/hooks/useChat';

const NO_LAYOUT_PATHS = ['/login'];

export default function ClientLayout({ children }: { children: React.ReactNode }) {
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const pathname = usePathname();
  const showLayout = !NO_LAYOUT_PATHS.includes(pathname);

  return (
    <AuthProvider>
      <AuthGuard>
        <CrackProvider>
          <ChatProvider>
            {showLayout ? (
              <div className="flex min-h-screen">
                <Sidebar
                  collapsed={sidebarCollapsed}
                  onToggle={() => setSidebarCollapsed(!sidebarCollapsed)}
                />
                <TopBar collapsed={sidebarCollapsed} />
                <main
                  className={`main-content flex-1 min-w-0 ${
                    sidebarCollapsed ? 'sidebar-collapsed' : ''
                  }`}
                >
                  <div className="p-6 min-w-0 w-full">{children}</div>
                </main>
              </div>
            ) : (
              <>{children}</>
            )}
          </ChatProvider>
        </CrackProvider>
      </AuthGuard>
    </AuthProvider>
  );
}
