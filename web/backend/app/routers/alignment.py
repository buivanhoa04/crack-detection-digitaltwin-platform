"""
Alignment Router — API cho Bình đồ duỗi phẳng & Xuất bản vẽ CAD
"""
import math
from datetime import datetime
from typing import List, Optional
import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import StreamingResponse

from app.database import get_db
from app.middleware.auth import get_current_user

router = APIRouter()

# ── HÌNH HỌC TÍNH TOÁN (GPS PROJECTION) ──────────────────

def latlng_to_meters(lat: float, lng: float, lat_ref: float) -> tuple[float, float]:
    """Chuyển đổi tọa độ GPS sang mét phẳng (Mercator nội bộ)."""
    r_earth = 6371000.0
    lat_rad = math.radians(lat)
    lng_rad = math.radians(lng)
    lat_ref_rad = math.radians(lat_ref)
    
    x = r_earth * lng_rad * math.cos(lat_ref_rad)
    y = r_earth * lat_rad
    return x, y

def distance_point_to_segment(px: float, py: float, ax: float, ay: float, bx: float, by: float) -> tuple[float, float, float, float]:
    """Chiếu điểm P lên đoạn thẳng AB. Trả về: (signed_distance, proj_x, proj_y, ratio_t)."""
    abx = bx - ax
    aby = by - ay
    apx = px - ax
    apy = py - ay
    
    ab_len_sq = abx**2 + aby**2
    if ab_len_sq == 0:
        return 0.0, ax, ay, 0.0
        
    t = (apx * abx + apy * aby) / ab_len_sq
    t = max(0.0, min(1.0, t)) # Kẹp trong đoạn thẳng AB
    
    proj_x = ax + t * abx
    proj_y = ay + t * aby
    
    # Khoảng cách vuông góc
    dist = math.sqrt((px - proj_x)**2 + (py - proj_y)**2)
    
    # Tích có hướng để tính chiều lệch (Trái âm / Phải dương)
    cross_product = abx * apy - aby * apx
    if cross_product < 0:
        dist = -dist
        
    return dist, proj_x, proj_y, t

def project_incident_on_centerline(lat: float, lng: float, centerline: List[tuple[float, float]]) -> tuple[float, float]:
    """Chiếu sự cố lên toàn bộ tim đường dạng gấp khúc. Trả về (distance_along_centerline_m, offset_m)."""
    if len(centerline) < 2:
        return 0.0, 0.0
        
    # Lấy lat trung bình để làm quy chiếu phẳng
    lat_ref = sum(pt[0] for pt in centerline) / len(centerline)
    
    # Chuyển đổi toàn bộ tim đường sang mét
    pts_m = [latlng_to_meters(pt[0], pt[1], lat_ref) for pt in centerline]
    px, py = latlng_to_meters(lat, lng, lat_ref)
    
    min_dist = float('inf')
    best_offset = 0.0
    best_distance = 0.0
    
    # Tính lũy kế độ dài tim đường
    cumulative_length = 0.0
    
    for i in range(len(pts_m) - 1):
        ax, ay = pts_m[i]
        bx, by = pts_m[i+1]
        
        # Độ dài đoạn này
        seg_len = math.sqrt((bx - ax)**2 + (by - ay)**2)
        
        offset, proj_x, proj_y, t = distance_point_to_segment(px, py, ax, ay, bx, by)
        
        abs_offset = abs(offset)
        if abs_offset < min_dist:
            min_dist = abs_offset
            best_offset = offset
            # Khoảng cách từ đầu tuyến đến hình chiếu trên đoạn này
            best_distance = cumulative_length + (t * seg_len)
            
        cumulative_length += seg_len
        
    return best_distance, best_offset

# ── OSM TIM ĐƯỜNG GETTER & FALLBACKS ────────────────────

