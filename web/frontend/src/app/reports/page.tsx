'use client';

import { useState, useEffect } from 'react';
import Link from 'next/link';
import { 
  FileBarChart, Download, Calendar, Filter, Database, 
  ChevronRight, ArrowRight, ShieldAlert, CheckCircle2, 
  Clock, Wrench, Construction, FileText, MapPin, 
  BarChart3, PieChart, Activity, ExternalLink
} from 'lucide-react';
import { surveysAPI, incidentsAPI } from '@/lib/api';
import { TCVN_GRADES, translateAIClass, REPAIR_STATUSES, calculatePavementIndex, getPavementLabel } from '@/lib/translate';
import { withAccessToken } from '@/lib/mediaAuth';

export default function ReportsPage() {
  const [surveys, setSurveys] = useState<any[]>([]);
  const [selectedSurveyId, setSelectedSurveyId] = useState<string>('');
  const [summary, setSummary] = useState<any>(null);
  const [incidents, setIncidents] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    setLoading(true);
    surveysAPI.getAll()
      .then(({ data }) => {
        const list = data.surveys || [];
        setSurveys(list);
        if (list.length > 0) setSelectedSurveyId(list[0].id);
      })
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    if (!selectedSurveyId) return;
    
    setLoading(true);
    incidentsAPI.getAll({
      survey_id: selectedSurveyId,
      approved_only: true,
    }).then((incidentsRes) => {
      const activeList = incidentsRes.data?.incidents || [];
      setIncidents(activeList);
      const gradeCounts: Record<string, number> = { A: 0, B: 0, C: 0, D: 0, E: 0 };
      const statusCounts: Record<string, number> = {};
      activeList.forEach((inc: any) => {
        const grade = String(inc.tcvn_grade || '').toUpperCase();
        if (gradeCounts[grade] !== undefined) gradeCounts[grade]++;
        const repairStatus = String(inc.repair_status || 'not_updated');
        statusCounts[repairStatus] = (statusCounts[repairStatus] || 0) + 1;
      });
      setSummary({
        total_incidents: activeList.length,
        by_grade: gradeCounts,
        by_status: statusCounts,
      });
    }).finally(() => setLoading(false));
  }, [selectedSurveyId]);

  const selectedSurvey = surveys.find(s => s.id === selectedSurveyId);

  const getStatusColorClass = (status: string) => {
    switch (status) {
      case 'completed': return 'bg-emerald-500';
      case 'repairing': return 'bg-orange-500';
      case 'scheduled': return 'bg-yellow-500';
      case 'detected':
      default:
        return 'bg-blue-500';
    }
  };

  const getPavementStyle = (pqi: number) => {
    const labelInfo = getPavementLabel(pqi);
    let textClass = 'text-emerald-600';
    let bgClass = 'bg-emerald-50';
    let textBadgeClass = 'text-emerald-700';
    let borderClass = 'border-emerald-200';

    if (labelInfo.color === 'blue') {
      textClass = 'text-blue-600';
      bgClass = 'bg-blue-50';
      textBadgeClass = 'text-blue-700';
      borderClass = 'border-blue-200';
    } else if (labelInfo.color === 'yellow') {
      textClass = 'text-yellow-600';
      bgClass = 'bg-yellow-50';
      textBadgeClass = 'text-yellow-700';
      borderClass = 'border-yellow-200';
    } else if (labelInfo.color === 'orange') {
      textClass = 'text-orange-600';
      bgClass = 'bg-orange-50';
      textBadgeClass = 'text-orange-700';
      borderClass = 'border-orange-200';
    } else if (labelInfo.color === 'red') {
      textClass = 'text-red-600';
      bgClass = 'bg-red-50';
      textBadgeClass = 'text-red-700';
      borderClass = 'border-red-200';
    }

    return { textClass, bgClass, textBadgeClass, borderClass, label: labelInfo.label };
  };

  const handleExportPDF = () => {
    if (!summary || !selectedSurvey || incidents.length === 0) {
      alert('Chưa có sự cố đã duyệt thuộc đúng đợt khảo sát này để xuất báo cáo TCVN.');
      return;
    }

    const pavementIndex = calculatePavementIndex(incidents);
    const pavementLabel = getPavementLabel(pavementIndex);
    const today = new Date().toLocaleDateString('vi-VN');

    // Build incidents rows
    const incidentRowsHtml = incidents.map((inc, index) => {
      let imgUrl = '';
      if (inc.images && inc.images.length > 0) {
        const imgPath = inc.images[0];
        if (imgPath.startsWith('http')) {
          imgUrl = imgPath;
        } else {
          // Standardize path to use secure reverse-proxy URL
          const cleanPath = imgPath
            .replace(/\\/g, '/')
            .replace(/^(\/)?api\/v1\/files\//, '')
            .replace(/^(\/)?files\//, '')
            .replace(/^\//, '');
          imgUrl = withAccessToken(`${window.location.origin}/api/v1/files/${cleanPath}`);
        }
      }

      const incId = inc.id || inc._id || '';
      const displayId = incId ? incId.slice(-6).toUpperCase() : 'N/A';

      return `
        <tr>
          <td style="text-align: center;">${index + 1}</td>
          <td><b>${translateAIClass(inc.classification)}</b><br/><small style="color: #64748b;">${displayId}</small></td>
          <td style="text-align: center;">Km ${inc.route_km ?? '---'}<br/><small style="color: #64748b;">Tuyến ${inc.route_name || 'Chưa cập nhật'}</small></td>
          <td style="text-align: center; font-weight: bold;">Hạng ${inc.tcvn_grade || '---'}</td>
          <td>${inc.damage_area_m2 ?? '—'} m²<br/>${inc.damage_width_mm ?? '—'} mm (Bề rộng)</td>
          <td style="text-align: center;">${inc.detected_by || 'Chưa cập nhật'}</td>
          <td>${inc.repair_method || 'Chưa có phương án được duyệt'}</td>
          <td style="text-align: center;">
            ${imgUrl ? `<img src="${imgUrl}" style="max-width: 90px; max-height: 70px; border-radius: 4px; border: 1px solid #e2e8f0; display: block; margin: 0 auto;"/>` : '<span style="color: #cbd5e1; font-size: 10px;">Không có ảnh</span>'}
          </td>
        </tr>
      `;
    }).join('');

    const tcvnRowsHtml = (['A', 'B', 'C', 'D', 'E'] as const).map(g => {
      const count = summary.by_grade?.[g] || 0;
      const pct = Math.round((count / (summary.total_incidents || 1)) * 100);
      return `
        <tr>
          <td style="font-weight: bold; text-align: center;">Hạng ${g}</td>
          <td>${TCVN_GRADES[g]?.label || 'Chưa phân loại'}</td>
          <td style="text-align: center; font-weight: bold;">${count}</td>
          <td style="text-align: center;">${pct}%</td>
        </tr>
      `;
    }).join('');

    const htmlContent = `
      <!DOCTYPE html>
      <html>
      <head>
        <title>Bao_cao_TCVN_${selectedSurvey.name.replace(/\s+/g, '_')}</title>
        <meta charset="utf-8" />
        <style>
          body {
            font-family: 'Times New Roman', Times, serif;
            font-size: 13pt;
            line-height: 1.45;
            color: #0f172a;
            margin: 20mm 15mm 20mm 20mm;
          }
          .header-table {
            width: 100%;
            border-collapse: collapse;
            margin-bottom: 25px;
          }
          .header-table td {
            vertical-align: top;
            border: none;
            padding: 0;
          }
          .header-left {
            text-align: center;
            width: 45%;
            font-size: 11pt;
          }
          .header-left-title {
            text-transform: uppercase;
            font-weight: bold;
          }
          .header-right {
            text-align: center;
            width: 55%;
            font-size: 11pt;
          }
          .header-right-title {
            font-weight: bold;
            text-transform: uppercase;
          }
          .national-divider {
            width: 100px;
            height: 1px;
            background-color: #000;
            margin: 4px auto 0 auto;
          }
          .doc-title {
            text-align: center;
            font-weight: bold;
            font-size: 15pt;
            margin-top: 30px;
            margin-bottom: 5px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
          }
          .doc-subtitle {
            text-align: center;
            font-style: italic;
            font-size: 12pt;
            margin-bottom: 35px;
          }
          .section-title {
            font-weight: bold;
            font-size: 13pt;
            text-transform: uppercase;
            margin-top: 25px;
            margin-bottom: 10px;
            border-bottom: 1.5px solid #000;
            padding-bottom: 3px;
          }
          .meta-list {
            margin-bottom: 20px;
          }
          .meta-item {
            margin-bottom: 6px;
          }
          .meta-label {
            font-weight: bold;
            display: inline-block;
            width: 180px;
          }
          table.data-table {
            width: 100%;
            border-collapse: collapse;
            margin-top: 15px;
            margin-bottom: 20px;
            font-size: 11pt;
          }
          table.data-table th {
            background-color: #f8fafc;
            font-weight: bold;
            text-transform: uppercase;
            font-size: 9.5pt;
            border: 1px solid #000;
            padding: 8px;
            text-align: center;
          }
          table.data-table td {
            border: 1px solid #000;
            padding: 8px;
            vertical-align: middle;
          }
          .pavement-score-card {
            border: 1px solid #000;
            background-color: #f8fafc;
            border-radius: 6px;
            padding: 15px;
            margin-top: 15px;
            margin-bottom: 20px;
            display: flex;
            align-items: center;
            justify-content: space-between;
          }
          .pavement-score {
            font-size: 24pt;
            font-weight: bold;
            color: #047857;
          }
          .page-break {
            page-break-before: always;
          }
          .signature-table {
            width: 100%;
            margin-top: 50px;
            border-collapse: collapse;
            page-break-inside: avoid;
          }
          .signature-table td {
            border: none;
            text-align: center;
            width: 33%;
            font-size: 11pt;
            vertical-align: top;
            padding: 10px 0;
          }
          .signature-title {
            font-weight: bold;
            margin-bottom: 60px;
          }
        </style>
      </head>
      <body>
        <!-- Header -->
        <table class="header-table">
          <tr>
            <td class="header-left">
              BỘ GIAO THÔNG VẬN TẢI<br/>
              <span class="header-left-title"><b>CỤC ĐƯỜNG BỘ VIỆT NAM</b></span>
              <div class="national-divider" style="width: 120px;"></div>
            </td>
            <td class="header-right">
              <span class="header-right-title"><b>CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM</b></span><br/>
              <b>Độc lập - Tự do - Hạnh phúc</b>
              <div class="national-divider" style="width: 150px;"></div>
            </td>
          </tr>
        </table>

        <!-- Document Title -->
        <div class="doc-title">BÁO CÁO KỸ THUẬT TUẦN KIỂM &amp; ĐÁNH GIÁ MẶT ĐƯỜNG</div>
        <div class="doc-subtitle">Đợt khảo sát: ${selectedSurvey.name} (Tuyến: ${selectedSurvey.route_name})</div>

        <!-- Section 1: Thông tin chung -->
        <div class="section-title">I. THÔNG TIN CHUNG ĐỢT KHẢO SÁT</div>
        <div class="meta-list">
          <div class="meta-item"><span class="meta-label">Đợt khảo sát:</span> ${selectedSurvey.name}</div>
          <div class="meta-item"><span class="meta-label">Tuyến đường:</span> ${selectedSurvey.route_name}</div>
          <div class="meta-item"><span class="meta-label">Phạm vi khảo sát:</span> Km ${selectedSurvey.route_km_start} - Km ${selectedSurvey.route_km_end}</div>
          <div class="meta-item"><span class="meta-label">Người khảo sát:</span> ${selectedSurvey.surveyor || 'Chưa cập nhật'}</div>
          <div class="meta-item"><span class="meta-label">Thời điểm khảo sát:</span> ${selectedSurvey.created_at ? selectedSurvey.created_at.split('T')[0] : today}</div>
          <div class="meta-item"><span class="meta-label">Ghi chú:</span> ${selectedSurvey.notes || 'Không có ghi chú thêm'}</div>
        </div>

        <!-- Section 2: Đánh giá chất lượng -->
        <div class="section-title">II. ĐÁNH GIÁ CHẤT LƯỢNG MẶT ĐƯỜNG (TCVN 8866)</div>
        <p>Tổng hợp kết quả hư hỏng mặt đường bê tông nhựa trên phạm vi khảo sát:</p>
        
        <div class="pavement-score-card">
          <div>
            <b>Chỉ số Đánh giá Chất lượng mặt đường (Pavement Index - PI):</b><br/>
            <small style="color: #64748b;">Tính toán theo quy chuẩn TCVN 8866 dựa trên khối lượng khuyết tật phát hiện tự động bởi AI.</small>
          </div>
          <div style="text-align: right; display: flex; align-items: center; gap: 15px;">
            <span class="pavement-score">${pavementIndex.toFixed(1)}</span>
            <span style="font-weight: bold; background-color: #ecfdf5; border: 1px solid #10b981; padding: 4px 8px; border-radius: 6px; font-size: 10pt; color: #047857; text-transform: uppercase;">
              Hạng ${pavementLabel.label}
            </span>
          </div>
        </div>

        <table class="data-table">
          <thead>
            <tr>
              <th>Phân hạng chất lượng</th>
              <th>Định nghĩa theo tiêu chuẩn TCVN</th>
              <th>Số lượng phát hiện (Sự cố)</th>
              <th>Tỉ lệ phần trăm (%)</th>
            </tr>
          </thead>
          <tbody>
            ${tcvnRowsHtml}
          </tbody>
        </table>

        <!-- Section 3: Bảng danh sách chi tiết -->
        <div class="page-break"></div>
        <div class="section-title">III. DANH SÁCH CHI TIẾT CÁC HƯ HỎNG PHÁT HIỆN QUA AI SCANS</div>
        <table class="data-table">
          <thead>
            <tr>
              <th style="width: 40px;">STT</th>
              <th>Loại khuyết tật</th>
              <th>Lý trình Km</th>
              <th>TCVN Grade</th>
              <th>Kích thước vật lý (GSD)</th>
              <th>Nguồn đo</th>
              <th>Khuyến nghị khắc phục</th>
              <th style="width: 100px;">Ảnh hiện trạng</th>
            </tr>
          </thead>
          <tbody>
            ${incidentRowsHtml || '<tr><td colspan="8" style="text-align: center; color: #94a3b8;">Không có dữ liệu hư hỏng nào được duyệt trong đợt này.</td></tr>'}
          </tbody>
        </table>

        <!-- Section 4: Chữ ký phê duyệt -->
        <table class="signature-table">
          <tr>
            <td>
              <span class="signature-title">NGƯỜI LẬP BÁO CÁO</span><br/>
              <i>(Ký và ghi rõ họ tên)</i>
            </td>
            <td>
              <span class="signature-title">BỘ PHẬN KIỂM DUYỆT</span><br/>
              <i>(Ký và ghi rõ họ tên)</i>
            </td>
            <td>
              Tỉnh/Thành phố, ngày ${today.split('/')[0]} tháng ${today.split('/')[1]} năm ${today.split('/')[2]}<br/>
              <span class="signature-title">LÃNH ĐẠO PHÊ DUYỆT</span><br/>
              <i>(Ký tên và đóng dấu)</i>
            </td>
          </tr>
        </table>
      </body>
      </html>
    `;

    // Print using a hidden iframe to prevent popup blocking
    let iframe = document.getElementById('print-iframe') as HTMLIFrameElement;
    if (!iframe) {
      iframe = document.createElement('iframe');
      iframe.id = 'print-iframe';
      iframe.style.position = 'fixed';
      iframe.style.right = '0';
      iframe.style.bottom = '0';
      iframe.style.width = '0';
      iframe.style.height = '0';
      iframe.style.border = 'none';
      document.body.appendChild(iframe);
    }

    const doc = iframe.contentDocument || iframe.contentWindow?.document;
    if (!doc) {
      alert('Không thể tạo luồng in. Vui lòng thử lại.');
      return;
    }

    doc.open();
    doc.write(htmlContent);
    doc.close();

    // Trigger printing
    setTimeout(() => {
      if (iframe.contentWindow) {
        iframe.contentWindow.focus();
        iframe.contentWindow.print();
      }
    }, 500);
  };

  return (
    <div className="space-y-8 animate-fade-in pb-20 text-slate-800">
      {/* ── HEADER & CONTROLS ────────────────────────────────── */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-6 bg-white p-8 rounded-[2.5rem] border border-slate-200/60 shadow-sm">
        <div className="flex items-center gap-5">
           <div className="w-16 h-16 rounded-2xl bg-emerald-50 flex items-center justify-center border border-emerald-100 shadow-md shadow-emerald-500/5">
              <FileBarChart className="w-8 h-8 text-emerald-600" />
           </div>
           <div>
              <h1 className="text-xl font-black text-slate-800 tracking-tight flex items-center gap-3">
                 Báo cáo Kỹ thuật TCVN 
              </h1>
              <p className="text-slate-500 text-sm font-medium">Phân tích đợt khảo sát &amp; Hồ sơ bảo trì hạ tầng</p>
           </div>
        </div>

        <div className="flex flex-wrap items-center gap-4">
           <div className="space-y-1">
              <label className="text-[10px] font-black text-slate-400 uppercase tracking-widest pl-1">Chọn đợt khảo sát</label>
              <select 
                value={selectedSurveyId} 
                onChange={e => setSelectedSurveyId(e.target.value)}
                className="bg-white border border-slate-200 rounded-2xl px-6 py-3.5 text-sm text-slate-800 font-bold outline-none focus:border-emerald-500 transition-all min-w-[280px] shadow-sm cursor-pointer"
              >
                {surveys.map(s => (
                  <option key={s.id} value={s.id}>{s.name} ({s.route_name})</option>
                ))}
              </select>
           </div>
           
           <div className="pt-4">
              <button 
                onClick={handleExportPDF}
                className="bg-emerald-600 hover:bg-emerald-700 text-white px-8 py-3.5 rounded-2xl flex items-center gap-3 transition-all text-xs font-black uppercase tracking-widest shadow-lg shadow-emerald-600/10 active:scale-95"
              >
                <Download className="w-4 h-4" /> Xuất Báo cáo PDF
              </button>
           </div>
        </div>
      </div>

      {loading && (
        <div className="flex flex-col items-center justify-center py-20 gap-4">
           <div className="w-12 h-12 border-4 border-emerald-500/20 border-t-emerald-500 rounded-full animate-spin" />
           <p className="text-slate-500 font-bold text-xs uppercase tracking-widest animate-pulse">Đang nạp dữ liệu kỹ thuật...</p>
        </div>
      )}

      {!loading && summary && (
        <>
          {/* ── STATS DASHBOARD ───────────────────────────────── */}
          <div className="grid grid-cols-1 md:grid-cols-4 gap-6">
             <div className="bg-white border border-slate-200/60 p-6 rounded-3xl hover:border-blue-500/20 hover:shadow-md transition-all group shadow-sm">
                <div className="flex items-center justify-between mb-4">
                   <div className="p-3 rounded-xl bg-blue-50 text-blue-600"><Database className="w-5 h-5" /></div>
                   <span className="text-[10px] font-black text-slate-400 uppercase">Tổng sự cố</span>
                </div>
                <p className="text-xl font-black text-slate-800">{summary.total_incidents}</p>
                <div className="mt-2 h-1.5 w-full bg-slate-100 rounded-full overflow-hidden">
                   <div className="h-full bg-blue-500" style={{width: '100%'}} />
                </div>
             </div>

             <div className="bg-white border border-slate-200/60 p-6 rounded-3xl hover:border-red-500/20 hover:shadow-md transition-all shadow-sm">
                <div className="flex items-center justify-between mb-4">
                   <div className="p-3 rounded-xl bg-rose-50 text-rose-600"><ShieldAlert className="w-5 h-5" /></div>
                   <span className="text-[10px] font-black text-slate-400 uppercase">Nguy hiểm (D-E)</span>
                </div>
                <p className="text-xl font-black text-slate-800">{(summary.by_grade?.D || 0) + (summary.by_grade?.E || 0)}</p>
                <div className="mt-2 h-1.5 w-full bg-slate-100 rounded-full overflow-hidden">
                   <div className="h-full bg-rose-500" style={{width: `${((summary.by_grade?.D || 0) + (summary.by_grade?.E || 0)) / (summary.total_incidents || 1) * 100}%`}} />
                </div>
             </div>

             <div className="bg-white border border-slate-200/60 p-6 rounded-3xl hover:border-amber-500/20 hover:shadow-md transition-all shadow-sm">
                <div className="flex items-center justify-between mb-4">
                   <div className="p-3 rounded-xl bg-amber-50 text-amber-600"><Construction className="w-5 h-5" /></div>
                   <span className="text-[10px] font-black text-slate-400 uppercase">Đang sửa chữa</span>
                </div>
                <p className="text-xl font-black text-slate-800">{summary.by_status?.in_repair || 0}</p>
                <div className="mt-2 h-1.5 w-full bg-slate-100 rounded-full overflow-hidden">
                   <div className="h-full bg-amber-500" style={{width: `${(summary.by_status?.in_repair || 0) / (summary.total_incidents || 1) * 100}%`}} />
                </div>
             </div>

             <div className="bg-white border border-slate-200/60 p-6 rounded-3xl hover:border-emerald-500/20 hover:shadow-md transition-all shadow-sm">
                <div className="flex items-center justify-between mb-4">
                   <div className="p-3 rounded-xl bg-emerald-50 text-emerald-600"><CheckCircle2 className="w-5 h-5" /></div>
                   <span className="text-[10px] font-black text-slate-400 uppercase">Đã nghiệm thu</span>
                </div>
                <p className="text-xl font-black text-slate-800">{summary.by_status?.verified || 0}</p>
                <div className="mt-2 h-1.5 w-full bg-slate-100 rounded-full overflow-hidden">
                   <div className="h-full bg-emerald-500" style={{width: `${(summary.by_status?.verified || 0) / (summary.total_incidents || 1) * 100}%`}} />
                </div>
             </div>
          </div>

          {/* ── DETAILED ANALYSIS ─────────────────────────────── */}
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
             {/* Left: Grade Breakdown */}
             <div className="bg-white border border-slate-200/60 rounded-[2rem] p-8 shadow-sm">
                <h3 className="text-sm font-black text-slate-800 uppercase tracking-widest mb-6 flex items-center gap-2">
                   <PieChart className="w-4 h-4 text-emerald-600" />
                   Phân hạng TCVN 8866
                </h3>
                <div className="space-y-5">
                   {(['A','B','C','D','E'] as const).map(g => {
                      const count = summary.by_grade?.[g] || 0;
                      const pct = Math.round((count / (summary.total_incidents || 1)) * 100);
                      const color = ({'A':'bg-emerald-500','B':'bg-blue-500','C':'bg-yellow-500','D':'bg-orange-500','E':'bg-red-500'} as any)[g];
                      return (
                        <div key={g} className="space-y-1.5">
                           <div className="flex justify-between text-[10px] font-bold">
                              <span className="text-slate-700 flex items-center gap-2">
                                 <div className={`w-2 h-2 rounded-full ${color}`} />
                                 Hạng {g} - {TCVN_GRADES[g]?.label || 'Chưa đánh giá'}
                              </span>
                              <span className="text-slate-500">{count} vụ ({pct}%)</span>
                           </div>
                           <div className="h-2 w-full bg-slate-100 rounded-full overflow-hidden">
                              <div className={`h-full ${color} transition-all duration-1000`} style={{width: `${pct}%`}} />
                           </div>
                        </div>
                      );
                   })}
                </div>
                
                <div className="mt-10 p-5 bg-slate-50 rounded-2xl border border-slate-100">
                   <p className="text-[10px] text-slate-400 font-bold uppercase mb-2">Chỉ số chất lượng đợt khảo sát (Pavement Index)</p>
                   {(() => {
                      const pqi = calculatePavementIndex(incidents);
                      const style = getPavementStyle(pqi);
                      return (
                        <div className="flex items-center gap-3">
                          <div className={`text-lg font-black ${style.textClass}`}>
                            {pqi.toFixed(1)}
                          </div>
                          <div className={`px-2 py-1 ${style.bgClass} ${style.textBadgeClass} text-[9px] font-black rounded-lg border ${style.borderClass}`}>
                            {style.label.toUpperCase()}
                          </div>
                          <p className="text-[9px] text-slate-500 italic ml-auto max-w-[120px] text-right leading-tight">Dựa trên dữ liệu và độ tin cậy AI GSD.</p>
                        </div>
                      );
                    })()}
                </div>
             </div>

             {/* Right: Detailed List Table */}
             <div className="lg:col-span-2 bg-white border border-slate-200/60 rounded-[2rem] overflow-hidden flex flex-col shadow-sm">
                <div className="p-8 border-b border-slate-100 flex items-center justify-between bg-slate-50/50">
                   <h3 className="text-sm font-black text-slate-800 uppercase tracking-widest flex items-center gap-2">
                      <Activity className="w-4 h-4 text-blue-500" />
                      Chi tiết Sự cố đợt khảo sát
                   </h3>
                   <span className="text-[10px] font-black text-slate-400">{incidents.length} kết quả</span>
                </div>

                <div className="flex-1 overflow-x-auto custom-scrollbar">
                   <table className="w-full text-left border-collapse">
                      <thead>
                         <tr className="border-b border-slate-100 bg-slate-50/30">
                            <th className="px-6 py-4 text-[9px] font-black text-slate-400 uppercase tracking-widest">Loại hư hỏng</th>
                            <th className="px-6 py-4 text-[9px] font-black text-slate-400 uppercase tracking-widest">Lý trình</th>
                            <th className="px-6 py-4 text-[9px] font-black text-slate-400 uppercase tracking-widest">TCVN</th>
                            <th className="px-6 py-4 text-[9px] font-black text-slate-400 uppercase tracking-widest">Kích thước</th>
                            <th className="px-6 py-4 text-[9px] font-black text-slate-400 uppercase tracking-widest">Nguồn đo</th>
                            <th className="px-6 py-4 text-[9px] font-black text-slate-400 uppercase tracking-widest text-right">Trạng thái</th>
                         </tr>
                      </thead>
                      <tbody className="divide-y divide-slate-100">
                         {incidents.map((inc, i) => (
                           <tr key={i} className="hover:bg-slate-50/40 transition-colors group border-b border-slate-100">
                              <td className="px-6 py-4">
                                 <div className="flex items-center gap-3">
                                    <div className="w-8 h-8 rounded-lg bg-slate-50 flex items-center justify-center border border-slate-200/60 group-hover:border-blue-500/20 transition-all">
                                       <FileText className="w-4 h-4 text-slate-400 group-hover:text-blue-500" />
                                    </div>
                                    <div>
                                       <p className="text-[11px] font-bold text-slate-800 leading-tight">{translateAIClass(inc.classification)}</p>
                                       <p className="text-[9px] text-slate-400 mt-0.5">{inc.id.slice(-6).toUpperCase()}</p>
                                    </div>
                                 </div>
                              </td>
                              <td className="px-6 py-4">
                                 <div className="flex items-center gap-1.5 text-xs font-bold text-slate-700">
                                    <MapPin className="w-3 h-3 text-rose-500" />
                                    Km {inc.route_km ?? '---'}
                                 </div>
                                 <p className="text-[9px] text-slate-400 mt-0.5">{inc.route_name || 'Chưa cập nhật'}</p>
                              </td>
                              <td className="px-6 py-4">
                                 <span className={`px-2 py-1 rounded-md text-[10px] font-black border ${
                                    inc.tcvn_grade === 'A' ? 'bg-emerald-50 text-emerald-700 border-emerald-100' :
                                    inc.tcvn_grade === 'E' ? 'bg-rose-50 text-rose-700 border-rose-100' :
                                    'bg-blue-50 text-blue-700 border-blue-100'
                                 }`}>
                                    Hạng {inc.tcvn_grade || '---'}
                                 </span>
                              </td>
                              <td className="px-6 py-4">
                                 <p className="text-[10px] font-bold text-slate-800">{inc.damage_area_m2 ?? '—'} m²</p>
                                 <p className="text-[9px] text-slate-400 mt-0.5">{inc.damage_width_mm ?? '—'} mm (Bề rộng)</p>
                              </td>
                              <td className="px-6 py-4">
                                 {inc.is_calibrated ? (
                                     <span className="px-2 py-1 bg-emerald-50 text-emerald-700 text-[9px] font-black rounded border border-emerald-100">
                                        AI GSD
                                     </span>
                                 ) : (
                                     <span className="px-2 py-1 bg-amber-50 text-amber-700 text-[9px] font-black rounded border border-amber-100">
                                        Thủ công
                                     </span>
                                 )}
                              </td>
                              <td className="px-6 py-4 text-right">
                                 <div className="flex items-center justify-end gap-2">
                                    <div className={`w-1.5 h-1.5 rounded-full ${getStatusColorClass(inc.repair_status || 'detected')}`} />
                                    <span className="text-[10px] font-black text-slate-700 uppercase tracking-tighter">
                                       {REPAIR_STATUSES.find(r => r.value === (inc.repair_status || 'detected'))?.label}
                                    </span>
                                 </div>
                                 <p className="text-[8px] text-slate-400 mt-0.5 italic">Cập nhật: {new Date(inc.created_at || inc.approved_at).toLocaleDateString('vi-VN')}</p>
                              </td>
                           </tr>
                         ))}
                      </tbody>
                   </table>
                </div>
                
                <div className="p-6 bg-slate-50/50 border-t border-slate-100 flex justify-between items-center">
                   <p className="text-[9px] text-slate-400 font-medium italic">* Báo cáo được tạo tự động bởi hệ thống Digital Twin Infrastructure.</p>
                   <Link href="/incidents-map" className="flex items-center gap-2 text-[10px] font-black text-blue-600 hover:text-blue-500 transition-all uppercase tracking-widest">
                      Xem chi tiết trên bản đồ <ArrowRight className="w-3 h-3" />
                   </Link>
                </div>
             </div>
          </div>
        </>
      )}

      {!loading && !summary && selectedSurveyId && (
        <div className="flex flex-col items-center justify-center py-20 px-4 border border-slate-200/60 rounded-[2.5rem] bg-white shadow-sm">
           <Database className="w-12 h-12 text-slate-300 mb-4" />
           <p className="text-slate-500 font-bold uppercase tracking-widest text-sm">Không tìm thấy dữ liệu tóm tắt cho đợt này</p>
           <button onClick={() => setSelectedSurveyId(selectedSurveyId)} className="mt-4 text-blue-600 text-xs font-bold hover:underline">Thử nạp lại dữ liệu</button>
        </div>
      )}
    </div>
  );
}
