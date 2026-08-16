from fastapi import APIRouter, Depends, HTTPException, Request
from typing import List, Optional
import shutil
import os
import time
import json
from datetime import datetime
from app.middleware.auth import get_current_user, require_admin
from app.database import get_db
from app.config import settings
import re
from bson import ObjectId

def fix_object_ids(doc):
    if isinstance(doc, dict):
        res = {}
        for k, v in doc.items():
            if isinstance(v, ObjectId):
                res[k] = str(v)
            elif isinstance(v, (dict, list)):
                res[k] = fix_object_ids(v)
            else:
                res[k] = v
        return res
    elif isinstance(doc, list):
        return [fix_object_ids(item) for item in doc]
    elif isinstance(doc, ObjectId):
        return str(doc)
    return doc

router = APIRouter()

# Sử dụng thư mục lưu trữ cục bộ
UPLOAD_DIR = settings.LOCAL_SOURCES_DIR
from app.models.incidents import create_incident, build_review_incident_id

from pathlib import Path

# Operational bridge catalog: these training classes are intentionally not
# part of the inspection product and must never be rendered in archive/map.
HIDDEN_BRIDGE_CLASSES = {"Control Point", "Pothole Asphalt", "Biological_Growth"}


def hide_non_operational_detections(task: dict, infrastructure_category: str) -> dict:
    """Filter legacy persisted detections as a read-time safety net."""
    if str(infrastructure_category).lower() != "bridge":
        return task
    frames = task.get("best_frames")
    if not isinstance(frames, list):
        return task
    for frame in frames:
        if isinstance(frame, dict) and isinstance(frame.get("detections"), list):
            frame["detections"] = [
                d for d in frame["detections"]
                if d.get("class") not in HIDDEN_BRIDGE_CLASSES
                and d.get("raw_class_name") not in HIDDEN_BRIDGE_CLASSES
            ]
    if "result_count" in task:
        task["result_count"] = sum(
            len(frame.get("detections", []))
            for frame in frames
            if isinstance(frame, dict)
        )
    return task

@router.get("/tree")
async def get_archive_tree(current_user: dict = Depends(get_current_user)):
    """
    Returns the year/month/day folder structure derived from MongoDB.
    This eliminates the need to scan local physical directories and accurately reflects server-side state.
    """
    db = get_db()
    if db is None:
        return {}

    try:
        # Lấy tất cả các task để hiển thị trong kho lưu trữ
        cursor = db.tasks.find({}).sort("created_at", -1)
        tasks = await cursor.to_list(length=2000)
        
        CAT_LABELS = {
            "road": "Mặt đường",
            "bridge": "Công trình cầu"
        }
        
        tree = {}
        for t in tasks:
            dt = t.get("created_at")
            if not dt: continue
                
            year = str(dt.year)
            month = f"{dt.month:02d}"
            day = f"{dt.day:02d}"
            
            # v63.0: Group by Category
            raw_cat = t.get("infrastructure_category") or t.get("model_type") or "road"
            category = "bridge" if any(k in str(raw_cat).lower() for k in ["bridge", "concrete", "pier"]) else "road"
            cat_label = CAT_LABELS.get(category, "Mặt đường")

            task_id = t.get("task_id", "")
            filename = t.get("filename", "Video")
            
            # Label format: Filename (#ShortID)
            short_id = task_id.replace("task_", "").upper()
            display_label = f"{filename} (#{short_id})"
            
            if year not in tree: tree[year] = {}
            if month not in tree[year]: tree[year][month] = {}
            if day not in tree[year][month]: tree[year][month][day] = {}
            if cat_label not in tree[year][month][day]: tree[year][month][day][cat_label] = []
            
            # Avoid duplicates
            if not any(item['id'] == task_id for item in tree[year][month][day][cat_label]):
                tree[year][month][day][cat_label].append({
                    "id": task_id,
                    "label": display_label
                })

        # Sort the tree: years descending, months descending, days descending
        # v67.1: Corrected structure to ensure Categories appear as folders in the UI tree
        sorted_tree = {}
        for y in sorted(tree.keys(), reverse=True):
            sorted_tree[y] = {}
            for m in sorted(tree[y].keys(), reverse=True):
                sorted_tree[y][m] = {}
                for d in sorted(tree[y][m].keys(), reverse=True):
                    day_folders = []
                    # v67.2: Use distinct IDs for category nodes that won't be mistaken for Tasks by the UI
                    for cat_label, task_list in tree[y][m][d].items():
                        day_folders.append({
                            "id": f"ui_cat_{cat_label}_{y}_{m}_{d}", # UI-safe ID
                            "label": cat_label,
                            "type": "folder",
                            "children": task_list
                        })
                    sorted_tree[y][m][d] = day_folders
                    
        return sorted_tree

    except Exception as e:
        print(f"[Archive Error] Tree generation from DB failed: {e}")
        raise HTTPException(status_code=500, detail=f"Lỗi lấy dữ liệu cây thư mục: {str(e)}")

