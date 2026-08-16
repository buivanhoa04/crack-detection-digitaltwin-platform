/* ── TypeScript Interfaces & Types ──────────────────── */
/* v2.0: Expanded with Survey, TCVN Grade, Repair Workflow */

// ── Auth Types ──────────────────────────────────────────
export interface User {
  id: string;
  email: string;
  full_name: string;
  role: 'admin' | 'user';
  avatar?: string;
  is_active?: boolean;
  created_at?: string;
  last_login?: string;
  created_by?: string;
}

export interface AuthTokens {
  access_token: string;
  refresh_token: string;
  token_type: string;
  user: User;
}

export interface LoginCredentials {
  email: string;
  password: string;
}

// ── Crack Detection Types ───────────────────────────────
export interface DetectionTask {
  task_id: string;
  status: 'queued' | 'processing' | 'done' | 'error';
  progress?: number;
  processingStatus?: string;
  fps?: number;
  eta_seconds?: number;
  elapsed_seconds?: number;
  processed_count?: number;
  total_count?: number;
  error_count?: number;
  created_at?: string;
  filename?: string;
  survey_id?: string;
}

export interface BoundingBox {
  x: number;
  y: number;
  width: number;
  height: number;
  confidence: number;
  label?: string;
}

export interface CrackFrame {
  frame_id: string;
  image_url: string;
  bboxes: BoundingBox[];
  confidence: number;
  timestamp?: string;
}

export interface DetectionResult {
  task_id: string;
  status: string;
  best_frames?: CrackFrame[];
  total_cracks?: number;
  processing_time?: number;
}

// ── Chatbot Types ───────────────────────────────────────
export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  timestamp: string;
  references?: DocumentReference[];
  tokens_used?: TokenUsage;
}

export interface DocumentReference {
  filename: string;
  page?: number;
  chunk_text?: string;
  score?: number;
}

export interface TokenUsage {
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
}

export interface ChatSession {
  session_id: string;
  title: string;
  created_at: string;
  message_count: number;
  last_message?: string;
}

export interface Document {
  id: string;
  filename: string;
  status: 'parsing' | 'success' | 'fail';
  size?: number;
  uploaded_at?: string;
  progress?: number;
  chunks_count?: number;
  uploaded_by?: string;
}

// ── Survey / Campaign Types (v2.0) ──────────────────────
export interface Survey {
  id: string;
  name: string;
  route_name: string;
  route_km_start: number;
  route_km_end: number;
  surveyor: string;
  method: 'vehicle' | 'drone' | 'walking';
  status: 'active' | 'completed' | 'cancelled';
  task_count: number;
  notes: string;
  created_by: string;
  created_at: string;
  updated_at?: string;
  tasks?: any[];
}

// ── Incident Types (v2.0 — Full Business Logic) ─────────
export interface Incident {
  id: string;
  title: string;
  description: string;
  severity: string;
  status: string;
  lat: number;
  lng: number;
  address: string;
  detected_at: string;
  detected_by: string;
  images: string[];
  confidence: number;
  crack_length_m: number;
  created_by: string;
  created_at: string;
  updated_at?: string;
  notes: string;
  detections?: any[];
  asset_type?: string;
  classification?: string;
  approved_by?: string;
  approved_at?: string;
  repaired_at?: string;
  // v2.0: Extended business fields
  route_name?: string;
  route_km?: number;
  lane_position?: string;
  tcvn_grade?: string;
  tcvn_grade_auto?: string;
  survey_id?: string;
  repair_status?: string;
  damage_area_m2?: number;
  damage_width_mm?: number;
  repair_method?: string;
  repair_cost_vnd?: number;
  contractor?: string;
  // v3.0: Calibration
  gsd_mm_per_pixel?: number;
  calibration_source?: string;
  is_calibrated?: boolean;
}



// ── Audit Log Types ─────────────────────────────────────
export interface AuditLogEntry {
  id: string;
  user_id: string;
  user_email: string;
  action: string;
  target: string;
  details: string;
  ip_address: string;
  timestamp: string;
}

export interface PaginatedResponse<T> {
  items: T[];
  total: number;
  page: number;
  limit: number;
  total_pages: number;
}

// ── Health Types ────────────────────────────────────────
export interface ServiceHealth {
  service: string;
  status: 'healthy' | 'unhealthy' | 'degraded' | 'unknown';
  response_time_ms?: number;
  details?: Record<string, any>;
}

export interface SystemHealth {
  status: string;
  services: ServiceHealth[];
  timestamp: string;
}

// ── Dashboard Types ─────────────────────────────────────
export interface DashboardStats {
  totalScans: number;
  totalCracks: number;
  avgProcessingTime: number;
  activeStreams: number;
  documentsUploaded: number;
  chatSessions: number;
}

export interface Alert {
  id: string;
  type: 'critical' | 'warning' | 'info';
  title: string;
  message: string;
  timestamp: string;
  read: boolean;
}
