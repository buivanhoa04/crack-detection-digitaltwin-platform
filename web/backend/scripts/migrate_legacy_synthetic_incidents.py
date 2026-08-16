"""Quarantine legacy incidents that contain synthetic spatial metadata.

Dry-run is the default. Apply only after a verified mongodump:

    python scripts/migrate_legacy_synthetic_incidents.py
    python scripts/migrate_legacy_synthetic_incidents.py --apply --backup-confirmed
"""

from __future__ import annotations

import argparse
import os
from datetime import datetime, timezone

from pymongo import MongoClient, UpdateOne


SYNTHETIC_QUERY = {
    "created_by": "system",
    "survey_id": "survey_ql1a",
    "route_name": "QL1A (Hồ Chí Minh)",
    "notes": {"$regex": "Task", "$options": "i"},
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--backup-confirmed", action="store_true")
    args = parser.parse_args()

    if args.apply and not args.backup_confirmed:
        parser.error("--apply requires --backup-confirmed")

    mongo_url = os.environ.get("MONGODB_URL")
    database_name = os.environ.get("DATABASE_NAME", "digital_twin")
    if not mongo_url:
        raise SystemExit("MONGODB_URL is required")

    client = MongoClient(mongo_url, serverSelectionTimeoutMS=5000)
    db = client[database_name]
    client.admin.command("ping")

    candidates = list(db.incidents.find(SYNTHETIC_QUERY))
    print(f"mode={'APPLY' if args.apply else 'DRY-RUN'} candidates={len(candidates)}")
    if not args.apply or not candidates:
        return 0

    survey_ids = set(db.surveys.distinct("id"))
    operations = []
    now = datetime.now(timezone.utc)
    for incident in candidates:
        task = db.tasks.find_one({"task_id": incident.get("task_id")}) or {}
        task_survey_id = task.get("survey_id")
        verified_survey_id = task_survey_id if task_survey_id in survey_ids else None

        legacy_snapshot = {
            "lat": incident.get("lat"),
            "lng": incident.get("lng"),
            "address": incident.get("address"),
            "route_name": incident.get("route_name"),
            "route_km": incident.get("route_km"),
            "lane_position": incident.get("lane_position"),
            "survey_id": incident.get("survey_id"),
            "approved_by": incident.get("approved_by"),
            "approved_at": incident.get("approved_at"),
        }
        operations.append(
            UpdateOne(
                {"_id": incident["_id"]},
                {
                    "$set": {
                        "lat": task.get("lat"),
                        "lng": task.get("lng"),
                        "address": task.get("address", ""),
                        "route_name": task.get("route_name", ""),
                        "route_km": task.get("route_km"),
                        "lane_position": task.get("lane_position", ""),
                        "survey_id": verified_survey_id,
                        "approved_by": None,
                        "approved_at": None,
                        "status": "pending_review",
                        "tcvn_grade": "",
                        "legacy_data_snapshot": legacy_snapshot,
                        "updated_at": now,
                    },
                    "$addToSet": {
                        "data_quality_flags": {
                            "$each": [
                                "legacy_synthetic_location",
                                "requires_manual_review",
                            ]
                        }
                    },
                },
            )
        )

    result = db.incidents.bulk_write(operations, ordered=False)
    print(f"matched={result.matched_count} modified={result.modified_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
