'use client';

import { useEffect } from 'react';
import { AlertCircle, RefreshCw, Home } from 'lucide-react';

export default function Error({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    // Log the error to an error reporting service
    console.error('Next.js Runtime Error:', error);
  }, [error]);

  return (
    <div className="min-h-[60vh] flex flex-col items-center justify-center p-6 text-center animate-fade-in">
      <div className="w-20 h-20 rounded-full bg-red-500/10 flex items-center justify-center mb-6">
        <AlertCircle className="w-10 h-10 text-red-500" />
      </div>
      
      <h2 className="text-xl font-bold text-white mb-2">Đã có lỗi xảy ra!</h2>
      <p className="text-slate-400 max-w-md mb-8">
        Hệ thống gặp sự cố bất ngờ khi xử lý yêu cầu của bạn. Vui lòng thử tải lại trang hoặc quay về trang chủ.
      </p>

      <div className="flex flex-wrap gap-4 justify-center">
        <button
          onClick={() => reset()}
          className="btn-gradient flex items-center gap-2"
        >
          <RefreshCw className="w-4 h-4" />
          Thử lại ngay
        </button>
        
        <a
          href="/"
          className="btn-secondary flex items-center gap-2"
        >
          <Home className="w-4 h-4" />
          Quay về Trang chủ
        </a>
      </div>

      {error.digest && (
        <p className="mt-8 text-[10px] text-slate-700 font-mono">
          Error Digest: {error.digest}
        </p>
      )}
    </div>
  );
}
