"""FastAPI request contracts. Guide-locked fields are deliberately absent."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator


class LoginRequest(BaseModel):
    password: str = Field(min_length=1, max_length=512)


class RunCreate(BaseModel):
    run_kind: Literal["dry_run", "live"]
    mode: Literal["safe", "aggressive", "degen"] = "safe"
    session_budget: float = Field(gt=0, le=1_000_000)
    min_bet: float = Field(gt=0, le=100_000)
    once: bool = False
    max_trades: Optional[int] = Field(default=None, ge=1, le=100_000)
    password: Optional[str] = Field(default=None, max_length=512)
    confirmation_text: Optional[str] = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def validate_budget(self):
        if self.min_bet > self.session_budget:
            raise ValueError("Min bet không được lớn hơn hạn mức phiên")
        return self


class BacktestCreate(BaseModel):
    hours: int = Field(default=72, ge=6, le=720)
    starting_bankroll: float = Field(default=100, gt=0, le=1_000_000)
    min_bet: float = Field(default=1, gt=0, le=100_000)

    @model_validator(mode="after")
    def validate_budget(self):
        if self.min_bet > self.starting_bankroll:
            raise ValueError("Min bet không được lớn hơn starting bankroll")
        return self
