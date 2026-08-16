"""
Maintenance Service - Decision Matrix and AI Technical Report Generation.
"""
import httpx
from datetime import datetime
from typing import Dict, List, Optional
from app.services.deterioration import get_pci_predictions, get_condition_label
from app.models.config_store import load_config

def get_maintenance_recommendation(pci: float, total_crack_area: float = 0.0) -> Dict:
    """
    Decision matrix based on Vietnamese road maintenance standards (TCVN).
    """
    if pci >= 85:
        return {
            "category": "Bảo trì thường xuyên",
            "action": "Vệ sinh mặt đường, khơi thông rãnh thoát nước, trám vá vết nứt chân chim rất nhỏ.",
            "priority": "Thấp (Low)",
            "estimated_cost_per_m2": 50000,
            "code": "RM-01"
        }
    elif pci >= 70:
        return {
            "category": "Bảo dưỡng phòng ngừa",
            "action": "Trám vá vết nứt dọc/ngang đơn lẻ bằng nhựa bitum nóng (Crack sealing), xử lý ổ gà nhỏ.",
            "priority": "Trung bình (Medium)",
            "estimated_cost_per_m2": 150000,
            "code": "PM-02"
        }
    elif pci >= 55:
        return {
            "category": "Sửa chữa định kỳ / Sửa chữa vừa",
            "action": "Vá sửa mặt đường hư hỏng diện rộng cục bộ, láng nhựa chống thấm hoặc rải lớp microsurfacing bảo vệ mặt đường.",
            "priority": "Cao (High)",
            "estimated_cost_per_m2": 350000,
            "code": "MR-03"
        }
    else:
        return {
            "category": "Sửa chữa lớn / Khôi phục cải tạo",
            "action": "Cào bóc tái sinh nguội mặt đường tại chỗ (Cold In-place Recycling) hoặc Cào bóc mặt đường cũ và thảm lại lớp bê tông nhựa nóng polymer mới (Milling & Overlay).",
            "priority": "Rất cao (Critical)",
            "estimated_cost_per_m2": 750000,
            "code": "MA-04"
        }

