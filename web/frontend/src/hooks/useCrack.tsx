'use client';

import React, { createContext, useContext, useState, useEffect, useCallback, useRef } from 'react';
import { crackAPI } from '@/lib/api';
import { useAuth } from './useAuth';

interface CrackContextType {
  tasks: any[];
  alerts: any[];
  stats: any;
  chartData: any[];
  selectedResult: any | null;
  setSelectedResult: (task: any) => void;
  startDetection: (
    files: File[],
    modelType: string,
    generate3D?: boolean,
    surveyId?: string,
    segmentationEnabled?: boolean,
    colorNormalizationEnabled?: boolean
  ) => Promise<void>;
  deleteTask: (taskId: string) => Promise<void>;
  retryTask: (taskId: string) => Promise<void>;
  refreshHistory: () => Promise<void>;
  refreshAlerts: () => Promise<void>;
  refreshDashboard: () => Promise<void>;
  isPolling: boolean;
}

const CrackContext = createContext<CrackContextType | null>(null);

export function CrackProvider({ children }: { children: React.ReactNode }) {
  const { user } = useAuth();
  const [tasks, setTasks] = useState<any[]>([]);
  const [alerts, setAlerts] = useState<any[]>([]);
  const [stats, setStats] = useState<any>({ totalScans: 0, cracksDetected: 0, chatSessions: 0, performance: 0 });
  const [chartData, setChartData] = useState<any[]>([]);
  const [selectedResult, setSelectedResult] = useState<any | null>(null);
  const [isPolling, setIsPolling] = useState(false);
  const pollingRef = useRef<NodeJS.Timeout | null>(null);

  // Load stats and chart
  const refreshDashboard = useCallback(async () => {
    if (!user) return;
    try {
      const [statsRes, chartRes] = await Promise.all([
        crackAPI.getStats(),
        crackAPI.getActivity()
      ]);
      setStats(statsRes.data);
      setChartData(chartRes.data);
    } catch (e) {
      console.error("Dashboard refresh failed", e);
    }
  }, [user]);

  useEffect(() => {
    if (!user) return;
    refreshDashboard();
    const interval = setInterval(refreshDashboard, 30000); // 30s refresh dashboard stats
    return () => clearInterval(interval);
  }, [refreshDashboard, user]);

  // Load alerts
  const refreshAlerts = useCallback(async () => {
    if (!user) return;
    try {
      const { data } = await crackAPI.getAlerts();
      if (data && data.alerts) {
        setAlerts(data.alerts);
      }
    } catch (e) {
      console.error("Failed to fetch alerts", e);
    }
  }, [user]);

  useEffect(() => {
    if (!user) return;
    refreshAlerts();
    const alertInterval = setInterval(refreshAlerts, 10000); // 10s refresh alerts
    return () => clearInterval(alertInterval);
  }, [refreshAlerts, user]);

  // Load initial history
  const refreshHistory = useCallback(async () => {
    if (!user) return;
    try {
      const { data } = await crackAPI.getHistory();
      if (data && data.tasks) {
        setTasks(prev => {
           // Merge: preserve local statuses of things that might be in polling
           const serverTasks = data.tasks;
           return serverTasks.map((st: any) => {
             const local = prev.find(p => p.task_id === st.task_id);
             return local ? { ...local, ...st } : st;
           });
        });
      }
    } catch (e) {
      console.error("Failed to fetch crack history", e);
    }
  }, [user]);

  useEffect(() => {
    if (!user) return;
    refreshHistory();
  }, [refreshHistory, user]);

  // Global Polling Logic - Only poll parent tasks and limit concurrency to 5 tasks to prevent request flooding
  const activeTaskKey = tasks
    .filter(t => t.task_id && t.status !== 'done' && t.status !== 'error' && !t.task_id.startsWith('error_') && !t.parent_task_id)
    .map(t => t.task_id as string)
    .sort()
    .slice(0, 5)
    .join('|');

  useEffect(() => {
    let cancelled = false;
    const activeTaskIds = activeTaskKey ? activeTaskKey.split('|') : [];

    if (user && activeTaskIds.length > 0) {
      setIsPolling(true);

      const poll = async () => {
        const results = await Promise.all(activeTaskIds.map(async taskId => {
          try {
            const { data } = await crackAPI.getStatus(taskId);
            return { taskId, data };
          } catch (error) {
            console.error(`Status check failed for ${taskId}`, error);
            return null;
          }
        }));

        if (cancelled) return;
        const updates = new Map(
          results
            .filter((result): result is { taskId: string; data: any } => Boolean(result?.data?.status))
            .map(result => [result.taskId, result.data])
        );
        if (updates.size > 0) {
          setTasks(previous => previous.map(task => {
            const update = updates.get(task.task_id);
            return update ? { ...task, ...update } : task;
          }));
          setSelectedResult((previous: any) => {
            if (!previous) return previous;
            const update = updates.get(previous.task_id);
            return update ? { ...previous, ...update } : previous;
          });
        }
        pollingRef.current = setTimeout(poll, 3000);
      };
      void poll();
    } else {
      setIsPolling(false);
      if (pollingRef.current) {
        clearTimeout(pollingRef.current);
        pollingRef.current = null;
      }
    }

    return () => {
      if (pollingRef.current) clearTimeout(pollingRef.current);
      pollingRef.current = null;
      cancelled = true;
    };
  }, [activeTaskKey, user]);

  const startDetection = async (
    files: File[],
    modelType: string,
    generate3D: boolean = false,
    surveyId?: string,
    segmentationEnabled: boolean = true,
    colorNormalizationEnabled: boolean = true
  ) => {
    if (files.length === 0) return;
    
    // Determine a nice display name for the task
    let displayName = files[0].name;
    if (files.length > 1) {
      const firstPath = files[0].webkitRelativePath;
      if (firstPath && firstPath.includes('/')) {
        displayName = firstPath.split('/')[0];
      } else {
        displayName = `${files[0].name} (và ${files.length - 1} tệp khác)`;
      }
    }

    // Generate a unique client-side task ID to link chunks together
    const taskId = `task_${Math.random().toString(36).substring(2, 10)}${Date.now().toString().slice(-4)}`;

    // Add a placeholder task in state showing transferring status
    const initialTask = {
      task_id: taskId,
      status: 'transferring',
      filename: displayName,
      model_type: modelType,
      survey_id: surveyId,
      created_at: new Date().toISOString(),
      message: files.length > 50 ? `Đang chuẩn bị tải lên (${files.length} tệp)...` : 'Đang tải lên...'
    };
    setTasks(prev => [initialTask, ...prev]);
    setSelectedResult(initialTask);

    const sleep = (milliseconds: number) =>
      new Promise(resolve => window.setTimeout(resolve, milliseconds));

    const uploadWithRetry = async (
      operation: () => Promise<unknown>,
      progressMessage: string
    ) => {
      const retryableStatuses = new Set([408, 425, 429, 500, 502, 503, 504]);
      const maxAttempts = 6;

      for (let attempt = 1; attempt <= maxAttempts; attempt++) {
        try {
          return await operation();
        } catch (error: any) {
          const status = error?.response?.status;
          const canRetry = !error?.response || retryableStatuses.has(status);
          if (!canRetry || attempt === maxAttempts) throw error;

          const retryAfterSeconds = Number(error?.response?.headers?.['retry-after']);
          const backoffMs = Number.isFinite(retryAfterSeconds) && retryAfterSeconds > 0
            ? retryAfterSeconds * 1000
            : Math.min(2000 * (2 ** (attempt - 1)), 15000);

          setTasks(prev => prev.map(t => t.task_id === taskId ? {
            ...t,
            message: `${progressMessage} Kết nối bận, tự thử lại ${attempt}/${maxAttempts - 1}...`
          } : t));
          await sleep(backoffMs + Math.floor(Math.random() * 500));
        }
      }
    };

    try {
      const file = files[0];
      const isLargeFile = files.length === 1 && (file.size > 5 * 1024 * 1024 || file.name.match(/\.(mp4|avi|mov|mkv)$/i) !== null);

      if (isLargeFile) {
        const fileChunkSize = 10 * 1024 * 1024; // 10MB chunk size
        const totalChunks = Math.ceil(file.size / fileChunkSize);
        
        for (let chunkIndex = 0; chunkIndex < totalChunks; chunkIndex++) {
          const start = chunkIndex * fileChunkSize;
          const end = Math.min(start + fileChunkSize, file.size);
          const fileChunk = file.slice(start, end);
          
          const percent = Math.round((chunkIndex / totalChunks) * 100);
          setTasks(prev => prev.map(t => t.task_id === taskId ? {
            ...t,
            message: `Đang tải lên: ${percent}%...`
          } : t));
          
          await uploadWithRetry(
            () => crackAPI.uploadChunk(
              fileChunk,
              chunkIndex,
              totalChunks,
              file.name,
              taskId,
              modelType,
              generate3D,
              surveyId,
              segmentationEnabled,
              colorNormalizationEnabled
            ),
            `Đang tải lên: ${percent}%...`
          );
        }
      } else {
        // Large-folder production upload: bounded parallelism saturates the
        // connection without holding all image bytes in memory at once.
        const chunkSize = 50;
        const uploadConcurrency = 4;
        const totalFiles = files.length;
        const batchCount = Math.ceil(totalFiles / chunkSize);
        const lastBatchIndex = batchCount - 1;
        let nextBatchIndex = 0;
        let acknowledgedFiles = 0;

        const uploadBatch = async (batchIndex: number, isLastChunk: boolean) => {
          const start = batchIndex * chunkSize;
          const chunk = files.slice(start, Math.min(start + chunkSize, totalFiles));
          setTasks(prev => prev.map(t => t.task_id === taskId ? {
            ...t,
            message: `Đang tải song song: ${acknowledgedFiles}/${totalFiles} ảnh...`
          } : t));

          await uploadWithRetry(
            () => crackAPI.detect(
              chunk,
              modelType,
              generate3D,
              surveyId,
              taskId,
              isLastChunk,
              segmentationEnabled,
              batchIndex,
              batchCount,
              colorNormalizationEnabled
            ),
            `Đang tải lên lô ${batchIndex + 1}/${batchCount}.`
          );

          acknowledgedFiles += chunk.length;
          setTasks(prev => prev.map(t => t.task_id === taskId ? {
            ...t,
            message: `Đã xác nhận: ${acknowledgedFiles}/${totalFiles} ảnh...`
          } : t));
        };

        // Reserve the final batch. It is sent only after every previous batch
        // is acknowledged, so finalization can never race ahead of uploads.
        const worker = async () => {
          while (true) {
            const batchIndex = nextBatchIndex++;
            if (batchIndex >= lastBatchIndex) return;
            await uploadBatch(batchIndex, false);
          }
        };

        if (lastBatchIndex > 0) {
          const workers = Array.from(
            { length: Math.min(uploadConcurrency, lastBatchIndex) },
            () => worker()
          );
          await Promise.all(workers);
        }

        await uploadBatch(lastBatchIndex, true);
      }

      // Success: update status to queued
      const finalTask = {
        task_id: taskId,
        status: 'queued',
        filename: displayName,
        model_type: modelType,
        survey_id: surveyId,
        created_at: new Date().toISOString(),
        message: 'Đang xếp hàng chờ xử lý...'
      };
      setTasks(prev => prev.map(t => t.task_id === taskId ? finalTask : t));
      setSelectedResult(finalTask);

    } catch (error: any) {
      console.error("Detection start failed", error);
      const errMsg = error.response?.data?.detail || error.message || "Lỗi mạng hoặc tệp quá dung lượng cho phép";
      alert(`Lỗi phân tích AI: ${errMsg}`);
      
      setTasks(prev => prev.map(t => t.task_id === taskId ? {
        ...t,
        status: 'error',
        message: `Lỗi tải lên: ${errMsg}`
      } : t));
      
      // Update selected result if active
      setSelectedResult((prev: any) => prev && prev.task_id === taskId ? {
        ...prev,
        status: 'error',
        message: `Lỗi tải lên: ${errMsg}`
      } : prev);
    }
  };

  const deleteTask = async (taskId: string) => {
    try {
      await crackAPI.deleteTask(taskId);
      setTasks(prev => prev.filter(t => t.task_id !== taskId));
      if (selectedResult?.task_id === taskId) {
        setSelectedResult(null);
      }
    } catch (e) {
      console.error("Failed to delete task", e);
      alert("Lỗi khi xóa tác vụ");
    }
  };

  const retryTask = async (taskId: string) => {
    try {
      await crackAPI.retryTask(taskId);
      setTasks((prev: any[]) => 
        prev.map(t => t.task_id === taskId ? { ...t, status: 'queued', best_frames: [], trackingDataUrl: null } : t)
      );
      if (selectedResult?.task_id === taskId) {
        setSelectedResult((prev: any) => prev ? { ...prev, status: 'queued', best_frames: [], trackingDataUrl: null } : null);
      }
    } catch (e) {
      console.error("Failed to retry task", e);
      alert("Lỗi khi phân tích lại tác vụ");
    }
  };

  return (
    <CrackContext.Provider value={{
      tasks,
      alerts,
      stats,
      chartData,
      selectedResult,
      setSelectedResult,
      startDetection,
      deleteTask,
      retryTask,
      refreshHistory,
      refreshAlerts,
      refreshDashboard,
      isPolling
    }}>
      {children}
    </CrackContext.Provider>
  );
}

export function useCrack() {
  const context = useContext(CrackContext);
  if (!context) {
    throw new Error('useCrack must be used within a CrackProvider');
  }
  return context;
}
