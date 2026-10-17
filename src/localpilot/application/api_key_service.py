"""Application service for local API key generation, validation, and lifecycle management."""

from __future__ import annotations
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from localpilot.domain.api_contract import ApiKeyPolicy, ApiKeyRecord, ApiKeyStatus
from localpilot.storage.db import DatabaseManager, get_db

logger = logging.getLogger(__name__)


class ApiKeyService:
    """Manages local API keys for loopback application integrations."""

    def __init__(self, db: Optional[DatabaseManager] = None) -> None:
        self.db = db or get_db()

    def create_key(
        self,
        name: str,
        app_name: Optional[str] = None,
        policy: Optional[ApiKeyPolicy] = None,
        expires_at: Optional[str] = None,
    ) -> Tuple[str, ApiKeyRecord]:
        """Generates a high-entropy local API token and persists its nonreversible verifier.

        Returns (raw_plaintext_token, ApiKeyRecord).
        The raw plaintext token must be presented to the user immediately; it is never saved to disk.
        """
        raw_token, record = ApiKeyRecord.generate(
            name=name,
            app_name=app_name,
            policy=policy,
            expires_at=expires_at,
        )

        policy_dict = record.policy.model_dump() if record.policy else {}
        if app_name:
            policy_dict["app_name"] = app_name

        conn = self.db.get_connection()
        with conn:
            conn.execute(
                """
                INSERT INTO api_keys (
                    key_id, name, prefix, hashed_secret, created_at, expires_at, revoked_at, policy_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.key_id,
                    record.display_name,
                    record.secret_prefix,
                    record.verifier,
                    record.created_at,
                    record.expires_at,
                    record.revoked_at,
                    json.dumps(policy_dict),
                ),
            )

        return raw_token, record

    def list_keys(self) -> List[ApiKeyRecord]:
        """Returns all registered local API keys."""
        conn = self.db.get_connection()
        rows = conn.execute("SELECT * FROM api_keys ORDER BY created_at DESC").fetchall()
        col_names = [col[0] for col in conn.execute("SELECT * FROM api_keys LIMIT 0").description]
        result: List[ApiKeyRecord] = []

        for row in rows:
            d = dict(zip(col_names, row))
            policy = ApiKeyPolicy()
            app_name = None
            if d.get("policy_json"):
                try:
                    pdata = json.loads(d["policy_json"])
                    app_name = pdata.get("app_name")
                    policy = ApiKeyPolicy(**pdata)
                except Exception:
                    pass

            is_revoked = bool(d.get("revoked_at"))
            result.append(
                ApiKeyRecord(
                    key_id=d["key_id"],
                    display_name=d["name"],
                    app_name=app_name,
                    secret_prefix=d["prefix"],
                    verifier=d["hashed_secret"],
                    status=ApiKeyStatus.REVOKED if is_revoked else ApiKeyStatus.ACTIVE,
                    policy=policy,
                    created_at=d["created_at"],
                    expires_at=d.get("expires_at"),
                    revoked_at=d.get("revoked_at"),
                )
            )
        return result

    def get_key(self, key_id: str) -> Optional[ApiKeyRecord]:
        """Retrieves an API key record by ID."""
        conn = self.db.get_connection()
        row = conn.execute("SELECT * FROM api_keys WHERE key_id = ?", (key_id,)).fetchone()
        if not row:
            return None
        col_names = [col[0] for col in conn.execute("SELECT * FROM api_keys LIMIT 0").description]
        d = dict(zip(col_names, row))

        policy = ApiKeyPolicy()
        app_name = None
        if d.get("policy_json"):
            try:
                pdata = json.loads(d["policy_json"])
                app_name = pdata.get("app_name")
                policy = ApiKeyPolicy(**pdata)
            except Exception:
                pass

        is_revoked = bool(d.get("revoked_at"))
        return ApiKeyRecord(
            key_id=d["key_id"],
            display_name=d["name"],
            app_name=app_name,
            secret_prefix=d["prefix"],
            verifier=d["hashed_secret"],
            status=ApiKeyStatus.REVOKED if is_revoked else ApiKeyStatus.ACTIVE,
            policy=policy,
            created_at=d["created_at"],
            expires_at=d.get("expires_at"),
            revoked_at=d.get("revoked_at"),
        )

    def verify_token(self, token: str) -> Optional[ApiKeyRecord]:
        """Verifies candidate token in constant time against active stored keys.

        Returns authenticated ApiKeyRecord if valid, otherwise None.
        """
        if not token:
            return None

        # Fetch all non-revoked keys
        conn = self.db.get_connection()
        rows = conn.execute("SELECT * FROM api_keys WHERE revoked_at IS NULL").fetchall()
        col_names = [col[0] for col in conn.execute("SELECT * FROM api_keys LIMIT 0").description]

        for row in rows:
            d = dict(zip(col_names, row))
            policy = ApiKeyPolicy()
            app_name = None
            if d.get("policy_json"):
                try:
                    pdata = json.loads(d["policy_json"])
                    app_name = pdata.get("app_name")
                    policy = ApiKeyPolicy(**pdata)
                except Exception:
                    pass

            rec = ApiKeyRecord(
                key_id=d["key_id"],
                display_name=d["name"],
                app_name=app_name,
                secret_prefix=d["prefix"],
                verifier=d["hashed_secret"],
                status=ApiKeyStatus.ACTIVE,
                policy=policy,
                created_at=d["created_at"],
                expires_at=d.get("expires_at"),
                revoked_at=None,
            )

            if rec.verify(token):
                return rec

        return None

    def revoke_key(self, key_id: str) -> bool:
        """Revokes an API key immediately."""
        key = self.get_key(key_id)
        if not key:
            raise ValueError(f"API key '{key_id}' does not exist.")

        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        conn = self.db.get_connection()
        with conn:
            conn.execute("UPDATE api_keys SET revoked_at = ? WHERE key_id = ?", (now, key_id))
        return True

    def delete_key(self, key_id: str) -> bool:
        """Removes an API key record permanently."""
        conn = self.db.get_connection()
        with conn:
            conn.execute("DELETE FROM api_keys WHERE key_id = ?", (key_id,))
        return True
