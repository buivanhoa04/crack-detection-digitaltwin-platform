"""
Surveys Router — API cho Quản lý Đợt Khảo sát
"""
from fastapi import APIRouter, HTTPException, Depends, Request
from app.middleware.auth import get_current_user, require_admin
from app.models.schemas import SurveyCreate, SurveyUpdate
from app.models.surveys import (
    create_survey, get_all_surveys, get_survey,
    update_survey, delete_survey, get_survey_summary
)
from app.models.audit import add_log

router = APIRouter()


@router.get("")
async def list_surveys(current_user: dict = Depends(get_current_user)):
    """Liệt kê tất cả đợt khảo sát."""
    surveys = await get_all_surveys()
    return {"surveys": surveys}


@router.get("/{survey_id}")
async def get_survey_detail(
    survey_id: str,
    current_user: dict = Depends(get_current_user)
):
    """Chi tiết 1 đợt khảo sát (kèm danh sách tasks liên quan)."""
    s = await get_survey(survey_id)
    if not s:
        raise HTTPException(status_code=404, detail="Không tìm thấy đợt khảo sát")
    return {"survey": s}


@router.post("")
async def create_new_survey(
    req: SurveyCreate,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    """Tạo đợt khảo sát mới."""
    survey = await create_survey(req.model_dump(), created_by=current_user["email"])

    await add_log(
        user_id=current_user["id"],
        user_email=current_user["email"],
        action="create_survey",
        target=survey["name"],
        details=f"Tuyến: {survey['route_name']} | Km{survey['route_km_start']}-Km{survey['route_km_end']}",
        ip_address=request.client.host if request.client else "",
    )

    return {"message": "Tạo đợt khảo sát thành công", "survey": survey}


@router.put("/{survey_id}")
async def update_existing_survey(
    survey_id: str,
    req: SurveyUpdate,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    """Cập nhật thông tin đợt khảo sát."""
    survey = await update_survey(survey_id, req.model_dump(exclude_none=True))
    if not survey:
        raise HTTPException(status_code=404, detail="Không tìm thấy đợt khảo sát")

    await add_log(
        user_id=current_user["id"],
        user_email=current_user["email"],
        action="update_survey",
        target=survey.get("name", survey_id),
        ip_address=request.client.host if request.client else "",
    )

    return {"message": "Cập nhật thành công", "survey": survey}


@router.delete("/{survey_id}")
async def remove_survey(
    survey_id: str,
    request: Request,
    current_user: dict = Depends(require_admin),
):
    """Xoá đợt khảo sát → chuyển vào thùng rác (cùng các tasks liên quan)."""
    s = await get_survey(survey_id)
    if not s:
        raise HTTPException(status_code=404, detail="Không tìm thấy đợt khảo sát")

    # Soft delete all tasks associated with this survey first
    from app.models.trash import soft_delete
    from app.database import get_db
    db = get_db()
    if db is not None:
        cursor = db.tasks.find({"survey_id": survey_id})
        tasks_to_delete = await cursor.to_list(length=1000)
        for t in tasks_to_delete:
            t_id = t.get("task_id") or str(t.get("_id"))
            await soft_delete("tasks", t_id, id_field="task_id", deleted_by=current_user["email"])

    # Soft delete survey → trash
    trash_doc = await soft_delete("surveys", survey_id, id_field="id", deleted_by=current_user["email"])
    if not trash_doc:
        raise HTTPException(status_code=500, detail="Không thể chuyển vào thùng rác")

    await add_log(
        user_id=current_user["id"],
        user_email=current_user["email"],
        action="soft_delete_survey",
        target=s.get("name", survey_id),
        details="Chuyển vào thùng rác (có thể khôi phục)",
        ip_address=request.client.host if request.client else "",
    )

    return {"message": "Đã chuyển đợt khảo sát vào thùng rác", "trash": trash_doc}


@router.get("/{survey_id}/summary")
async def survey_summary(
    survey_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Thống kê tổng hợp: số sự cố, phân loại, mức độ TCVN."""
    summary = await get_survey_summary(survey_id)
    if not summary:
        raise HTTPException(status_code=404, detail="Không tìm thấy đợt khảo sát")
    return summary
