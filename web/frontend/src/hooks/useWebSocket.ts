'use client';

import { useEffect, useRef, useState, useCallback } from 'react';

interface UseWebSocketOptions {
  url: string;
  onMessage?: (data: any) => void;
  onError?: (error: Event) => void;
  onOpen?: () => void;
  onClose?: () => void;
  autoConnect?: boolean;
}

export function useWebSocket({
  url,
  onMessage,
  onError,
  onOpen,
  onClose,
  autoConnect = false,
}: UseWebSocketOptions) {
  const wsRef = useRef<WebSocket | null>(null);
  const [isConnected, setIsConnected] = useState(false);
  const [lastMessage, setLastMessage] = useState<any>(null);

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return;

    const token = localStorage.getItem('access_token');
    const parsedUrl = new URL(url, window.location.href);
    if (token) parsedUrl.searchParams.set('token', token);
    const wsUrl = parsedUrl.toString();

    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;

    ws.onopen = () => {
      setIsConnected(true);
      onOpen?.();
    };

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        setLastMessage(data);
        onMessage?.(data);
      } catch {
        setLastMessage(event.data);
        onMessage?.(event.data);
      }
    };

    ws.onerror = (error) => {
      onError?.(error);
    };

    ws.onclose = () => {
      setIsConnected(false);
      onClose?.();
    };
  }, [url, onMessage, onError, onOpen, onClose]);

  const disconnect = useCallback(() => {
    wsRef.current?.close();
    wsRef.current = null;
    setIsConnected(false);
  }, []);

  const sendMessage = useCallback((data: any) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(typeof data === 'string' ? data : JSON.stringify(data));
    }
  }, []);

  useEffect(() => {
    if (autoConnect) connect();
    return () => disconnect();
  }, [autoConnect, connect, disconnect]);

  return { isConnected, lastMessage, connect, disconnect, sendMessage };
}
