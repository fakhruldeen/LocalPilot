"""Application service for privacy-preserving local API usage accounting and operational metrics."""

from __future__ import annotations
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from localpilot.domain.api_contract import ApiRequestRecord, TokenUsageSource
from localpilot.storage.db import DatabaseManager, get_db

logger = logging.getLogger(__name__)


class ApiAccountingService:
    """Records and aggregates request-level operational metrics without capturing content."""

    def __init__(self, db: Optional[DatabaseManager] = None) -> None:
        self.db = db or get_db()

    def record_request(
        self,
        key_id: Optional[str],
        model_alias: str,
        resolved_version_id: str,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        latency_ms: float = 0.0,
        first_token_latency_ms: Optional[float] = None,
        status_code: int = 200,
        error_code: Optional[str] = None,
        app_name: Optional[str] = None,
        request_id: Optional[str] = None,
    ) -> str:
        """Stores a privacy-first accounting record. Never persists prompts or completions."""
        req_id = request_id or f"req-{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        total_tokens = prompt_tokens + completion_tokens

        conn = self.db.get_connection()
        with conn:
            conn.execute(
                """
                INSERT INTO api_requests (
                    request_id, timestamp, key_id, app_name, model_alias, resolved_version_id,
                    prompt_tokens, completion_tokens, total_tokens, latency_ms,
                    first_token_latency_ms, status_code, error_code
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    req_id,
                    now,
                    key_id,
                    app_name,
                    model_alias,
                    resolved_version_id,
                    prompt_tokens,
                    completion_tokens,
                    total_tokens,
                    latency_ms,
                    first_token_latency_ms,
                    status_code,
                    error_code,
                ),
            )
        return req_id

    def list_recent_requests(
        self,
        limit: int = 50,
        key_id: Optional[str] = None,
        model_alias: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Returns recent requests ordered by latest first."""
        conn = self.db.get_connection()
        query = "SELECT * FROM api_requests WHERE 1=1"
        params: List[Any] = []

        if key_id:
            query += " AND key_id = ?"
            params.append(key_id)
        if model_alias:
            query += " AND model_alias = ?"
            params.append(model_alias)

        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        rows = conn.execute(query, tuple(params)).fetchall()
        col_names = [col[0] for col in conn.execute("SELECT * FROM api_requests LIMIT 0").description]
        return [dict(zip(col_names, row)) for row in rows]

    def get_usage_summary(
        self,
        key_id: Optional[str] = None,
        model_alias: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Calculates total requests, token volume, average latency, and error rate."""
        conn = self.db.get_connection()
        query = """
            SELECT
                COUNT(*) as total_requests,
                SUM(CASE WHEN status_code = 200 THEN 1 ELSE 0 END) as successful_requests,
                SUM(CASE WHEN status_code != 200 THEN 1 ELSE 0 END) as error_requests,
                SUM(prompt_tokens) as total_prompt_tokens,
                SUM(completion_tokens) as total_completion_tokens,
                SUM(total_tokens) as total_tokens,
                AVG(latency_ms) as avg_latency_ms,
                AVG(first_token_latency_ms) as avg_first_token_latency_ms
            FROM api_requests
            WHERE 1=1
        """
        params: List[Any] = []
        if key_id:
            query += " AND key_id = ?"
            params.append(key_id)
        if model_alias:
            query += " AND model_alias = ?"
            params.append(model_alias)

        row = conn.execute(query, tuple(params)).fetchone()
        if not row or row[0] == 0:
            return {
                "total_requests": 0,
                "successful_requests": 0,
                "error_requests": 0,
                "total_prompt_tokens": 0,
                "total_completion_tokens": 0,
                "total_tokens": 0,
                "avg_latency_ms": 0.0,
                "avg_first_token_latency_ms": 0.0,
                "tokens_per_second": 0.0,
            }

        total_reqs = row[0]
        succ_reqs = row[1] or 0
        err_reqs = row[2] or 0
        prompt_toks = row[3] or 0
        comp_toks = row[4] or 0
        tot_toks = row[5] or 0
        avg_lat = round(float(row[6] or 0.0), 2)
        avg_ft = round(float(row[7] or 0.0), 2) if row[7] is not None else 0.0

        tps = 0.0
        if avg_lat > 0 and comp_toks > 0:
            total_active_sec = (avg_lat * total_reqs) / 1000.0
            if total_active_sec > 0:
                tps = round(comp_toks / total_active_sec, 2)

        return {
            "total_requests": total_reqs,
            "successful_requests": succ_reqs,
            "error_requests": err_reqs,
            "total_prompt_tokens": prompt_toks,
            "total_completion_tokens": comp_toks,
            "total_tokens": tot_toks,
            "avg_latency_ms": avg_lat,
            "avg_first_token_latency_ms": avg_ft,
            "tokens_per_second": tps,
        }

    def get_per_model_breakdown(self) -> List[Dict[str, Any]]:
        """Summarizes request volume and token consumption per model alias."""
        conn = self.db.get_connection()
        query = """
            SELECT
                model_alias,
                COUNT(*) as request_count,
                SUM(prompt_tokens) as prompt_tokens,
                SUM(completion_tokens) as completion_tokens,
                SUM(total_tokens) as total_tokens,
                AVG(latency_ms) as avg_latency_ms
            FROM api_requests
            GROUP BY model_alias
            ORDER BY request_count DESC
        """
        rows = conn.execute(query).fetchall()
        result: List[Dict[str, Any]] = []
        for r in rows:
            result.append(
                {
                    "model_alias": r[0],
                    "request_count": r[1],
                    "prompt_tokens": r[2] or 0,
                    "completion_tokens": r[3] or 0,
                    "total_tokens": r[4] or 0,
                    "avg_latency_ms": round(float(r[5] or 0.0), 2),
                }
            )
        return result

    def get_per_key_breakdown(self) -> List[Dict[str, Any]]:
        """Summarizes request volume and token consumption per API key."""
        conn = self.db.get_connection()
        query = """
            SELECT
                r.key_id,
                COALESCE(k.name, 'unauthenticated') as key_name,
                COALESCE(r.app_name, 'direct') as app_name,
                COUNT(*) as request_count,
                SUM(r.prompt_tokens) as prompt_tokens,
                SUM(r.completion_tokens) as completion_tokens,
                SUM(r.total_tokens) as total_tokens,
                AVG(r.latency_ms) as avg_latency_ms
            FROM api_requests r
            LEFT JOIN api_keys k ON r.key_id = k.key_id
            GROUP BY r.key_id, k.name, r.app_name
            ORDER BY request_count DESC
        """
        rows = conn.execute(query).fetchall()
        result: List[Dict[str, Any]] = []
        for r in rows:
            result.append(
                {
                    "key_id": r[0] or "none",
                    "key_name": r[1],
                    "app_name": r[2],
                    "request_count": r[3],
                    "prompt_tokens": r[4] or 0,
                    "completion_tokens": r[5] or 0,
                    "total_tokens": r[6] or 0,
                    "avg_latency_ms": round(float(r[7] or 0.0), 2),
                }
            )
        return result
