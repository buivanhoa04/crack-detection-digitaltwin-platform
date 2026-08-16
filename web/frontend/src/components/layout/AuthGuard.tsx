'use client';

import { useAuth } from '@/hooks/useAuth';
import { usePathname, useRouter } from 'next/navigation';
import { useEffect } from 'react';

const PUBLIC_PATHS = ['/login'];

export default function AuthGuard({ children }: { children: React.ReactNode }) {
  const { isAuthenticated, loading } = useAuth();
  const pathname = usePathname();
  const router = useRouter();

  useEffect(() => {
    if (!loading && !isAuthenticated && !PUBLIC_PATHS.includes(pathname)) {
      router.push('/login');
    }
  }, [isAuthenticated, loading, pathname, router]);

  // Show loading spinner while checking auth
  if (loading) {
    return (
      <div className="fixed inset-0 flex items-center justify-center bg-dark-950">
        <div className="flex flex-col items-center gap-4">
          <div className="w-12 h-12 rounded-full border-4 border-blue-500/20 border-t-blue-500 animate-spin" />
          <p className="text-sm text-slate-500 font-medium">Đang tải...</p>
        </div>
      </div>
    );
  }

  // Public pages
  if (PUBLIC_PATHS.includes(pathname)) {
    return <>{children}</>;
  }

  // Protected pages
  if (!isAuthenticated) {
    return null;
  }

  return <>{children}</>;
}
