"""Activity history queries and formatting for user-facing timelines."""

from __future__ import annotations
import json
from typing import Any, Dict, List, Optional
from localpilot.observability.schema import ActivityRecord
from localpilot.storage.db import DatabaseManager, get_db


class ActivityService:
    """Provides filtered access to friendly activity history."""

    def __init__(self, db: Optional[DatabaseManager] = None) -> None:
        self.db = db or get_db()

    def get_activities(
        self,
        limit: int = 50,
        category: Optional[str] = None,
        operation_id: Optional[str] = None,
    ) -> List[ActivityRecord]:
        conn = self.db.get_connection()
        query = "SELECT activity_id, timestamp, category, title, summary, details_json, operation_id, outcome FROM activity_log WHERE 1=1"
        params: List[Any] = []
        if category:
            query += " AND category = ?"
            params.append(category)
        if operation_id:
            query += " AND operation_id = ?"
            params.append(operation_id)
        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        cursor = conn.execute(query, params)
        records: List[ActivityRecord] = []
        for row in cursor.fetchall():
            records.append(
                ActivityRecord(
                    activity_id=row["activity_id"],
                    timestamp=row["timestamp"],
                    category=row["category"],
                    title=row["title"],
                    summary=row["summary"],
                    details=json.loads(row["details_json"]),
                    operation_id=row["operation_id"],
                    outcome=row["outcome"],
                )
            )
        return records