async def fetch_centerline_from_osm(route_name: str, bbox: tuple[float, float, float, float]) -> List[tuple[float, float]]:
    """Truy vấn Overpass API lấy tọa độ tim đường thực tế theo ref."""
    min_lat, min_lng, max_lat, max_lng = bbox
    overpass_url = "https://overpass-api.de/api/interpreter"
    
    # Query ways có ref khớp với route_name
    query = f"""
    [out:json][timeout:15];
    way[ref="{route_name}"]({min_lat},{min_lng},{max_lat},{max_lng});
    out geom;
    """
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(overpass_url, data={"data": query})
            
        if response.status_code != 200:
            return []
            
        data = response.json()
        elements = data.get("elements", [])
        if not elements:
            return []
            
        # Thu thập các điểm và nối lại
        points = []
        for elem in elements:
            geom = elem.get("geometry", [])
            for pt in geom:
                points.append((pt["lat"], pt["lon"]))
                
        # Lọc bỏ trùng lặp liên tiếp và sắp xếp đơn giản từ Bắc xuống Nam hoặc Tây sang Đông
        if not points:
            return []
            
        # Sắp xếp thô các đoạn đường dựa trên hướng chính của đợt khảo sát
        # Để đơn giản, lọc tọa độ trùng lặp cục bộ
        unique_pts = [points[0]]
        for pt in points[1:]:
            if pt != unique_pts[-1]:
                unique_pts.append(pt)
                
        return unique_pts
    except Exception:
        # Nếu mạng lỗi hoặc Overpass chậm, dùng fallback
        return []

def generate_fallback_centerline(incidents: List[dict], start_km: float, end_km: float) -> List[tuple[float, float]]:
    """Use only recorded incident coordinates; never invent a centerline."""
    if incidents:
        # Sắp xếp các sự cố theo thời gian ghi nhận (thứ tự xe chạy)
        sorted_incs = sorted(incidents, key=lambda x: x.get("detected_at", ""))
        pts = [
            (inc["lat"], inc["lng"])
            for inc in sorted_incs
            if inc.get("lat") is not None and inc.get("lng") is not None
        ]
        if len(pts) >= 2:
            return pts
            
    return []

# ── API ENDPOINTS ───────────────────────────────────────

@router.get("/survey/{survey_id}")
async def get_survey_alignment(survey_id: str, current_user: dict = Depends(get_current_user)):
    """Tính toán và chiếu toàn bộ sự cố trong đợt khảo sát lên tim đường duỗi phẳng."""
    db = get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Cơ sở dữ liệu không khả dụng")
        
    survey = await db.surveys.find_one({"id": survey_id})
    if not survey:
        raise HTTPException(status_code=404, detail="Không tìm thấy đợt khảo sát")
        
    # Lấy danh sách sự cố
    cursor = db.incidents.find({"survey_id": survey_id, "status": {"$ne": "deleted"}})
    incidents = await cursor.to_list(length=1000)
    for inc in incidents:
        inc["_id"] = str(inc["_id"])
        
    route_name = survey.get("route_name", "")
    start_km = survey.get("route_km_start")
    end_km = survey.get("route_km_end")
    
    # 1. Tính toán tim đường
    centerline = []
    if incidents:
        lats = [inc["lat"] for inc in incidents if "lat" in inc]
        lngs = [inc["lng"] for inc in incidents if "lng" in inc]
        if lats and lngs:
            bbox = (min(lats) - 0.05, min(lngs) - 0.05, max(lats) + 0.05, max(lngs) + 0.05)
            centerline = await fetch_centerline_from_osm(route_name, bbox)
            
    if not centerline:
        centerline = generate_fallback_centerline(incidents, start_km, end_km)
    if len(centerline) < 2:
        raise HTTPException(
            status_code=422,
            detail="Không đủ tọa độ khảo sát thật để dựng tim đường. Cần ít nhất hai điểm GPS hợp lệ.",
        )
        
    # 2. Chiếu sự cố lên tim đường để tính Km và Offset
    projected_incidents = []
    for inc in incidents:
        lat = inc.get("lat")
        lng = inc.get("lng")
        if lat is None or lng is None:
            continue
            
        dist_m, offset_m = project_incident_on_centerline(lat, lng, centerline)
        
        # Quy đổi khoảng cách mét dọc tuyến sang Km lý trình tương đối
        # lý trình = Km_bắt_đầu + (khoảng_cách_mét / 1000)
        calc_km = start_km + (dist_m / 1000.0)
        
        # Giới hạn trong khoảng Km đợt khảo sát nếu vượt quá
        calc_km = max(start_km, min(end_km, calc_km)) if end_km > start_km else calc_km
        
        inc_copy = dict(inc)
        inc_copy["route_km"] = round(calc_km, 3)
        inc_copy["offset_m"] = round(offset_m, 2)
        projected_incidents.append(inc_copy)
        
    return {
        "survey_id": survey_id,
        "survey_name": survey.get("name", ""),
        "route_name": route_name,
        "route_km_start": start_km,
        "route_km_end": end_km,
        "centerline": [{"lat": pt[0], "lng": pt[1]} for pt in centerline],
        "incidents": projected_incidents
    }