# --- SHARED UTILITY FOR AI DATA DISCOVERY (v3.3 - Robust Windows Paths) ---
def discover_best_frames(task_id, iso_folder_path, infrastructure_category="road", confidence_fallback=0):
    """
    Physically scans a task folder and returns a mapped list of frame objects with AI detections.
    """
    target_dir = Path(iso_folder_path)
    if not target_dir.exists() or not target_dir.is_dir():
        return []
        
    try:
        files = list(target_dir.iterdir())
        
        def natural_sort_key(p):
            s = p.name
            return [int(text) if text.isdigit() else text.lower() for text in re.split('([0-9]+)', s)]
            
        # v64.3: Deep Search for Assets
        # Check both the parent folder and any task-specific subfolders (handles nested AI results)
        potential_img_paths = list(target_dir.glob("*.[jJ][pP][gG]")) + \
                              list(target_dir.glob("*.[jJ][pP][eE][gG]")) + \
                              list(target_dir.glob("*.[pP][nN][gG]"))
        
        # If no images in root, check task subfolder
        if not potential_img_paths:
            task_sub = target_dir / task_id
            if task_sub.exists() and task_sub.is_dir():
                potential_img_paths = list(task_sub.glob("*.[jJ][pP][gG]")) + \
                                      list(task_sub.glob("*.[pP][nN][gG]"))
        
        img_paths = sorted(potential_img_paths, key=natural_sort_key)
        
        # 2. Extract AI Data from JSON (v64.4: Deep Search for JSON too)
        json_candidates = list(target_dir.glob("*.json"))
        if not json_candidates:
            task_sub = target_dir / task_id
            if task_sub.exists() and task_sub.is_dir():
                json_candidates = list(task_sub.glob("*.json"))
                
        json_data = {}
        if json_candidates:
            best_json = next((p for p in json_candidates if "track" in p.name.lower()), json_candidates[0])
            try:
                with open(best_json, "r") as fj:
                    json_data = json.load(fj)
            except Exception:
                pass

        # 3. Build Frame Objects
        best_frames = []
        base_path = Path(UPLOAD_DIR)
        
        for p in img_paths:
            # v4.0: Use pathlib's relative_to for extreme robustness on Windows
            try:
                rel_p = str(p.relative_to(base_path)).replace("\\", "/")
            except ValueError:
                # Fallback if somehow p is not under base_path
                full_p_str = str(p.absolute()).replace("\\", "/")
                marker = 'sources/'
                idx = full_p_str.lower().find(marker)
                rel_p = full_p_str[idx + len(marker):] if idx != -1 else p.name
            
            fid_match = re.search(r'(\d+)', p.name.lower())
            id_to_find = str(fid_match.group(1)) if fid_match else None
            
            detections = []
            if json_data and id_to_find:
                if "track" in p.name.lower():
                    frames_map = json_data.get("frames", {})
                    for f_idx, objs in frames_map.items():
                        for obj in objs:
                            if str(obj.get("track_id")) == id_to_find:
                                conf = obj.get("confidence")
                                if conf is None:
                                    conf = obj.get("conf")
                                bbox = obj.get("bbox") or obj.get("box") or []
                                polygon = obj.get("polygon")
                                if bbox and len(bbox) >= 4:
                                    detections.append({
                                        "class": str(obj.get("class") or "unknown"),
                                        "class_id": obj.get("class_id"),
                                        "raw_class_id": obj.get("raw_class_id", obj.get("class_id")),
                                        "raw_class_name": obj.get(
                                            "raw_class_name",
                                            obj.get("class") or "unknown"
                                        ),
                                        "class_mapping_applied": bool(
                                            obj.get("class_mapping_applied", False)
                                        ),
                                        "confidence": float(conf),
                                        "bbox": [float(v) for v in bbox],
                                        "polygon": polygon
                                    })
                        if detections: break
                
                if not detections:
                    frames_map = json_data.get("frames", {})
                    frame_data = frames_map.get(id_to_find, [])
                    for obj in frame_data:
                        # v4.6: Robust key detection to support various AI models (Road, Bridge, etc.)
                        conf = obj.get("confidence")
                        if conf is None:
                            conf = obj.get("conf")
                        if conf is None:
                            conf = obj.get("score")
                        bbox = obj.get("bbox") or obj.get("box") or obj.get("rectangle") or []
                        polygon = obj.get("polygon")
                        cls_name = obj.get("class") or obj.get("label") or infrastructure_category
                        
                        if bbox and len(bbox) >= 4:
                            detections.append({
                                "class": str(cls_name), # Fixed: Use the robustly detected cls_name
                                "class_id": obj.get("class_id"),
                                "raw_class_id": obj.get("raw_class_id", obj.get("class_id")),
                                "raw_class_name": obj.get("raw_class_name", cls_name),
                                "class_mapping_applied": bool(
                                    obj.get("class_mapping_applied", False)
                                ),
                                "confidence": float(conf),
                                "bbox": [float(v) for v in bbox],
                                "polygon": polygon
                            })
            
            if not detections:
                detections = [{
                    "class": infrastructure_category,
                    "confidence": float(confidence_fallback),
                    "bbox": None
                }]

            # v72.7: Perform on-the-fly normalization for Archive snapshots
            if detections:
                # We need width/height of the original image to normalize
                try:
                    import cv2
                    img = cv2.imread(str(p))
                    if img is not None:
                        h, w = img.shape[:2]
                        for det in detections:
                            bbox = det.get("bbox")
                            if bbox and len(bbox) == 4:
                                # Check if already pixel-based
                                if any(v > 1.05 for v in bbox):
                                    det["bbox"] = [
                                        bbox[0] / w,
                                        bbox[1] / h,
                                        bbox[2] / w,
                                        bbox[3] / h
                                    ]
                            polygon = det.get("polygon")
                            if polygon and isinstance(polygon, list):
                                valid_points = [
                                    point for point in polygon
                                    if isinstance(point, (list, tuple)) and len(point) >= 2
                                ]
                                if valid_points and any(
                                    float(point[0]) > 1.05 or float(point[1]) > 1.05
                                    for point in valid_points
                                ):
                                    det["polygon"] = [
                                        [float(point[0]) / w, float(point[1]) / h]
                                        for point in valid_points
                                    ]
                except Exception:
                    pass

            best_frames.append({
                "id": p.name,
                "frameFilePath": rel_p,
                "url": rel_p,
                "status": "pending",
                "detections": detections
            })
            
        return best_frames
    except Exception as e:
        print(f"Discovery error in {iso_folder_path}: {e}")
        return []

