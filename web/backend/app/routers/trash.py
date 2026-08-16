"""
Trash Router — API Thùng rác hệ thống (Soft Delete & Restore)
"""
from typing import Optional
from fastapi import APIRouter, HTTPException, Depends, Request
from pydantic import BaseModel
from app.middleware.auth import get_current_user
from app.database import get_db
from app.models.trash import (
    get_all_trash, restore, permanent_delete, empty_trash
)
from app.models.audit import add_log

router = APIRouter()


@router.get("")
async def list_trash(
    item_type: Optional[str] = None,
    current_user: dict = Depends(get_current_user),
):
    """
    Liệt kê tất cả items trong thùng rác.
    Query params: ?item_type=incidents | surveys | digital-twin
    """
    is_admin = current_user.get("role") == "admin"
    user_email = None if is_admin else current_user.get("email")
    items = await get_all_trash(item_type, user_email=user_email)
    return {"trash": items, "total": len(items)}


@router.post("/restore/{item_id}")
async def restore_item(
    item_id: str,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    """Khôi phục item từ thùng rác về collection gốc."""
    db = get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Cơ sở dữ liệu không khả dụng")

    trash_doc = await db.trash.find_one({"item_id": item_id})
    if not trash_doc:
        raise HTTPException(status_code=404, detail="Không tìm thấy item trong thùng rác")

    is_admin = current_user.get("role") == "admin"
    if not is_admin and trash_doc.get("deleted_by") != current_user.get("email"):
        raise HTTPException(status_code=403, detail="Bạn không có quyền khôi phục item này")

    result = await restore(item_id)
    if not result:
        raise HTTPException(status_code=404, detail="Không tìm thấy item trong thùng rác")

    await add_log(
        user_id=current_user["id"],
        user_email=current_user["email"],
        action="restore_from_trash",
        target=item_id,
        details=f"Restored to: {result['collection']}",
        ip_address=request.client.host if request.client else "",
    )

    return {"message": "Đã khôi phục thành công", **result}


@router.delete("/permanent/{item_id}")
async def permanent_delete_item(
    item_id: str,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    """Xóa vĩnh viễn item khỏi thùng rác — KHÔNG THỂ KHÔI PHỤC."""
    db = get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Cơ sở dữ liệu không khả dụng")

    trash_doc = await db.trash.find_one({"item_id": item_id})
    if not trash_doc:
        raise HTTPException(status_code=404, detail="Không tìm thấy item trong thùng rác")

    is_admin = current_user.get("role") == "admin"
    if not is_admin and trash_doc.get("deleted_by") != current_user.get("email"):
        raise HTTPException(status_code=403, detail="Bạn không có quyền xóa vĩnh viễn item này")

    success = await permanent_delete(item_id)
    if not success:
        raise HTTPException(status_code=404, detail="Không tìm thấy item trong thùng rác")

    await add_log(
        user_id=current_user["id"],
        user_email=current_user["email"],
        action="permanent_delete",
        target=item_id,
        ip_address=request.client.host if request.client else "",
    )

    return {"message": "Đã xóa vĩnh viễn"}


@router.delete("/empty")
async def empty_all_trash(
    item_type: Optional[str] = None,
    request: Request = None,
    current_user: dict = Depends(get_current_user),
):
    """Xóa vĩnh viễn toàn bộ thùng rác (hoặc theo type)."""
    db = get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Cơ sở dữ liệu không khả dụng")

    is_admin = current_user.get("role") == "admin"

    if is_admin:
        count = await empty_trash(item_type)
    else:
        # Non-admins can only empty their own trash
        query = {"deleted_by": current_user.get("email")}
        if item_type:
            query["item_type"] = item_type

        cursor = db.trash.find(query)
        items = await cursor.to_list(length=1000)

        count = 0
        for item in items:
            success = await permanent_delete(item["item_id"])
            if success:
                count += 1

    await add_log(
        user_id=current_user["id"],
        user_email=current_user["email"],
        action="empty_trash",
        target=item_type or "all",
        details=f"Deleted {count} items",
        ip_address=request.client.host if request and request.client else "",
    )

    return {"message": f"Đã xóa {count} items", "deleted_count": count}
