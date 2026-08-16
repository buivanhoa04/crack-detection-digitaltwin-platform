export interface ProjectModel {
  id: string;
  name: string;
  jobId: string;
  date: string;
  status: 'completed' | 'processing' | 'failed' | 'placeholder';
  surveyId?: string;
  
  // Mapped survey metadata
  surveyName?: string;
  routeName?: string;
  routeKmStart?: number;
  routeKmEnd?: number;
  surveyor?: string;
}

// ── AI Defect Detection Types ────────────────────────

export interface DefectDetection {
  track_id: number;
  class: string;
  class_id?: number;
  confidence: number;
  bbox: number[];
  polygon?: number[][]; // Coordinates [[x1, y1], [x2, y2], ...] normalized to 0-1
  // Kích thước pixel (luôn có)
  pixel_width?: number;
  pixel_area?: number;
  // Kích thước thực tế (chỉ có sau khi hiệu chuẩn GSD)
  real_width_mm?: number;
  real_area_m2?: number;
}

export interface DefectFrame {
  frame_index: number;
  timestamp: string;
  frameFilePath: string;
  bbox?: number[];
  polygon?: number[][];
  detections: DefectDetection[];
}

export interface DefectSeverity {
  level: string;
  label: string;
}

export interface DefectAnalysis {
  description?: string;
  causes?: string[];
  technical_detail?: string;
  conclusion_and_repair_plan?: string;
}

export interface DefectReport {
  task_id: string;
  track_id: number;
  class_name: string;
  defect_code: string;
  defect_name: string;
  confidence: number;
  severity: DefectSeverity;
  analysis: DefectAnalysis;
  analysis_source: string; // 'catalog' | 'vision_llm'
  tcvn_references: string[];
  recommendations: string[];
  frame_index: number;
  timestamp: string;
  frameFilePath: string;
  bbox: number[];
  analyzed_at: string;
  defect_code_mapping?: string;
  current_status_details?: string;
  technical_analysis?: {
    tcvn_references?: string[];
    causes?: string[];
  };
  recommendations_to_contractor?: string[];
  conclusion_and_repair_plan?: string;
}

export interface CalibrationResult {
  is_calibrated: boolean;
  gsd_mm_per_pixel: number;
  calibration_source: string;
  calibration_source_name: string;
  calibration_confidence: number;
  damages: DefectDetection[];
  references_found: {
    class_name: string;
    standard_name: string;
    standard_width_mm: number;
    confidence: number;
  }[];
}

export interface TCVNStandard {
  class_name: string;
  name: string;
  standard_width_mm: number;
  tcvn_code: string;
  description: string;
  priority: number;
}

export interface DefectMarkerData {
  track_id: number;
  class: string;
  confidence: number;
  severity: string;
  frame_index: number;
  timestamp: string;
  frameFilePath: string;
  bbox?: number[];
  polygon?: number[][];
  position3D?: [number, number, number];
  real_width_mm?: number;
  real_area_m2?: number;
}
