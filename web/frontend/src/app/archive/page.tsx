'use client';

import { useEffect } from 'react';
import { useRouter } from 'next/navigation';
import { Loader2 } from 'lucide-react';

export default function ArchiveRedirectPage() {
  const router = useRouter();

  useEffect(() => {
    router.replace('/digital-twin');
  }, [router]);

  return (
    <div className="h-screen w-screen flex flex-col items-center justify-center bg-slate-50 text-slate-500 gap-3">
      <Loader2 className="w-8 h-8 text-blue-500 animate-spin" />
      <p className="text-xs font-bold uppercase tracking-widest">Đang chuyển hướng sang Trung tâm Phân tích AI...</p>
    </div>
  );
}
