"""Process configuration from environment variables (see .env.example)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path


def _decimal(value: str | None, default: Decimal) -> Decimal:
    try:
        result=Decimal(value) if value is not None and value!='' else default
    except InvalidOperation as exc:
        raise ValueError("invalid budget configuration") from exc
    if not result.is_finite():
        raise ValueError("budget must be finite")
    return result


@dataclass
class Settings:
    database_url: str | None
    model_base_url: str | None
    model_name: str | None
    model_api_key: str | None
    runtime_dir: Path
    admin_credential: str | None
    budget_cny: Decimal
    per_call_reserve_cny: Decimal
    top_k: int

    def __post_init__(self):
        if not self.budget_cny.is_finite() or self.budget_cny<0:
            raise ValueError("budget must be nonnegative and finite")
        if not self.per_call_reserve_cny.is_finite() or self.per_call_reserve_cny<=0:
            raise ValueError("per-call reservation must be positive and finite")

    @classmethod
    def from_env(cls, env: dict | None = None) -> Settings:
        source = os.environ if env is None else env
        database_url = (source.get("DATABASE_URL") or "").strip() or None
        return cls(
            database_url=database_url,
            model_base_url=(source.get("MODEL_BASE_URL") or "").strip() or None,
            model_name=(source.get("MODEL_NAME") or "").strip() or None,
            model_api_key=(source.get("MODEL_API_KEY") or "").strip() or None,
            runtime_dir=Path(source.get("RUNTIME_DIR") or "./runtime"),
            admin_credential=(source.get("ADMIN_CREDENTIAL") or "").strip() or None,
            budget_cny=_decimal(source.get("BUDGET_CNY"), Decimal("100.00")),
            per_call_reserve_cny=_decimal(source.get("PER_CALL_RESERVE_CNY"), Decimal("0.10")),
            top_k=int(source.get("RETRIEVAL_TOP_K") or "10"),
        )

    @property
    def model_configured(self) -> bool:
        return bool(
            self.model_base_url and self.model_name and self.model_api_key
        )
