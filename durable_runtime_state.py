"""Small dict-compatible facade for restart-safe interactive workflow state.

Only workflow metadata belongs here. Resume text/file bytes are deliberately
never persisted; Resume Match stores only the vacancy hash needed to reconstruct
the selected job from the durable payload store.
"""
from __future__ import annotations

from typing import Any, Callable


_MISSING = object()


class RuntimeStateMap:
    """A minimal dict-like map backed by DatabaseConnection runtime-state APIs."""

    def __init__(
        self,
        db,
        state_key: str,
        *,
        ttl_seconds: int,
        encode: Callable[[Any], dict],
        decode: Callable[[dict], Any],
    ):
        self.db = db
        self.state_key = str(state_key)
        self.ttl_seconds = int(ttl_seconds)
        self.encode = encode
        self.decode = decode

    def __setitem__(self, user_id: int, value: Any) -> None:
        encoded = self.encode(value)
        if not isinstance(encoded, dict):
            raise TypeError("runtime state encoder must return dict")
        self.db.set_runtime_state(
            int(user_id),
            self.state_key,
            encoded,
            ttl_seconds=self.ttl_seconds,
        )

    def _load(self, user_id: int):
        state = self.db.get_runtime_state(int(user_id), self.state_key)
        if state is None:
            return _MISSING
        try:
            value = self.decode(state)
        except Exception:
            value = None
        if value is None:
            self.db.delete_runtime_state(int(user_id), self.state_key)
            return _MISSING
        return value

    def __contains__(self, user_id: object) -> bool:
        try:
            uid = int(user_id)
        except (TypeError, ValueError):
            return False
        return self._load(uid) is not _MISSING

    def __getitem__(self, user_id: int):
        value = self._load(int(user_id))
        if value is _MISSING:
            raise KeyError(user_id)
        return value

    def get(self, user_id: int, default=None):
        value = self._load(int(user_id))
        return default if value is _MISSING else value

    def pop(self, user_id: int, default=_MISSING):
        uid = int(user_id)
        value = self._load(uid)
        if value is _MISSING:
            if default is _MISSING:
                raise KeyError(user_id)
            return default
        self.db.delete_runtime_state(uid, self.state_key)
        return value

    def clear_user(self, user_id: int) -> None:
        self.db.delete_runtime_state(int(user_id), self.state_key)