@router.get("/survey/{survey_id}/export/dxf")
async def export_survey_alignment_dxf(survey_id: str):
    """Xuất bản vẽ kỹ thuật AutoCAD DXF chứa bình đồ duỗi phẳng của tuyến đường khảo sát."""
    # Gọi logic tính toán trên để lấy incidents đã chiếu
    data = await get_survey_alignment(survey_id)
    incidents = data["incidents"]
    route_name = data["route_name"]
    start_km = data["route_km_start"]
    end_km = data["route_km_end"]
    
    # Khởi tạo chuỗi DXF cơ bản
    dxf_lines = [
        "  0", "SECTION",
        "  2", "HEADER",
        "  9", "$ACADVER",
        "  1", "AC1015",  # Định dạng AutoCAD 2000
        "  0", "ENDSEC",
        "  0", "SECTION",
        "  2", "TABLES",
        "  0", "TABLE",
        "  2", "LAYER",
        " 70", "3",
        "  0", "LAYER",
        "  2", "TIM_DUONG",  # Lớp Tim Đường (màu trắng)
        " 70", "0",
        " 62", "7",
        "  6", "CONTINUOUS",
        "  0", "LAYER",
        "  2", "SU_CO",      # Lớp Sự cố (màu đỏ)
        " 70", "0",
        " 62", "1",
        "  6", "CONTINUOUS",
        "  0", "LAYER",
        "  2", "CHU_THICH",   # Lớp Chú thích (màu xanh lá)
        " 70", "0",
        " 62", "3",
        "  6", "CONTINUOUS",
        "  0", "ENDTAB",
        "  0", "ENDSEC",
        "  0", "SECTION",
        "  2", "ENTITIES"
    ]
    
    # Tổng chiều dài tuyến theo mét
    total_len_m = (end_km - start_km) * 1000.0
    if total_len_m <= 0:
        total_len_m = 1000.0  # Mặc định 1km nếu Km lỗi
        
    # 1. Vẽ trục tim đường (Y = 0)
    dxf_lines.extend([
        "  0", "LINE",
        "  8", "TIM_DUONG",
        " 10", "0.0",
        " 20", "0.0",
        " 30", "0.0",
        " 11", str(total_len_m),
        " 21", "0.0",
        " 31", "0.0"
    ])
    
    # 2. Vẽ dải vạch phân làn phụ (ví dụ mép làn trái Y = -3.75m, mép làn phải Y = 3.75m)
    for offset_y in [-3.75, 3.75]:
        dxf_lines.extend([
            "  0", "LINE",
            "  8", "TIM_DUONG",
            " 10", "0.0",
            " 20", str(offset_y),
            " 30", "0.0",
            " 11", str(total_len_m),
            " 21", str(offset_y),
            " 31", "0.0"
        ])
        
    # 3. Vẽ vạch chia lý trình (ticks) mỗi 100 mét
    step_m = 100.0
    for m in range(0, int(total_len_m) + 10, int(step_m)):
        curr_km = start_km + (m / 1000.0)
        km_str = f"Km{curr_km:.1f}"
        
        # Tick dọc
        dxf_lines.extend([
            "  0", "LINE",
            "  8", "TIM_DUONG",
            " 10", str(m),
            " 20", "-6.0",
            " 30", "0.0",
            " 11", str(m),
            " 21", "6.0",
            " 31", "0.0"
        ])
        
        # Chữ chú thích Km
        dxf_lines.extend([
            "  0", "TEXT",
            "  8", "CHU_THICH",
            " 10", str(m),
            " 20", "-12.0",
            " 30", "0.0",
            " 40", "2.0",  # Chiều cao chữ
            "  1", km_str,
            " 50", "90.0"  # Xoay chữ 90 độ
        ])
        
    # 4. Vẽ ký hiệu hư hỏng (sự cố)
    for inc in incidents:
        inc_km = inc.get("route_km", 0.0)
        inc_offset = inc.get("offset_m", 0.0)
        
        # Tọa độ X tương ứng dọc theo trục duỗi phẳng
        inc_x = (inc_km - start_km) * 1000.0
        
        # Vẽ dấu X chéo thể hiện sự cố
        size = 1.5
        dxf_lines.extend([
            "  0", "LINE",
            "  8", "SU_CO",
            " 10", str(inc_x - size),
            " 20", str(inc_offset - size),
            " 30", "0.0",
            " 11", str(inc_x + size),
            " 21", str(inc_offset + size),
            " 31", "0.0",
            
            "  0", "LINE",
            "  8", "SU_CO",
            " 10", str(inc_x - size),
            " 20", str(inc_offset + size),
            " 30", "0.0",
            " 11", str(inc_x + size),
            " 21", str(inc_offset - size),
            " 31", "0.0"
        ])
        
        # Chữ chú thích sự cố
        title = inc.get("title", "Sự cố nứt")
        label_text = f"{title} (Km{inc_km:.3f}, Lệch: {inc_offset}m)"
        
        dxf_lines.extend([
            "  0", "TEXT",
            "  8", "CHU_THICH",
            " 10", str(inc_x),
            " 20", str(inc_offset + 3.0),
            " 30", "0.0",
            " 40", "1.2",
            "  1", label_text,
            " 50", "45.0"  # Xoay chữ nghiêng 45 độ cho dễ đọc
        ])
        
    # Đóng file DXF
    dxf_lines.extend([
        "  0", "ENDSEC",
        "  0", "EOF"
    ])
    
    dxf_content = "\n".join(dxf_lines)
    
    filename = f"Binh_do_QL_{route_name}_Survey_{survey_id[:8]}.dxf"
    
    return Response(
        content=dxf_content,
        media_type="application/dxf",
        headers={
            "Content-Disposition": f"attachment; filename={filename}"
        }
    )

