"""
Segments Router - API endpoints for routes, segments, and PCI history.
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from typing import List, Optional
from datetime import datetime
from pydantic import BaseModel

from app.middleware.auth import get_current_user
from app.database import get_db
from app.models.segments import (
    get_all_routes,
    get_route_segments,
    get_segment,
    get_segment_history,
    add_pci_history,
    map_coordinate_to_segment,
    create_route,
    create_segment
)
from app.services.deterioration import get_pci_predictions
from app.services.maintenance import generate_segment_report

router = APIRouter()

class PCIHistoryRequest(BaseModel):
    pci_score: float
    crack_area: float
    survey_id: str

class MapCoordinateRequest(BaseModel):
    lat: float
    lng: float
    route_id: Optional[str] = None
    max_distance_meters: Optional[float] = 50.0

class RouteCreateRequest(BaseModel):
    route_id: str
    name: str
    start_km: float
    end_km: float
    province: str

class SegmentCreateRequest(BaseModel):
    segment_id: str
    route_id: str
    name: str
    start_gps: dict
    end_gps: dict
    lane: str
    pci_current: float
    structural_type: str

@router.get("/routes")
async def list_routes(current_user: dict = Depends(get_current_user)):
    """List all configured routes."""
    return await get_all_routes()

@router.post("/routes")
async def add_route(payload: RouteCreateRequest, current_user: dict = Depends(get_current_user)):
    """Add a new route configuration."""
    try:
        return await create_route(
            payload.route_id,
            payload.name,
            payload.start_km,
            payload.end_km,
            payload.province
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/routes/{route_id}/segments")
async def list_route_segments(route_id: str, current_user: dict = Depends(get_current_user)):
    """List all segments under a specific route."""
    return await get_route_segments(route_id)

@router.post("/segments")
async def add_segment(payload: SegmentCreateRequest, current_user: dict = Depends(get_current_user)):
    """Add a new segment configuration."""
    try:
        return await create_segment(
            payload.segment_id,
            payload.route_id,
            payload.name,
            payload.start_gps,
            payload.end_gps,
            payload.lane,
            payload.pci_current,
            payload.structural_type
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/segments/{segment_id}")
async def get_segment_details(segment_id: str, current_user: dict = Depends(get_current_user)):
    """Get detailed status of a specific segment."""
    seg = await get_segment(segment_id)
    if not seg:
        raise HTTPException(status_code=404, detail="Phân đoạn không tồn tại")
    return seg

@router.get("/segments/{segment_id}/history")
async def get_segment_pci_history(segment_id: str, current_user: dict = Depends(get_current_user)):
    """Get quality history (PCI scores) for a segment."""
    return await get_segment_history(segment_id)

@router.post("/segments/{segment_id}/pci-history")
async def update_segment_quality(
    segment_id: str,
    payload: PCIHistoryRequest,
    current_user: dict = Depends(get_current_user)
):
    """Add a new inspection record and update segment PCI."""
    seg = await get_segment(segment_id)
    if not seg:
        raise HTTPException(status_code=404, detail="Phân đoạn không tồn tại")
        
    try:
        return await add_pci_history(
            segment_id,
            payload.survey_id,
            payload.pci_score,
            payload.crack_area
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/segments/map-coordinate")
async def map_coordinate(payload: MapCoordinateRequest, current_user: dict = Depends(get_current_user)):
    """
    Finds the closest segment to a coordinate.
    Useful for GIS-to-segment resolution.
    """
    seg = await map_coordinate_to_segment(
        payload.lat,
        payload.lng,
        payload.route_id,
        payload.max_distance_meters
    )
    if not seg:
        raise HTTPException(status_code=404, detail="Không tìm thấy phân đoạn nào đủ gần")
    return seg

@router.get("/segments/{segment_id}/predict")
async def predict_segment_pci(segment_id: str, current_user: dict = Depends(get_current_user)):
    """
    Predict future PCI scores for a segment (6, 12, 24 months).
    """
    seg = await get_segment(segment_id)
    if not seg:
        raise HTTPException(status_code=404, detail="Phân đoạn không tồn tại")
        
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database offline")
        
    # Get approved incidents to find total crack area
    cursor = db.incidents.find({
        "segment_id": segment_id,
        "approved_by": {"$nin": [None, ""]},
        "approved_at": {"$nin": [None, ""]},
    })
    incidents = await cursor.to_list(length=1000)
    total_crack_area = sum(inc.get("damage_area_m2") or 0.0 for inc in incidents)
    
    pci = seg.get("pci_current")
    if pci is None:
        raise HTTPException(status_code=422, detail="Phân đoạn chưa có PCI đo thực tế")
    struct_type = seg.get("structural_type")
    if not struct_type:
        raise HTTPException(status_code=422, detail="Phân đoạn chưa có loại kết cấu đã xác minh")
    
    predictions = get_pci_predictions(pci, struct_type, total_crack_area)
    return {"segment_id": segment_id, "predictions": predictions}

@router.get("/segments/{segment_id}/report")
async def get_segment_report_endpoint(segment_id: str, current_user: dict = Depends(get_current_user)):
    """
    Generate technical pavement inspection report (LLM / Template fallback).
    """
    seg = await get_segment(segment_id)
    if not seg:
        raise HTTPException(status_code=404, detail="Phân đoạn không tồn tại")
        
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database offline")
        
    cursor = db.incidents.find({
        "segment_id": segment_id,
        "approved_by": {"$nin": [None, ""]},
        "approved_at": {"$nin": [None, ""]},
    })
    incidents = await cursor.to_list(length=1000)
    
    try:
        report_text = await generate_segment_report(seg, incidents)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {"segment_id": segment_id, "report": report_text}

@router.post("/routes/{route_id}/generate-segments")
async def generate_route_segments(
    route_id: str,
    bbox: str = Query(..., description="Format: min_lat,min_lng,max_lat,max_lng"),
    segment_length_m: float = Query(100.0, description="Segment length in meters"),
    current_user: dict = Depends(get_current_user)
):
    """
    Automatically queries OpenStreetMap for the route's centerline path,
    divides it into exact segment_length_m pieces using LRS interpolation,
    and seeds the segments in MongoDB.
    """
    raise HTTPException(
        status_code=422,
        detail="Đã tắt sinh phân đoạn tự động vì tạo Km/PCI giả. Hãy nhập hình học và số liệu khảo sát đã xác minh qua POST /segments.",
    )

    db = get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database offline")
        
    route = await db.routes.find_one({"route_id": route_id})
    if not route:
        raise HTTPException(status_code=404, detail="Tuyến đường không tồn tại trong hệ thống")
        
    try:
        min_lat, min_lng, max_lat, max_lng = map(float, bbox.split(","))
    except ValueError:
        raise HTTPException(status_code=400, detail="Bbox phải có định dạng: min_lat,min_lng,max_lat,max_lng")
        
    from app.routers.alignment import fetch_centerline_from_osm
    from app.utils.spatial import interpolate_points_along_path
    
    # Query OSM
    bbox_tuple = (min_lat, min_lng, max_lat, max_lng)
    osm_points = await fetch_centerline_from_osm(route.get("name", "QL.1"), bbox_tuple)
    
    if not osm_points:
        # Try query using route_id as fallback
        osm_points = await fetch_centerline_from_osm(route_id, bbox_tuple)
        
    if not osm_points:
        raise HTTPException(status_code=404, detail="Không tìm thấy tọa độ tim đường từ OSM trong khu vực bbox này")
        
    # Convert list of tuples (lat, lng) to list of dicts
    dict_points = [{"lat": pt[0], "lng": pt[1]} for pt in osm_points]
    
    # Filter out duplicate points
    unique_points = [dict_points[0]]
    for p in dict_points[1:]:
        if abs(p["lat"] - unique_points[-1]["lat"]) > 1e-6 or abs(p["lng"] - unique_points[-1]["lng"]) > 1e-6:
            unique_points.append(p)
            
    # Run interpolation
    pci_pts = interpolate_points_along_path(unique_points, segment_length_m)
    if len(pci_pts) < 2:
        raise HTTPException(status_code=400, detail="Độ dài tuyến đường quá ngắn để phân đoạn")
        
    # Clear old segments for this route
    await db.segments.delete_many({"route_id": route_id})
    
    created_count = 0
    segments_to_create = len(pci_pts) - 1
    for i in range(segments_to_create):
        segment_id = f"seg_{route_id}_km0_{i}00"
        name = f"Km0+{i}00 - Km0+{(i+1)}00"
        start_gps = pci_pts[i]
        end_gps = pci_pts[i+1]
        
        await create_segment(
            segment_id=segment_id,
            route_id=route_id,
            name=name,
            start_gps=start_gps,
            end_gps=end_gps,
            lane="Làn hỗn hợp",
            pci_current=100.0,
            structural_type="Bê tông nhựa nóng"
        )
        created_count += 1
        
    return {
        "status": "success",
        "route_id": route_id,
        "raw_points_count": len(osm_points),
        "interpolated_points_count": len(pci_pts),
        "segments_created": created_count
    }