@router.get("/snapshots")
async def list_snapshots_by_date(
    year: str, 
    month: str, 
    day: str,
    asset_type: Optional[str] = None,
    current_user: dict = Depends(get_current_user)
):
    """
    List all detection tasks and their snapshots for a specific date.
    """
    db = get_db()
    if db is None:
        return {"tasks": [], "warning": "Cơ sở dữ liệu hoặc kết nối MongoDB đang gặp sự cố"}

    # Find tasks created on this specific date
    start_date = datetime(int(year), int(month), int(day), 0, 0, 0)
    end_date = datetime(int(year), int(month), int(day), 23, 59, 59)
    
    query = {
        # Batch uploads create one parent task plus one child task per file.
        # Children are implementation details and must not be rendered as
        # separate archive folders/rows (otherwise a 9-image batch appears
        # nine times and has no aggregated polygon data).
        "parent_task_id": {"$exists": False},
        "created_at": {"$gte": start_date, "$lte": end_date},
        "$or": [
            {"status": {"$in": ["done", "done (auto-detected)"]}},
            {"processingStatus": "xử lý xong"}
        ]
    }
    if asset_type:
        query = {
            "$and": [
                {
                    "parent_task_id": {"$exists": False},
                    "created_at": {"$gte": start_date, "$lte": end_date},
                    "$or": [
                        {"status": {"$in": ["done", "done (auto-detected)"]}},
                        {"processingStatus": "xử lý xong"}
                    ]
                },
                {"$or": [{"infrastructure_category": asset_type}, {"model_type": asset_type}]}
            ]
        }
        
    pipeline = [
        {"$match": query},
        {
            "$lookup": {
                "from": "surveys",
                "localField": "survey_id",
                "foreignField": "id",
                "as": "survey_info"
            }
        },
        {"$unwind": {"path": "$survey_info", "preserveNullAndEmptyArrays": True}},
        {"$sort": {"created_at": -1}}
    ]
    
    cursor = db.tasks.aggregate(pipeline)
    tasks = await cursor.to_list(length=100)
    
    valid_tasks = []
    for t in tasks:
        # On-the-fly status correction
        if t.get("status") != "done" and t.get("processingStatus") == "xử lý xong":
            t["status"] = "done"
            await db.tasks.update_one({"_id": t["_id"]}, {"$set": {"status": "done"}})
            
        # Pull survey info to root level for UI grouping
        if "survey_info" in t and t["survey_info"]:
            # Preserve the canonical survey link for approval metadata.  The
            # lookup only adds display fields otherwise, so the review modal
            # could not preselect the survey for legacy task records.
            t["survey_id"] = t.get("survey_id") or t["survey_info"].get("id")
            t["route_name"] = t["survey_info"].get("route_name", "")
            t["survey_name"] = t["survey_info"].get("name", "")
            t["iteration"] = t["survey_info"].get("iteration", 1)
            del t["survey_info"]
        else:
            t["route_name"] = "Khác"
            t["survey_name"] = ""
            t["iteration"] = 1

        t["_id"] = str(t["_id"])
        # Normalize task_id to avoid null
        t["task_id"] = t.get("task_id") or t["_id"]
        
        infra_cat = t.get("infrastructure_category") or t.get("model_type") or "road"
        t["infrastructure_category"] = infra_cat 
        
        # SMART DISCOVERY: auto-find best_frames only if missing or lacking real detections
        local_p_raw = t.get("local_path")
        if local_p_raw:
            p_obj = Path(local_p_raw)
            iso_folder = p_obj if p_obj.is_dir() else p_obj.parent
            if iso_folder.exists():
                current_bf = t.get("best_frames", [])
                has_real_dets = any(
                    isinstance(f, dict) and any(d.get("class") not in ["road", "bridge", "unknown"] for d in f.get("detections", []))
                    for f in current_bf
                )
                if not current_bf or not has_real_dets:
                    discovered = discover_best_frames(t.get("task_id", ""), str(iso_folder), infra_cat, t.get("confidence", 0))
                    disc_has_real = any(
                        isinstance(f, dict) and any(d.get("class") not in ["road", "bridge", "unknown"] for d in f.get("detections", []))
                        for f in discovered
                    )
                    if disc_has_real or not current_bf:
                        t["best_frames"] = discovered
                        try:
                            await db.tasks.update_one({"_id": t["_id"]}, {"$set": {"best_frames": discovered}})
                        except Exception:
                            pass

        t["trackingDataUrl"] = f"/api/crack/tracking/{t.get('task_id')}"
        if "created_at" in t and isinstance(t["created_at"], datetime):
            t["created_at"] = t["created_at"].isoformat() + "Z"
            
        valid_tasks.append(fix_object_ids(hide_non_operational_detections(t, infra_cat)))
        
    return {"tasks": valid_tasks}