@router.get("/survey/{survey_id}/evolution")
async def get_survey_defect_evolution(survey_id: str, compare_survey_id: Optional[str] = None):
    """So sánh tiến triển hư hỏng giữa đợt hiện tại và đợt khảo sát trước đó trên cùng tuyến đường."""
    db = get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Cơ sở dữ liệu không khả dụng")
        
    survey = await db.surveys.find_one({"id": survey_id})
    if not survey:
        raise HTTPException(status_code=404, detail="Không tìm thấy đợt khảo sát")
        
    route_name = survey.get("route_name", "")
    
    # 1. Tìm đợt khảo sát để so sánh
    if not compare_survey_id:
        # Tự động tìm đợt khảo sát liền trước trên cùng tuyến đường
        prev_survey = await db.surveys.find_one({
            "route_name": route_name,
            "created_at": {"$lt": survey.get("created_at")},
            "status": {"$ne": "deleted"}
        }, sort=[("created_at", -1)])
        
        if prev_survey:
            compare_survey_id = prev_survey["id"]
            
    # Lấy thống kê của đợt hiện tại
    from app.models.surveys import get_survey_summary
    current_summary = await get_survey_summary(survey_id)
    
    # Lấy thống kê đợt so sánh
    compare_summary = {}
    if compare_survey_id:
        compare_summary = await get_survey_summary(compare_survey_id)
        
    # 2. Tính toán chênh lệch (Delta)
    delta = {}
    if compare_summary:
        delta["incidents"] = current_summary.get("total_incidents", 0) - compare_summary.get("total_incidents", 0)
        
        # So sánh theo từng phân loại AI
        curr_class = current_summary.get("by_classification", {})
        comp_class = compare_summary.get("by_classification", {})
        all_classes = set(curr_class.keys()).union(set(comp_class.keys()))
        
        class_diff = {}
        for c in all_classes:
            class_diff[c] = curr_class.get(c, 0) - comp_class.get(c, 0)
        delta["by_classification"] = class_diff
        
        # So sánh theo độ nghiêm trọng
        curr_sev = current_summary.get("by_severity", {})
        comp_sev = compare_summary.get("by_severity", {})
        all_sevs = set(curr_sev.keys()).union(set(comp_sev.keys()))
        
        sev_diff = {}
        for s in all_sevs:
            sev_diff[s] = curr_sev.get(s, 0) - comp_sev.get(s, 0)
        delta["by_severity"] = sev_diff
    else:
        delta["incidents"] = current_summary.get("total_incidents", 0)
        delta["by_classification"] = current_summary.get("by_classification", {})
        delta["by_severity"] = current_summary.get("by_severity", {})
        
    return {
        "survey_id": survey_id,
        "survey_name": survey.get("name", ""),
        "compare_survey_id": compare_survey_id,
        "compare_survey_name": compare_summary.get("survey_name", "Không có"),
        "route_name": route_name,
        "current_stats": current_summary,
        "previous_stats": compare_summary,
        "delta": delta
    }