async def generate_segment_report(segment: dict, incidents: List[dict]) -> str:
    """
    Generates a technical report for the segment.
    Attempts to call LLM (RAGFlow) first, falls back to a template on error.
    """
    pci = segment.get("pci_current")
    if pci is None:
        raise ValueError("Phân đoạn chưa có PCI đo thực tế")
    struct_type = segment.get("structural_type") or "Chưa cập nhật"
    lane = segment.get("lane") or "Chưa cập nhật"
    name = segment.get("name") or "Chưa cập nhật"
    segment_id = segment.get("segment_id", "")

    measured_areas = [
        float(inc["damage_area_m2"])
        for inc in incidents
        if isinstance(inc.get("damage_area_m2"), (int, float))
    ]
    total_crack_area = sum(measured_areas)
    incident_lines = "\n".join(
        f"- {inc.get('classification') or 'Chưa phân loại'} | "
        f"TCVN: {inc.get('tcvn_grade') or 'Chưa đánh giá'} | "
        f"Diện tích: {inc.get('damage_area_m2') if inc.get('damage_area_m2') is not None else 'Chưa đo'} m²"
        for inc in incidents
    ) or "- Chưa có sự cố đã duyệt thuộc phân đoạn."
    return f"""# BÁO CÁO KỸ THUẬT PHÂN ĐOẠN
**Mã phân đoạn:** {segment_id}
**Phân đoạn:** {name}
**Kết cấu:** {struct_type}
**Làn đường:** {lane}
**PCI đã đo:** {float(pci):.1f}/100
**Số sự cố đã duyệt:** {len(incidents)}
**Diện tích hư hỏng đã đo:** {total_crack_area:.2f} m² trên {len(measured_areas)} sự cố có số đo

## Danh sách dữ liệu nguồn
{incident_lines}

> Báo cáo chỉ tổng hợp dữ liệu đã lưu và đã duyệt; không tự sinh tọa độ, kích thước, đơn giá hay khuyến nghị giả.
"""

    # Legacy AI/prediction implementation below is intentionally unreachable
    # until every external assumption can be sourced and audited.
    predictions = get_pci_predictions(pci, struct_type, total_crack_area)
    rec = get_maintenance_recommendation(pci, total_crack_area)
    
    # 1. Build a rich technical prompt
    prompt = f"""
Hãy đóng vai trò là một kỹ sư kiểm định đường bộ cao cấp thuộc Cục Đường bộ Việt Nam. 
Viết một báo cáo kỹ thuật đánh giá tình trạng mặt đường chuyên nghiệp cho phân đoạn sau:
- Mã phân đoạn: {segment_id}
- Tên phân đoạn: {name}
- Loại kết cấu: {struct_type}
- Làn đường: {lane}
- Chỉ số PCI hiện tại: {pci:.1f}/100 ({get_condition_label(pci)})
- Tổng diện tích hư hỏng phát hiện: {total_crack_area:.2f} m²
- Số lượng sự cố được phê duyệt: {len(incidents)} vụ

Dự báo PCI trong tương lai:
- 6 tháng: {predictions[0]['predicted_pci']:.1f} ({predictions[0]['condition']})
- 12 tháng: {predictions[1]['predicted_pci']:.1f} ({predictions[1]['condition']})
- 24 tháng: {predictions[2]['predicted_pci']:.1f} ({predictions[2]['condition']})

Đề xuất giải pháp bảo trì từ Decision Matrix:
- Phân loại bảo trì: {rec['category']}
- Biện pháp đề xuất: {rec['action']}
- Mức độ ưu tiên: {rec['priority']}
- Đơn giá ước lượng: {rec['estimated_cost_per_m2']:,} VNĐ/m²

Hãy viết báo cáo bằng Tiếng Việt chuẩn kỹ thuật giao thông đường bộ, có bố cục rõ ràng bao gồm:
1. ĐÁNH GIÁ CHUNG
2. PHÂN TÍCH SUY GIẢM CHẤT LƯỢNG (Dự báo xu hướng)
3. ĐỀ XUẤT PHƯƠNG ÁN THI CÔNG & DỰ TOÁN KINH PHÍ SƠ BỘ.
Báo cáo cần trang trọng, súc tích và có tính thuyết phục cao cho hội đồng phê duyệt vốn.
"""

    # 2. Try calling RAGFlow if configured
    from app.config import settings
    config = load_config()
    rag_url = config.get("ragflow_api_url", settings.RAGFLOW_API_URL).rstrip('/')
    rag_token = config.get("ragflow_api_token", settings.RAGFLOW_API_TOKEN)
    
    if rag_url and rag_token:
        try:
            headers = {"X-API-Token": rag_token, "Content-Type": "application/json"}
            payload = {
                "question": prompt,
                "session_id": f"report_{segment_id}",
                "stream": False
            }
            async with httpx.AsyncClient(timeout=30.0) as client:
                res = await client.post(f"{rag_url}/chat", headers=headers, json=payload)
                if res.status_code == 200:
                    data = res.json()
                    answer = data.get("data", {}).get("answer") or data.get("answer")
                    if answer:
                        return answer
        except Exception as e:
            print(f"[REPORT LLM WARNING] LLM generation failed, falling back to template: {e}")
            
    # 3. Fallback template (Beautiful Markdown)
    fallback_report = f"""# BÁO CÁO KỸ THUẬT ĐÁNH GIÁ CHẤT LƯỢNG MẶT ĐƯỜNG
**Mã phân đoạn:** {segment_id}  
**Lý trình:** {name} | **Làn đường:** {lane}  
**Kết cấu:** {struct_type}  
**Thời gian lập báo cáo:** {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}  

---

## 1. ĐÁNH GIÁ TRẠNG THÁI HIỆN TẠI
- **Chỉ số PCI hiện thời:** **{pci:.1f} / 100.0** (Tình trạng: **{get_condition_label(pci).upper()}**)
- **Tổng diện tích hư hại bề mặt:** **{total_crack_area:.2f} m²**
- **Tổng số lượng điểm khuyết tật:** **{len(incidents)} điểm nứt đường** đã được phê duyệt qua hệ thống AI Digital Twin.
- *Nhận xét sơ bộ:* Bề mặt phân đoạn đang có dấu hiệu xuống cấp {("nhẹ" if pci >= 85 else "vừa phải" if pci >= 70 else "nghiêm trọng" if pci >= 55 else "rất nghiêm trọng")}. Các khuyết tật tập trung chủ yếu vào nứt mặt đường và cần được can thiệp sớm để tránh hiện tượng thấm ẩm gây phá hỏng nền đường sâu hơn.

## 2. PHÂN TÍCH VÀ DỰ BÁO SUY GIẢM (Model AASHTO)
Dựa trên mô hình toán học dự báo suy giảm chất lượng mặt đường theo thời gian, chất lượng phân đoạn này dự kiến sẽ biến động như sau nếu không thực hiện bảo dưỡng sửa chữa:
- **Dự báo sau 6 tháng:** PCI giảm xuống còn **{predictions[0]['predicted_pci']:.1f}** ({predictions[0]['condition']})
- **Dự báo sau 12 tháng:** PCI giảm xuống còn **{predictions[1]['predicted_pci']:.1f}** ({predictions[1]['condition']})
- **Dự báo sau 24 tháng:** PCI giảm xuống còn **{predictions[2]['predicted_pci']:.1f}** ({predictions[2]['condition']})

*Đặc biệt lưu ý:* Tốc độ suy thoái sẽ tăng nhanh sau năm đầu tiên do ảnh hưởng tích lũy nước mưa và tải trọng xe qua các vết nứt hiện có.

## 3. ĐỀ XUẤT PHƯƠNG ÁN XỬ LÝ & DỰ TOÁN KINH PHÍ
Áp dụng Ma trận Quyết định (Decision Matrix) theo quy trình quản lý đường bộ quốc gia:
- **Phân loại cấp độ bảo trì:** **{rec['category']}**
- **Biện pháp thi công đề xuất:** {rec['action']}
- **Mức độ ưu tiên thực hiện:** **{rec['priority']}**
- **Dự toán kinh phí sơ bộ:**
  - Diện tích cần can thiệp: **{max(1.0, total_crack_area):.2f} m²** (Dựa trên tổng diện tích nứt thực tế)
  - Đơn giá bảo dưỡng ước tính: **{rec['estimated_cost_per_m2']:,} VNĐ/m²**
  - **Tổng chi phí ước lượng:** **{(max(1.0, total_crack_area) * rec['estimated_cost_per_m2']):,.0f} VNĐ** (Chưa bao gồm thuế VAT và phí điều hành thiết bị công trường)

Báo cáo này được tự động xuất bởi module Prescriptive Twin của Hệ thống PMS.
"""
    return fallback_report
