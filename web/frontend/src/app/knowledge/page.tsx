'use client';

import { useState, useCallback, useEffect } from 'react';
import { useDropzone } from 'react-dropzone';
import { useAuth } from '@/hooks/useAuth';
import { chatbotAPI } from '@/lib/api';
import type { Document } from '@/types';
import {
  UploadCloud,
  FileText,
  Trash2,
  RefreshCw,
  AlertCircle,
  CheckCircle2,
  Loader2,
  FileIcon,
  Play,
  Pause,
} from 'lucide-react';
import { format } from 'date-fns';

export default function KnowledgePage() {
  const { user } = useAuth();
  const isAdmin = user?.role === 'admin';

  const [documents, setDocuments] = useState<Document[]>([]);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState('');
  const [parsingIds, setParsingIds] = useState<Set<string>>(new Set());

  const fetchDocuments = async () => {
    try {
      setLoading(true);
      setError('');
      const { data } = await chatbotAPI.getDocuments();
      setDocuments(data.documents || []);
    } catch (err: any) {
      setError(err.response?.data?.detail || 'Lỗi tải danh sách tài liệu');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchDocuments();
    // Poll for status updates if any doc is parsing
    const interval = setInterval(() => {
      setDocuments((currentDocs) => {
        const hasParsing = currentDocs.some((d) => d.status === 'parsing');
        if (hasParsing) {
          fetchDocuments();
        }
        return currentDocs;
      });
    }, 5000);

    return () => clearInterval(interval);
  }, []);

  const onDrop = useCallback(async (acceptedFiles: File[]) => {
    if (acceptedFiles.length === 0) return;
    
    setUploading(true);
    setError('');

    try {
      for (const file of acceptedFiles) {
        await chatbotAPI.uploadDocument(file);
      }
      await fetchDocuments();
    } catch (err: any) {
      setError(err.response?.data?.detail || 'Upload thất bại');
    } finally {
      setUploading(false);
    }
  }, []);

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    accept: {
      'application/pdf': ['.pdf'],
      'text/plain': ['.txt'],
      'text/markdown': ['.md'],
    },
    disabled: !isAdmin,
  });

  const handleDelete = async (docId: string) => {
    if (!isAdmin) return;
    if (!confirm('Bạn có chắc chắn muốn xóa tài liệu này? Chatbot sẽ không thể tìm kiếm thông tin từ đây nữa.')) return;

    try {
      await chatbotAPI.deleteDocument(docId);
       setDocuments(documents.filter((d) => d.id !== docId));
    } catch (err: any) {
       setError(err.response?.data?.detail || 'Lỗi khi xóa tài liệu');
    }
  };

  const handleParse = async (docId: string, action: 'start' | 'stop') => {
    if (!isAdmin) return;
    setParsingIds(prev => new Set(prev).add(docId));
    setError('');
    try {
      await chatbotAPI.parseDocument(docId, action);
      // Refresh list after a short delay to let RAGFlow process
      setTimeout(() => fetchDocuments(), 1500);
    } catch (err: any) {
      setError(err.response?.data?.detail || `Lỗi ${action === 'start' ? 'bắt đầu' : 'dừng'} phân tích`);
    } finally {
      setParsingIds(prev => {
        const next = new Set(prev);
        next.delete(docId);
        return next;
      });
    }
  };

  const formatSize = (bytes?: number | null) => {
    if (bytes === null || bytes === undefined || bytes <= 0) return 'N/A';
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
  };

  return (
    <div className="space-y-6 max-w-6xl mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-white mb-1">
            Tri thức Trợ lý Khoa học
          </h1>
          <p className="text-slate-400 text-sm">
            Quản lý tài liệu, tiêu chuẩn TCVN làm cơ sở tri thức cho Chatbot AI
          </p>
        </div>
        <button
          onClick={fetchDocuments}
          className="flex items-center gap-2 px-4 py-2 bg-slate-800 text-white rounded-xl hover:bg-slate-700 transition"
        >
          <RefreshCw className="w-4 h-4" /> Cập nhật
        </button>
      </div>

      {error && (
        <div className="bg-red-500/10 border border-red-500/20 text-red-400 p-4 rounded-xl text-sm flex items-start gap-3">
          <AlertCircle className="w-5 h-5 shrink-0" />
          <p>{error}</p>
        </div>
      )}

      {/* Upload Zone */}
      {isAdmin && (
        <div
          {...getRootProps()}
          className={`relative border-2 border-dashed rounded-2xl p-10 transition-colors text-center cursor-pointer overflow-hidden ${
            isDragActive
              ? 'border-blue-500 bg-blue-500/10'
              : 'border-slate-700 hover:border-slate-500 bg-slate-800/30'
          }`}
        >
          <input {...getInputProps()} />
          <div className="flex flex-col items-center justify-center pointer-events-none">
            <UploadCloud
              className={`w-12 h-12 mb-4 ${
                isDragActive ? 'text-blue-400' : 'text-slate-500'
              }`}
            />
            <p className="text-base font-medium text-slate-200 mb-1">
              {isDragActive
                ? 'Thả file vào đây'
                : 'Kéo thả file hoặc nhấp để tải lên'}
            </p>
            <p className="text-sm text-slate-500 text-center">
              Hỗ trợ: PDF (.pdf), Text (.txt, .md). Tối đa 50MB. <br />
              File sau khi tải lên sẽ được bóc tách và embedded tự động vào RAGFlow.
            </p>
          </div>

          {uploading && (
            <div className="absolute inset-0 bg-slate-900/80 backdrop-blur-sm flex flex-col items-center justify-center z-10 text-white">
              <Loader2 className="w-8 h-8 animate-spin mb-3 text-blue-500" />
              <p className="font-medium animate-pulse">Đang tải lên tài liệu...</p>
            </div>
          )}
        </div>
      )}

      {/* Document List */}
      <div className="bg-slate-900 border border-white/[0.05] rounded-2xl overflow-hidden shadow-xl">
        <div className="p-5 border-b border-white/[0.05]">
          <h2 className="text-lg font-semibold text-white">Danh sách tài liệu</h2>
        </div>

        {loading ? (
          <div className="p-10 flex flex-col items-center justify-center text-slate-400">
            <Loader2 className="w-8 h-8 animate-spin mb-3" />
            <p>Đang tải dữ liệu...</p>
          </div>
        ) : documents.length === 0 ? (
          <div className="p-16 text-center">
            <div className="w-16 h-16 bg-slate-800 rounded-2xl flex items-center justify-center mx-auto mb-4">
              <FileIcon className="w-8 h-8 text-slate-500" />
            </div>
            <h3 className="text-white font-medium mb-1">Chưa có tài liệu nào</h3>
            <p className="text-slate-500 text-sm">
              {isAdmin ? 'Hãy tải lên file đầu tiên để xây dựng tri thức.' : 'Đang chờ admin cập nhật tri thức.'}
            </p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left border-collapse">
              <thead>
                <tr className="border-b border-white/[0.05] text-[11px] uppercase tracking-wider text-slate-500 bg-slate-800/50">
                  <th className="px-6 py-4 font-semibold">Tên file</th>
                  <th className="px-6 py-4 font-semibold">Trạng thái</th>
                  <th className="px-6 py-4 font-semibold whitespace-nowrap">Dung lượng</th>
                  <th className="px-6 py-4 font-semibold whitespace-nowrap">Số Chunk</th>
                  <th className="px-6 py-4 font-semibold text-right">Thao tác</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-white/[0.05]">
                {documents.map((doc) => (
                  <tr key={doc.id} className="hover:bg-white/[0.02] transition-colors">
                    <td className="px-6 py-4">
                      <div className="flex items-start gap-3">
                        <div className="w-10 h-10 rounded-xl bg-slate-800 flex items-center justify-center shrink-0">
                          <FileText className="w-5 h-5 text-blue-400" />
                        </div>
                        <div>
                          <p className="text-sm font-medium text-slate-200 line-clamp-1 max-w-[300px]">
                            {doc.filename}
                          </p>
                          <div className="text-[11px] text-slate-500 mt-0.5">
                            {doc.uploaded_at
                              ? format(new Date(doc.uploaded_at), 'HH:mm • dd/MM/yyyy')
                              : 'Không rõ thời gian'}
                          </div>
                        </div>
                      </div>
                    </td>
                    <td className="px-6 py-4">
                      {doc.status === 'success' ? (
                        <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-emerald-500/10 text-emerald-400 text-xs font-medium border border-emerald-500/20">
                          <CheckCircle2 className="w-3.5 h-3.5" /> Thành công
                        </span>
                      ) : doc.status === 'fail' ? (
                        <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-red-500/10 text-red-400 text-xs font-medium border border-red-500/20">
                          <AlertCircle className="w-3.5 h-3.5" /> Lỗi phân tách
                        </span>
                      ) : (doc.status as string) === 'cancel' || (doc.status as string) === 'paused' ? (
                        <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-amber-500/10 text-amber-400 text-xs font-medium border border-amber-500/20">
                          <Pause className="w-3.5 h-3.5" /> Đã dừng ({doc.progress || 0}%)
                        </span>
                      ) : (doc.status as string) === 'uploaded' || (doc.status as string) === '0' || !doc.status ? (
                        <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-slate-500/10 text-slate-400 text-xs font-medium border border-slate-500/20">
                          Chưa phân tích
                        </span>
                      ) : (
                        <div className="flex items-center gap-3 w-40">
                          <Loader2 className="w-4 h-4 text-blue-400 animate-spin shrink-0" />
                          <div className="flex-1 w-full bg-slate-800 rounded-full h-1.5 border border-white/5">
                            <div
                              className="bg-blue-500 h-1.5 rounded-full transition-all duration-300"
                              style={{ width: `${doc.progress || 10}%` }}
                            />
                          </div>
                          <span className="text-xs text-slate-400 w-8">{doc.progress || 0}%</span>
                        </div>
                      )}
                    </td>
                    <td className="px-6 py-4 text-sm text-slate-400 whitespace-nowrap">
                      {formatSize(doc.size)}
                    </td>
                    <td className="px-6 py-4 text-sm text-slate-400">
                      {doc.chunks_count != null && doc.chunks_count >= 0 ? (
                        <span className="px-2 py-0.5 rounded-md bg-slate-800 text-slate-300">
                          {doc.chunks_count} chunks
                        </span>
                      ) : (
                        <span className="text-slate-600">-</span>
                      )}
                    </td>
                    <td className="px-6 py-4">
                      <div className="flex items-center justify-end gap-1">
                        {isAdmin && (
                          <>
                            {/* Play: Start/Re-parse */}
                            {(doc.status !== 'parsing' || doc.progress === 0) && (
                              <button
                                onClick={() => handleParse(doc.id, 'start')}
                                disabled={parsingIds.has(doc.id)}
                                className="p-2 text-emerald-400 hover:bg-emerald-500/10 rounded-lg transition-colors disabled:opacity-50"
                                title={(doc.status as string) === 'success' ? 'Phân tích lại' : (doc.status as string) === 'cancel' ? 'Tiếp tục phân tích' : 'Bắt đầu phân tích'}
                              >
                                {parsingIds.has(doc.id) ? (
                                  <Loader2 className="w-4 h-4 animate-spin" />
                                ) : (
                                  <Play className="w-4 h-4" />
                                )}
                              </button>
                            )}
                            
                            {/* Pause: Stop parsing */}
                            {doc.status === 'parsing' && (doc.progress ?? 0) > 0 && (
                              <button
                                onClick={() => handleParse(doc.id, 'stop')}
                                disabled={parsingIds.has(doc.id)}
                                className="p-2 text-amber-400 hover:bg-amber-500/10 rounded-lg transition-colors disabled:opacity-50"
                                title="Dừng phân tích"
                              >
                                {parsingIds.has(doc.id) ? (
                                  <Loader2 className="w-4 h-4 animate-spin" />
                                ) : (
                                  <Pause className="w-4 h-4" />
                                )}
                              </button>
                            )}
                            
                            {/* Delete Button */}
                            <button
                              onClick={() => handleDelete(doc.id)}
                              className="p-2 text-slate-500 hover:text-red-400 hover:bg-red-500/10 rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                              title="Xóa tài liệu"
                            >
                              <Trash2 className="w-4 h-4" />
                            </button>
                          </>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
