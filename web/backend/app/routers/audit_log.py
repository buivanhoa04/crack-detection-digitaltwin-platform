"""
Audit log router (Admin only).
"""
from fastapi import APIRouter, Depends, Query
from typing import Optional

from app.middleware.auth import get_current_user
from app.models.audit import get_logs, get_action_types

router = APIRouter()


@router.get("")
async def list_audit_logs(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    action: Optional[str] = None,
    user: Optional[str] = None,
    current_user: dict = Depends(get_current_user),
):
    return await get_logs(page=page, limit=limit, action_filter=action, user_filter=user)


@router.get("/actions")
async def list_action_types(current_user: dict = Depends(get_current_user)):
    return {"actions": await get_action_types()}