@router.post("/snapshot/action")
async def update_snapshot_status(
    payload: dict,
    current_user: dict = Depends(get_current_user)
):
    """
    Updates the status of an INDIVIDUAL frame and persists state to DB.
    Also triggers incident creation for the map if status is 'approved'.
    """
    task_id = payload.get("task_id")
    frame_idx = payload.get("frame_index")
    batch_result_idx = payload.get("batch_result_index")
    new_status = payload.get("status")
    metadata = payload.get("metadata", {})

    if (
        task_id is None
        or (frame_idx is None and batch_result_idx is None)
        or new_status is None
    ):
        raise HTTPException(status_code=400, detail="Thiếu thông tin task_id, frame_index hoặc status")

    db = get_db()
    if db is None:
         raise HTTPException(status_code=503, detail="Database unavailable")

    # 1. Fetch the task and ensure best_frames array is persisted in DB
    from bson import ObjectId
    query_conditions = [{"_id": task_id}, {"task_id": task_id}]
    if ObjectId.is_valid(task_id):
        query_conditions.append({"_id": ObjectId(task_id)})
    query = {"$or": query_conditions}
    
    task = await db.tasks.find_one(query)
    if not task:
        # Retry with alt format
        alt_id = f"task_{task_id}" if not task_id.startswith("task_") else task_id.replace("task_", "")
        alt_conditions = [{"_id": alt_id}, {"task_id": alt_id}]
        if ObjectId.is_valid(alt_id):
            alt_conditions.append({"_id": ObjectId(alt_id)})
        task = await db.tasks.find_one({"$or": alt_conditions})
        if not task:
            raise HTTPException(status_code=404, detail="Không tìm thấy bản ghi task")
        task_id = alt_id # Sync ID for update
        query = {"$or": alt_conditions}

    # A large-folder frame lives in batch_results, not in the 200-item parent
    # preview. Resolve it by its stable global result_index.
    batch_query = None
    frame_obj = None
    if batch_result_idx is not None:
        try:
            batch_result_idx = int(batch_result_idx)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="batch_result_index không hợp lệ")
        batch_query = {
            "task_id": task_id,
            "result_index": batch_result_idx,
        }
        batch_doc = await db.batch_results.find_one(batch_query)
        if not batch_doc:
            raise HTTPException(status_code=404, detail="Không tìm thấy ảnh kết quả phân trang")
        frame_obj = batch_doc.get("frame", {})

    # Persist legacy non-batch frames if they are not already present.
    if batch_query is None and not task.get("best_frames"):
        local_p_raw = task.get("local_path")
        if local_p_raw:
            p_obj = Path(local_p_raw)
            # Use pathlib to find the correct folder robustly
            iso_folder = p_obj if p_obj.is_dir() else p_obj.parent
            
            # Use the helper to get EVERYTHING including detections/BBoxes
            m_type = task.get("model_type", "").lower()
            infra_cat = "bridge" if "bridge" in m_type or "concrete" in m_type else "road"
            persisted_frames = discover_best_frames(task_id, str(iso_folder), infra_cat, task.get("confidence", 0))
            
            if persisted_frames:
                await db.tasks.update_one(query, {"$set": {"best_frames": persisted_frames}})
                task["best_frames"] = persisted_frames

    # 2. Update the actual storage location.
    if batch_query is not None:
        update_data = {"frame.status": new_status}
        if metadata:
            update_data["frame.metadata"] = metadata
        await db.batch_results.update_one(batch_query, {"$set": update_data})
    else:
        update_data = {f"best_frames.{frame_idx}.status": new_status}
        if metadata:
            update_data[f"best_frames.{frame_idx}.metadata"] = metadata
        await db.tasks.update_one(query, {"$set": update_data})

    review_index = (
        batch_result_idx if batch_result_idx is not None else int(frame_idx)
    )

        # 3. MAPPING: Create an incident if approved
    if new_status == "approved":
        # Get frame info for image
        best_frames = task.get("best_frames", [])
        image_url = ""
        frame_confidence = 0
        classification = ""
        detections = []

        if frame_obj is None and frame_idx is not None and 0 <= int(frame_idx) < len(best_frames):
            frame_obj = best_frames[frame_idx]
        if frame_obj:
            image_url = frame_obj.get("frameFilePath", "")
            
            # v72.3: Extract specific confidence, class and DETECTIONS (BBoxes) from this frame
            detections = frame_obj.get("detections", [])
            if detections and len(detections) > 0:
                frame_confidence = detections[0].get("confidence", 0)
                classification = detections[0].get("class", "")
        
        # Fallback to task level if frame level failed
        if frame_confidence == 0:
            frame_confidence = task.get("confidence", 0)

        def safe_float(val, default=0.0):
            if val is None:
                return default
            try:
                return float(val)
            except (ValueError, TypeError):
                return default

        incident_data = {
            "title": metadata.get("title", f"Sự cố phê duyệt từ AI - {task_id}"),
            "description": metadata.get("description", "Duyệt nhanh từ Archive snapshot."),
            "severity": metadata.get("severity", "warning"),
            "lat": metadata.get("lat"),
            "lng": metadata.get("lng"),
            "address": metadata.get("address", ""),
            "images": [image_url] if image_url else [],
            "asset_type": metadata.get("asset_type", task.get("infrastructure_category", "road")),
            "confidence": frame_confidence,
            "classification": classification,
            "detections": detections, # v72.3 Fixed: Store BBoxes in the incident for Map view
            "approved_by": current_user.get("email") or current_user.get("id"),
            "approved_at": datetime.utcnow().isoformat(),
            "detected_at": task.get("created_at").isoformat() if isinstance(task.get("created_at"), datetime) else task.get("created_at"),
            # Extended business fields
            "route_name": metadata.get("route_name", ""),
            "route_km": safe_float(metadata.get("route_km"), None),
            "lane_position": metadata.get("lane_position", ""),
            "tcvn_grade": metadata.get("tcvn_grade", ""),
            "tcvn_grade_auto": metadata.get("tcvn_grade_auto", ""),
            "survey_id": metadata.get("survey_id"),
            "repair_status": metadata.get("repair_status", "detected"),
            "damage_area_m2": safe_float(metadata.get("damage_area_m2"), None),
            "damage_width_mm": safe_float(metadata.get("damage_width_mm"), None),
            "repair_method": metadata.get("repair_method", ""),
            "gsd_mm_per_pixel": safe_float(metadata.get("gsd_mm_per_pixel"), None),
            "calibration_source": metadata.get("calibration_source", ""),
            "is_calibrated": bool(metadata.get("is_calibrated", False)),
            "source_task_id": task_id,
            "source_frame_index": review_index,
            "source_batch_result_index": batch_result_idx,
        }
        await create_incident(incident_data, created_by=current_user["email"])
    elif new_status == "rejected":
        best_frames = task.get("best_frames", [])
        image_url = ""
        if frame_obj is None and frame_idx is not None and 0 <= int(frame_idx) < len(best_frames):
            frame_obj = best_frames[frame_idx]
        if frame_obj:
            image_url = frame_obj.get("frameFilePath", "")

        rejection_filters = [
            {"id": build_review_incident_id(task_id, review_index)},
            {"source_task_id": task_id, "source_frame_index": review_index},
        ]
        # Compatibility cleanup for records approved before source traceability
        # was introduced. Exact image matching keeps the scope to this frame.
        if image_url:
            rejection_filters.append({
                "images": image_url,
                "approved_by": {"$nin": [None, ""]},
            })

        await db.incidents.update_many(
            {"$or": rejection_filters},
            {"$set": {
                "status": "rejected",
                "approved_by": None,
                "approved_at": None,
                "rejected_by": current_user.get("email") or current_user.get("id"),
                "rejected_at": datetime.utcnow().isoformat(),
                "updated_at": datetime.utcnow().isoformat(),
            }},
        )

    return {"message": "Đã cập nhập trạng thái và ánh xạ Bản đồ thành công", "status": new_status}

