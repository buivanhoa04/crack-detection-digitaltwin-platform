"""
Incidents router for map-based damage tracking.
"""
from typing import Optional
from datetime import datetime

from fastapi import APIRouter, HTTPException, Depends, Request, Query

from app.middleware.auth import get_current_user, require_admin
from app.models.schemas import IncidentCreate, IncidentUpdate
from app.models.incidents import (
    get_all_incidents, get_incident,
    create_incident, update_incident,
)
from app.models.trash import soft_delete
from app.models.audit import add_log

router = APIRouter()


@router.get("")
async def list_incidents(
    survey_id: Optional[str] = Query(default=None),
    approved_only: bool = Query(default=False),
    current_user: dict = Depends(get_current_user),
):
    """Get all incidents for map display."""
    incs = await get_all_incidents(
        survey_id=survey_id,
        approved_only=approved_only,
    )
    return {"incidents": incs}


@router.get("/{incident_id}")
async def get_incident_detail(
    incident_id: str,
    current_user: dict = Depends(get_current_user),
):
    inc = await get_incident(incident_id)
    if not inc:
        raise HTTPException(status_code=404, detail="Không tìm thấy sự cố")
    return {"incident": inc}


@router.post("")
async def create_new_incident(
    req: IncidentCreate,
    request: Request,
    current_user: dict = Depends(require_admin),
):
    incident_data = req.model_dump()
    incident_data["approved_by"] = current_user["email"]
    incident_data["approved_at"] = datetime.utcnow().isoformat()
    inc = await create_incident(incident_data, created_by=current_user["email"])

    await add_log(
        user_id=current_user["id"],
        user_email=current_user["email"],
        action="create_incident",
        target=inc["title"],
        details=f"Severity: {inc['severity']}",
        ip_address=request.client.host if request.client else "",
    )

    return {"message": "Tạo sự cố thành công", "incident": inc}


@router.put("/{incident_id}")
async def update_existing_incident(
    incident_id: str,
    req: IncidentUpdate,
    request: Request,
    current_user: dict = Depends(require_admin),
):
    inc = await update_incident(incident_id, req.model_dump(exclude_none=True))
    if not inc:
        raise HTTPException(status_code=404, detail="Không tìm thấy sự cố")

    await add_log(
        user_id=current_user["id"],
        user_email=current_user["email"],
        action="update_incident",
        target=inc["title"],
        ip_address=request.client.host if request.client else "",
    )

    return {"message": "Cập nhật thành công", "incident": inc}


@router.delete("/{incident_id}")
async def remove_incident(
    incident_id: str,
    request: Request,
    current_user: dict = Depends(require_admin),
):
    incident = await get_incident(incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail="Không tìm thấy sự cố")

    trash_doc = await soft_delete(
        "incidents",
        incident_id,
        id_field="id",
        deleted_by=current_user["email"],
    )
    if not trash_doc:
        raise HTTPException(status_code=500, detail="Không thể chuyển sự cố vào thùng rác")

    if incident.get("segment_id"):
        from app.models.segments import recalculate_segment_pci
        await recalculate_segment_pci(
            incident["segment_id"],
            incident.get("survey_id"),
        )

    await add_log(
        user_id=current_user["id"],
        user_email=current_user["email"],
        action="soft_delete_incident",
        target=incident_id,
        details="Chuyển sự cố vào thùng rác",
        ip_address=request.client.host if request.client else "",
    )

    return {
        "message": "Đã chuyển sự cố vào thùng rác",
        "deleted_count": 1,
        "incident_id": incident_id,
        "trash": trash_doc,
    }
