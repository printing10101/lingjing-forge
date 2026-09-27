"""Agent Token Database and Authentication System."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

logger = logging.getLogger(__name__)


class PermissionScope(str, Enum):
    R = "R"
    W = "W"
    B = "B"
    N = "N"
    C = "C"
    T = "T"


@dataclass
class AgentToken:
    agent_id: str
    token_hash: str
    scopes: list[str]
    created_at: float
    expires_at: float | None
    paper_only: bool
    is_active: bool = True


class AgentTokenStore:
    """Simple JSON-based token storage."""

    def __init__(self, storage_path: str | None = None):
        if storage_path is None:
            storage_path = os.environ.get(
                "AGENT_TOKEN_STORE",
                str(Path.home() / ".lingjing" / "agent_tokens.json"),
            )
        self._storage_path = Path(storage_path)
        self._tokens: dict[str, AgentToken] = {}
        self._load()

    def _load(self):
        if self._storage_path.exists():
            try:
                data = json.loads(self._storage_path.read_text())
                for agent_id, t in data.items():
                    self._tokens[agent_id] = AgentToken(**t)
            except (json.JSONDecodeError, KeyError, TypeError, ValueError, OSError) as e:
                logger.warning("Failed to load agent token store: %s", e, exc_info=True)

    def _save(self):
        self._storage_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            aid: {
                "agent_id": t.agent_id,
                "token_hash": t.token_hash,
                "scopes": t.scopes,
                "created_at": t.created_at,
                "expires_at": t.expires_at,
                "paper_only": t.paper_only,
                "is_active": t.is_active,
            }
            for aid, t in self._tokens.items()
        }
        tmp = self._storage_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2))
        tmp.replace(self._storage_path)
        if os.name != "nt":
            os.chmod(str(self._storage_path), 0o600)

    @staticmethod
    def hash_token(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    def create_token(
        self,
        scopes: list[str],
        expires_in: int | None = None,
        paper_only: bool = True,
    ) -> tuple[str, AgentToken]:
        import uuid

        raw = f"lj_agent_{secrets.token_hex(16)}"
        agent_id = str(uuid.uuid4())
        now = time.time()
        token = AgentToken(
            agent_id=agent_id,
            token_hash=self.hash_token(raw),
            scopes=scopes,
            created_at=now,
            expires_at=(now + expires_in) if expires_in else None,
            paper_only=paper_only,
        )
        self._tokens[agent_id] = token
        self._save()
        return raw, token

    def validate_token(self, raw_token: str) -> AgentToken | None:
        h = self.hash_token(raw_token)
        for t in self._tokens.values():
            if t.token_hash == h and t.is_active:
                if t.expires_at and time.time() > t.expires_at:
                    t.is_active = False
                    self._save()
                    return None
                return t
        return None

    def revoke_token(self, agent_id: str) -> bool:
        if agent_id in self._tokens:
            self._tokens[agent_id].is_active = False
            self._save()
            return True
        return False

    def list_tokens(self) -> list[dict]:
        result = []
        for t in self._tokens.values():
            result.append(
                {
                    "agent_id": t.agent_id,
                    "scopes": t.scopes,
                    "created_at": t.created_at,
                    "expires_at": t.expires_at,
                    "paper_only": t.paper_only,
                    "is_active": t.is_active,
                    "token_prefix": "lj_agent_" + t.token_hash[:8] + "...",
                }
            )
        return result

    def revoke_t_tokens(self) -> int:
        count = 0
        for t in self._tokens.values():
            if "T" in t.scopes and t.is_active:
                t.is_active = False
                count += 1
        if count > 0:
            self._save()
        return count


agent_token_store = AgentTokenStore()