@router.delete("/tasks/{task_id}")
async def delete_task_archive(
    task_id: str,
    current_user: dict = Depends(require_admin)
):
    """
    Soft delete a task, moving it to the trash bin.
    """
    from app.models.trash import soft_delete
    db = get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Cơ sở dữ liệu không khả dụng")

    # Soft delete the task using standard id field
    trash_doc = await soft_delete("tasks", task_id, id_field="task_id", deleted_by=current_user.get("email", "admin@digitaltwin.vn"))
    if not trash_doc:
        raise HTTPException(status_code=404, detail="Không tìm thấy bản ghi task")

    task_id_to_delete = trash_doc.get("item_id")
    return {"message": f"Dữ liệu task {task_id_to_delete} đã được chuyển vào thùng rác."}

@router.get("/all-snapshots")
async def list_all_pending_snapshots(
    status: str = "pending",
    limit: int = 100,
    current_user: dict = Depends(get_current_user)
):
    """
    Returns a flat list of snapshots filtered by approval status.
    No more folder-diving required.
    """
    db = get_db()
    if db is None:
        return {"tasks": []}
        
    query = {
        "parent_task_id": {"$exists": False},
        "approval_status": status, 
        "$or": [
            {"status": {"$in": ["done", "done (auto-detected)"]}},
            {"processingStatus": "xử lý xong"}
        ]
    }
    
    pipeline = [
        {"$match": query},
        {
            "$lookup": {
                "from": "surveys",
                "localField": "survey_id",
                "foreignField": "id",
                "as": "survey_info"
            }
        },
        {"$unwind": {"path": "$survey_info", "preserveNullAndEmptyArrays": True}},
        {"$sort": {"created_at": -1}},
        {"$limit": limit}
    ]
    
    cursor = db.tasks.aggregate(pipeline)
    tasks = await cursor.to_list(length=limit)
    
    valid_tasks = []
    for t in tasks:
        t["_id"] = str(t["_id"])
        
        # On-the-fly status correction
        if t.get("status") != "done" and t.get("processingStatus") == "xử lý xong":
            t["status"] = "done"
            await db.tasks.update_one({"_id": t["_id"]}, {"$set": {"status": "done"}})
            
        # Pull survey info to root level for UI grouping
        if "survey_info" in t and t["survey_info"]:
            t["route_name"] = t["survey_info"].get("route_name", "")
            t["survey_name"] = t["survey_info"].get("name", "")
            t["iteration"] = t["survey_info"].get("iteration", 1)
            del t["survey_info"]
        else:
            t["route_name"] = "Khác"
            t["survey_name"] = ""
            t["iteration"] = 1

        # Normalize task_id to avoid null
        t["task_id"] = t.get("task_id") or t["_id"]
        
        # v4.6: Robust category retrieval (handles both field names)
        infra_cat = t.get("infrastructure_category") or t.get("model_type") or "road"
        t["infrastructure_category"] = infra_cat # Normalize for UI
        
        # SMART DISCOVERY: auto-find best_frames if missing or if disk has more images (including 0-defect clean frames)
        local_p_raw = t.get("local_path")
        if local_p_raw:
            p_obj = Path(local_p_raw)
            iso_folder = p_obj if p_obj.is_dir() else p_obj.parent
            if iso_folder.exists():
                discovered = discover_best_frames(t.get("task_id", ""), str(iso_folder), infra_cat, t.get("confidence", 0))
                current_bf = t.get("best_frames", [])
                if len(discovered) > len(current_bf) or not current_bf:
                    t["best_frames"] = discovered
                    try:
                        await db.tasks.update_one({"_id": t["_id"]}, {"$set": {"best_frames": discovered}})
                    except Exception:
                        pass

        # v72.5: Include trackingDataUrl for video playback in archive
        t["trackingDataUrl"] = f"/api/crack/tracking/{t.get('task_id')}"

        if "created_at" in t and isinstance(t["created_at"], datetime):
            t["created_at"] = t["created_at"].isoformat() + "Z"
            
        valid_tasks.append(hide_non_operational_detections(t, infra_cat))
            
    return {"tasks": valid_tasks}

