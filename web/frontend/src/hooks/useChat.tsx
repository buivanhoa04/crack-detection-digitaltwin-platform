'use client';

import React, { createContext, useContext, useState, useEffect, useCallback } from 'react';
import { chatbotAPI } from '@/lib/api';
import type { ChatMessage, ChatSession } from '@/types';
import { useAuth } from './useAuth';

interface ChatContextType {
  sessions: ChatSession[];
  activeSession: string;
  messages: ChatMessage[];
  isLoading: boolean;
  setActiveSession: (id: string) => void;
  sendMessage: (content: string) => Promise<void>;
  createSession: () => Promise<void>;
  deleteSession: (id: string) => Promise<void>;
  refreshSessions: () => Promise<void>;
}

const ChatContext = createContext<ChatContextType | undefined>(undefined);

export function ChatProvider({ children }: { children: React.ReactNode }) {
  const { user } = useAuth();
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [activeSession, setActiveSession] = useState<string>('');
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [isLoading, setIsLoading] = useState(false);

  const refreshSessions = useCallback(async () => {
    if (!user) return;
    try {
      const { data } = await chatbotAPI.getSessions();
      // Middleware returns { sessions: [...] }
      const sessionList = data.sessions || [];
      setSessions(sessionList);
      if (sessionList.length > 0 && !activeSession) {
        setActiveSession(sessionList[0].session_id);
      }
    } catch (error) {
      console.error('Failed to fetch sessions:', error);
    }
  }, [user, activeSession]);

  const loadMessages = useCallback(async (sessionId: string) => {
    if (!user || !sessionId) return;
    try {
      const { data } = await chatbotAPI.getMessages(sessionId);
      // Middleware returns { messages: [...] }
      const msgList = data.messages || [];
      
      if (msgList.length === 0) {
        setMessages([{
          id: 'welcome',
          role: 'assistant',
          content: 'Xin chào! Tôi là trợ lý AI kỹ thuật. Hãy đặt câu hỏi cho tôi!',
          timestamp: new Date().toISOString(),
        }]);
      } else {
        setMessages(msgList.map((m: any, idx: number) => ({
          id: `msg_${idx}_${Date.now()}`,
          role: m.role,
          content: m.content,
          timestamp: m.created_at,
          references: m.references || [],
        })));
      }
    } catch (error) {
      console.error('Failed to fetch messages:', error);
    }
  }, [user]);

  useEffect(() => {
    if (user) {
      refreshSessions();
    }
  }, [user, refreshSessions]);

  useEffect(() => {
    if (activeSession) {
      loadMessages(activeSession);
    }
  }, [activeSession, loadMessages]);

  const sendMessage = async (content: string) => {
    if (!content.trim()) return;

    let sessionId = activeSession;
    if (!sessionId) {
      try {
        const { data } = await chatbotAPI.createSession();
        sessionId = data.session_id;
        const newSession: ChatSession = {
          session_id: sessionId,
          title: 'Cuộc trò chuyện mới',
          created_at: new Date().toISOString(),
          message_count: 0,
        };
        setSessions(prev => [newSession, ...prev]);
        setActiveSession(sessionId);
      } catch (error) {
        console.error('Failed to create a chat session:', error);
        return;
      }
    }

    const userMsg: ChatMessage = {
      id: `user_${Date.now()}`,
      role: 'user',
      content,
      timestamp: new Date().toISOString(),
    };

    setMessages(prev => [...prev, userMsg]);
    setIsLoading(true);

    const aiMessageId = `ai_${Date.now()}`;
    const aiMsgPlaceholder: ChatMessage = {
      id: aiMessageId,
      role: 'assistant',
      content: '',
      timestamp: new Date().toISOString(),
      references: [],
    };
    
    setMessages(prev => [...prev, aiMsgPlaceholder]);

    try {
      const { data } = await chatbotAPI.sendMessage(content, sessionId);
      const aiAnswer = data.answer || '';
      
      setMessages(prev => 
        prev.map(m => m.id === aiMessageId ? { ...m, content: aiAnswer } : m)
      );
      
      // Update session title in list if first message
      if (messages.length <= 1) {
        refreshSessions();
      }
    } catch (error) {
      console.error('Chat error:', error);
      setMessages(prev => 
        prev.map(m => m.id === aiMessageId ? { ...m, content: 'Đã xảy ra lỗi khi kết nối với trợ lý AI.' } : m)
      );
    } finally {
      setIsLoading(false);
    }
  };

  const createSession = async () => {
    try {
      const { data } = await chatbotAPI.createSession();
      const newSession: ChatSession = {
        session_id: data.session_id,
        title: 'Cuộc trò chuyện mới',
        created_at: new Date().toISOString(),
        message_count: 0
      };
      
      // Update local state immediately so user sees it
      setSessions(prev => [newSession, ...prev]);
      setActiveSession(data.session_id);
      setMessages([{
        id: 'welcome',
        role: 'assistant',
        content: 'Phiên mới đã được tạo. Tôi có thể giúp gì cho bạn?',
        timestamp: new Date().toISOString(),
      }]);
    } catch (error) {
      console.error('Failed to create session:', error);
    }
  };

  const deleteSession = async (id: string) => {
    const previousSessions = sessions;
    try {
      // Optimistic UI update: Remove from list immediately
      setSessions(prev => prev.filter(s => s.session_id !== id));
      
      // Send delete request to backend
      await chatbotAPI.deleteSession(id);
      
      // If deleted session was the active one, reset
      if (id === activeSession) {
        setMessages([]);
        setActiveSession('');
      }
    } catch (error: any) {
      console.error('Delete session failed:', error);
      setSessions(previousSessions);
    }
  };

  return (
    <ChatContext.Provider value={{
      sessions,
      activeSession,
      messages,
      isLoading,
      setActiveSession,
      sendMessage,
      createSession,
      deleteSession,
      refreshSessions
    }}>
      {children}
    </ChatContext.Provider>
  );
}

export function useChat() {
  const context = useContext(ChatContext);
  if (context === undefined) {
    throw new Error('useChat must be used within a ChatProvider');
  }
  return context;
}
