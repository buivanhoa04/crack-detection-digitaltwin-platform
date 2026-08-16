'use client';

import { useState, useRef, useEffect } from 'react';
import {
  Send,
  Plus,
  Trash2,
  MessageSquare,
  Bot,
  User,
  FileText,
  Upload,
  CheckCircle2,
  Loader2,
  AlertCircle,
  Copy,
  RefreshCw,
  Paperclip,
} from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { chatbotAPI } from '@/lib/api';
import type { ChatMessage, ChatSession, Document } from '@/types';
import { useChat } from '@/hooks/useChat';

export default function ChatbotPage() {
  const [mounted, setMounted] = useState(false);
  const {
    sessions,
    activeSession,
    messages,
    isLoading,
    setActiveSession,
    sendMessage,
    createSession,
    deleteSession
  } = useChat();

  const [input, setInput] = useState('');
  const messagesEndRef = useRef<HTMLDivElement>(null);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  useEffect(() => {
    setMounted(true);
  }, []);

  useEffect(() => {
    scrollToBottom();
  }, [messages]);

  const handleSend = async () => {
    if (!input.trim() || isLoading) return;
    const content = input;
    setInput('');
    await sendMessage(content);
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const handleDeleteSession = async (sessionId: string, e: React.MouseEvent) => {
    e.stopPropagation();
    // Bỏ qua confirm để dọn dẹp nhanh theo yêu cầu người dùng
    await deleteSession(sessionId);
  };

  const copyMessage = (content: string) => {
    navigator.clipboard.writeText(content);
  };

  return (
    <div className="h-[calc(100vh-var(--topbar-height)-3rem)] flex gap-4 animate-fade-in">
      {/* ── Sessions Sidebar ─────────────────────────── */}
      <div className="w-64 shrink-0 glass-card flex flex-col border-r border-slate-100 bg-white/50">
        <div className="p-3 border-b border-slate-100">
          <button
            onClick={createSession}
            className="btn-gradient w-full flex items-center justify-center gap-2 text-xs py-2 text-white shadow-md active:scale-95 transition-transform"
            id="new-session"
          >
            <Plus className="w-3.5 h-3.5" />
            Phiên mới
          </button>
        </div>

        {/* Session List */}
        <div className="flex-1 overflow-y-auto p-2 space-y-1 custom-scrollbar">
          {sessions.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-full text-slate-400 p-4 text-center">
              <MessageSquare className="w-8 h-8 mb-2 opacity-20" />
              <p className="text-[10px] font-medium leading-relaxed">
                Chưa có phiên chat nào.<br/>Nhấn <b>"Phiên mới"</b> để bắt đầu.
              </p>
            </div>
          ) : (
            sessions.map((session) => (
              <div
                key={session.session_id}
                onClick={() => setActiveSession(session.session_id)}
                className={`w-full flex items-center gap-2.5 px-3 py-2.5 rounded-lg text-left transition-all duration-200 group cursor-pointer relative ${
                  activeSession === session.session_id
                    ? 'bg-blue-600 text-white shadow-lg shadow-blue-600/20'
                    : 'text-slate-500 hover:bg-slate-100 hover:text-slate-900 border border-transparent hover:border-slate-200'
                }`}
              >
                <MessageSquare className={`w-4 h-4 shrink-0 ${activeSession === session.session_id ? 'text-white' : 'text-blue-500'}`} />
                <span className="text-[10px] font-semibold truncate pr-8">{session.title}</span>
                
                <button 
                  onClick={(e) => handleDeleteSession(session.session_id, e)}
                  className={`absolute right-1.5 p-1.5 rounded-md transition-all flex items-center justify-center z-10 ${
                    activeSession === session.session_id 
                      ? 'bg-white/10 text-white/70 hover:text-white hover:bg-white/20' 
                      : 'opacity-0 group-hover:opacity-100 bg-slate-50 text-slate-400 hover:text-red-500 hover:bg-red-50 shadow-sm'
                  }`}
                  title="Xóa phiên"
                >
                  <Trash2 className="w-3.5 h-3.5" />
                </button>
              </div>
            ))
          )}
        </div>
      </div>

      {/* ── Main Chat Area ───────────────────────────── */}
      <div className="flex-1 flex flex-col glass-card bg-white">
        {/* Chat Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-slate-100 bg-slate-50/50">
          <div className="flex items-center gap-2">
            <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-emerald-500 to-teal-600 flex items-center justify-center shadow-lg shadow-emerald-500/20">
              <Bot className="w-4 h-4 text-white" />
            </div>
            <div>
              <p className="text-xs font-bold text-slate-800">Trợ lý AI Kỹ thuật</p>
              <p className="text-[10px] text-slate-400 font-medium">RAGFlow • Tra cứu TCVN</p>
            </div>
          </div>
          <div className="flex items-center gap-1">
            <div className="glow-dot green" />
            <span className="text-[10px] text-emerald-600 font-bold">Live System</span>
          </div>
        </div>

        {/* Messages */}
        <div className="flex-1 overflow-y-auto p-4 space-y-4">
          {messages.map((msg) => (
            <div
              key={msg.id}
              className={`flex gap-3 ${
                msg.role === 'user' ? 'justify-end' : 'justify-start'
              } animate-slide-up`}
            >
              {msg.role === 'assistant' && (
                <div className="w-7 h-7 rounded-lg bg-gradient-to-br from-emerald-500 to-teal-600 flex items-center justify-center shrink-0 mt-1">
                  <Bot className="w-3.5 h-3.5 text-white" />
                </div>
              )}
              <div className={`max-w-[85%] flex flex-col ${msg.role === 'user' ? 'items-end' : 'items-start'}`}>
                <div
                  className={
                    msg.role === 'user' ? 'chat-bubble-user w-fit' : 'chat-bubble-ai w-fit'
                  }
                >
                  {msg.role === 'user' ? (
                    <p className="whitespace-pre-wrap leading-relaxed text-[15px]">{msg.content}</p>
                  ) : (
                    <div className="markdown-content text-[15px]">
                      {mounted ? (
                        <ReactMarkdown remarkPlugins={[remarkGfm]}>
                          {msg.content}
                        </ReactMarkdown>
                      ) : (
                        <p className="whitespace-pre-wrap leading-relaxed text-[15px]">{msg.content}</p>
                      )}
                    </div>
                  )}
                </div>
                <div className={`flex items-center gap-2 mt-1.5 px-1`}>
                  <span className="text-[9px] text-slate-400 font-medium">
                    {(() => {
                      try {
                        if (!msg.timestamp) return '';
                        const tsStr = String(msg.timestamp);
                        const date = new Date(tsStr);
                        if (isNaN(date.getTime())) return '';
                        
                        const utcDate = tsStr.includes('Z') || tsStr.includes('+') 
                          ? date 
                          : new Date(tsStr + 'Z');
                          
                        if (isNaN(utcDate.getTime())) return '';
                        
                        return utcDate.toLocaleTimeString('vi-VN', { 
                          hour: '2-digit', 
                          minute: '2-digit', 
                          timeZone: 'Asia/Ho_Chi_Minh' 
                        });
                      } catch (e) {
                        return '';
                      }
                    })()}
                  </span>
                  {msg.role === 'assistant' && msg.id !== 'welcome' && (
                    <button
                      onClick={() => copyMessage(msg.content)}
                      className="text-slate-600 hover:text-slate-400 transition-colors"
                    >
                      <Copy className="w-3 h-3" />
                    </button>
                  )}
                  {msg.tokens_used && (
                    <span className="text-[9px] text-slate-700">
                      {msg.tokens_used.total_tokens} tokens
                    </span>
                  )}
                </div>
                {/* References */}
                {msg.references && msg.references.length > 0 && (
                  <div className="mt-2 space-y-1">
                    {msg.references.map((ref, i) => (
                      <div
                        key={i}
                        className="flex items-center gap-1.5 px-2 py-1 rounded-md bg-blue-500/5 border border-blue-500/10 text-[9px] text-blue-400"
                      >
                        <FileText className="w-3 h-3" />
                        {ref.filename}
                        {ref.page && ` (trang ${ref.page})`}
                      </div>
                    ))}
                  </div>
                )}
              </div>
              {msg.role === 'user' && (
                <div className="w-7 h-7 rounded-lg bg-gradient-to-br from-blue-500 to-indigo-600 flex items-center justify-center shrink-0 mt-1">
                  <User className="w-3.5 h-3.5 text-white" />
                </div>
              )}
            </div>
          ))}
          {isLoading && (
            <div className="flex gap-3 animate-fade-in">
              <div className="w-7 h-7 rounded-lg bg-gradient-to-br from-emerald-500 to-teal-600 flex items-center justify-center shrink-0">
                <Bot className="w-3.5 h-3.5 text-white" />
              </div>
              <div className="chat-bubble-ai">
                <div className="flex items-center gap-1.5">
                  <div className="w-1.5 h-1.5 rounded-full bg-slate-500 animate-bounce" style={{ animationDelay: '0s' }} />
                  <div className="w-1.5 h-1.5 rounded-full bg-slate-500 animate-bounce" style={{ animationDelay: '0.2s' }} />
                  <div className="w-1.5 h-1.5 rounded-full bg-slate-500 animate-bounce" style={{ animationDelay: '0.4s' }} />
                </div>
              </div>
            </div>
          )}
          <div ref={messagesEndRef} />
        </div>

        {/* Input Area */}
        <div className="p-4 border-t border-slate-100 bg-white shadow-[0_-4px_20px_rgba(0,0,0,0.02)]">
          <div className="max-w-4xl mx-auto flex items-end gap-3">
            <div className="flex-1 relative">
              <textarea
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder="Nhập câu hỏi về tiêu chuẩn TCVN, kỹ thuật công trình..."
                className="w-full p-4 pr-12 rounded-xl bg-slate-50 border border-slate-200 focus:bg-white focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 transition-all resize-none min-h-[100px] max-h-48 text-[15px] leading-relaxed custom-scrollbar"
                rows={2}
                id="chat-input"
              />
              <div className="absolute right-3 bottom-3 text-[10px] text-slate-400 font-medium">
                Enter để gửi
              </div>
            </div>
            <button
              onClick={handleSend}
              disabled={!input.trim() || isLoading}
              className="btn-gradient p-4 disabled:opacity-50 text-white shadow-lg shadow-blue-600/20 active:scale-95 transition-transform rounded-xl"
              id="send-message"
            >
              <Send className="w-5 h-5" />
            </button>
          </div>
        </div>
      </div>


    </div>
  );
}