@router.post("/approve-detailed/{task_id}")
async def approve_task_detailed(
    task_id: str,
    details: dict, # title, description, severity, lat, lng, address, asset_type
    current_user: dict = Depends(require_admin)
):
    """
    1. Update task approval status
    2. Create an official incident record for the map
    """
    db = get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Cơ sở dữ liệu không khả dụng")

    # Fetch the original task to get the image info
    from bson import ObjectId
    query_conditions = [{"_id": task_id}, {"task_id": task_id}]
    if ObjectId.is_valid(task_id):
        query_conditions.append({"_id": ObjectId(task_id)})
    query = {"$or": query_conditions}

    task = await db.tasks.find_one(query)
    if not task:
        raise HTTPException(status_code=404, detail="Không tìm thấy bản ghi task")

    # 1. Update task status
    await db.tasks.update_one(
        query,
        {"$set": {
            "approval_status": "approved",
            "approved_by": current_user["email"],
            "updated_at": datetime.utcnow()
        }}
    )

    # 2. Create incident for the map
    # Include the first valid IMAGE frame as the primary image for the incident
    image_url = ""
    valid_images = [
        f.get("frameFilePath", "") 
        for f in task.get("best_frames", []) 
        if f.get("frameFilePath", "").lower().endswith(('.jpg', '.jpeg', '.png'))
    ]
    
    if valid_images:
        image_url = valid_images[0]

    def safe_float(val, default=0.0):
        if val is None:
            return default
        try:
            return float(val)
        except (ValueError, TypeError):
            return default

    incident_data = {
        "title": details.get("title", f"Sự cố {task['filename']}"),
        "description": details.get("description", ""),
        "severity": details.get("severity", "warning"),
        "lat": details.get("lat"),
        "lng": details.get("lng"),
        "address": details.get("address", ""),
        "images": [image_url] if image_url else [],
        "asset_type": details.get("asset_type", task.get("model_type", "road")),
        "confidence": task.get("confidence", 0),
        "detected_at": task.get("created_at").isoformat() if isinstance(task.get("created_at"), datetime) else None,
        # Extended business fields
        "route_name": details.get("route_name", ""),
        "route_km": safe_float(details.get("route_km"), None),
        "lane_position": details.get("lane_position", ""),
        "tcvn_grade": details.get("tcvn_grade", ""),
        "tcvn_grade_auto": details.get("tcvn_grade_auto", ""),
        "survey_id": details.get("survey_id"),
        "repair_status": details.get("repair_status", "detected"),
        "damage_area_m2": safe_float(details.get("damage_area_m2"), None),
        "damage_width_mm": safe_float(details.get("damage_width_mm"), None),
        "repair_method": details.get("repair_method", ""),
        "gsd_mm_per_pixel": safe_float(details.get("gsd_mm_per_pixel"), None),
        "calibration_source": details.get("calibration_source", ""),
        "is_calibrated": bool(details.get("is_calibrated", False)),
        "source_task_id": task_id,
        "source_frame_index": None,
    }
    
    new_inc = await create_incident(incident_data, created_by=current_user["email"])
    
    return {
        "message": "Đã phê duyệt và đẩy dữ liệu sang Bản đồ thành công",
        "incident_id": new_inc["id"]
    }

