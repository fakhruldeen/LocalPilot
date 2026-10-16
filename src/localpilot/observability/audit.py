"""Audit decision records query and reporting service."""

from __future__ import annotations
import json
from typing import Any, Dict, List, Optional
from localpilot.storage.db import DatabaseManager, get_db


class AuditService:
    """Manages queries into immutable decision and approval records."""

    def __init__(self, db: Optional[DatabaseManager] = None) -> None:
        self.db = db or get_db()

    def get_decisions(
        self,
        target_id: Optional[str] = None,
        decision_type: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        conn = self.db.get_connection()
        query = "SELECT decision_id, timestamp, decision_type, target_id, payload_json, outcome, reason_code FROM audit_decisions WHERE 1=1"
        params: List[Any] = []
        if target_id:
            query += " AND target_id = ?"
            params.append(target_id)
        if decision_type:
            query += " AND decision_type = ?"
            params.append(decision_type)
        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        cursor = conn.execute(query, params)
        records: List[Dict[str, Any]] = []
        for row in cursor.fetchall():
            records.append({
                "decision_id": row["decision_id"],
                "timestamp": row["timestamp"],
                "decision_type": row["decision_type"],
                "target_id": row["target_id"],
                "payload": json.loads(row["payload_json"]),
                "outcome": row["outcome"],
                "reason_code": row["reason_code"],
            })
        return records