@router.patch("/approve/{task_id}")
async def update_approval_status(
    task_id: str,
    status: str, # approved, rejected, pending
    current_user: dict = Depends(require_admin)
):
    """
    Simplified approval for quick rejection or resets.
    """
    if status not in ["approved", "rejected", "pending"]:
        raise HTTPException(status_code=400, detail="Trạng thái không hợp lệ")
        
    db = get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Cơ sở dữ liệu không khả dụng")

    from bson import ObjectId
    query_conditions = [{"_id": task_id}, {"task_id": task_id}]
    if ObjectId.is_valid(task_id):
        query_conditions.append({"_id": ObjectId(task_id)})
    query = {"$or": query_conditions}

    result = await db.tasks.update_one(
        query,
        {"$set": {"approval_status": status, "approved_by": current_user["email"], "updated_at": datetime.utcnow()}}
    )
    
    if result.modified_count == 0:
        raise HTTPException(status_code=404, detail="Không tìm thấy bản ghi")

    if status == "rejected":
        await db.incidents.update_many(
            {
                "$or": [
                    {"id": build_review_incident_id(task_id, None)},
                    {"source_task_id": task_id, "source_frame_index": None},
                ]
            },
            {"$set": {
                "status": "rejected",
                "approved_by": None,
                "approved_at": None,
                "rejected_by": current_user["email"],
                "rejected_at": datetime.utcnow().isoformat(),
                "updated_at": datetime.utcnow().isoformat(),
            }},
        )
        
    return {"message": f"Đã chuyển trạng thái sang: {status}"}
