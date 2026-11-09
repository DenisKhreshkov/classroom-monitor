"""Shared models and Classroom date conversion (dueTime is UTC)."""

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, model_validator

BERLIN = ZoneInfo("Europe/Berlin")
COURSEWORK_RECENT_DAYS = 7


def classroom_timestamp(value) -> datetime | None:
    """Parse timezone-aware RFC 3339 metadata without guessing missing dates."""
    if not isinstance(value, str):
        return None
    try:
        result = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return result.astimezone(UTC) if result.tzinfo is not None else None


@dataclass
class Assignment:
    course_id: str
    course_name: str
    id: str
    title: str
    raw: dict
    material_text: list[dict] = field(default_factory=list)
    course_state: str = "ACTIVE"

    def eligible_for_analysis(self, now: datetime) -> bool:
        """Keep recent published work OR an open deadline, in active courses.

        updateTime is deliberately excluded: editing an old task must not make
        historical homework recent. scheduledTime is a publication proxy only
        for already-published work and only after that timestamp has arrived.
        """
        if self.course_state != "ACTIVE" or self.raw.get("state", "PUBLISHED") != "PUBLISHED":
            return False
        current = now.astimezone(UTC)
        cutoff = current - timedelta(days=COURSEWORK_RECENT_DAYS)
        for field_name in ("creationTime", "scheduledTime"):
            timestamp = classroom_timestamp(self.raw.get(field_name))
            if timestamp is not None and cutoff <= timestamp <= current:
                return True
        try:
            due = self.due_at
        except (KeyError, TypeError, ValueError, OverflowError):
            return False
        if due is None:
            return False
        if "dueTime" in self.raw:
            return due >= current
        # For date-only metadata, retain today's deadline through the local day.
        return due.date() >= now.astimezone(BERLIN).date()

    @property
    def due_at(self) -> datetime | None:
        day = self.raw.get("dueDate")
        if not day:
            return None
        time = self.raw.get("dueTime", {})
        return datetime(
            day["year"],
            day["month"],
            day["day"],
            time.get("hours", 0),
            time.get("minutes", 0),
            time.get("seconds", 0),
            tzinfo=UTC,
        ).astimezone(BERLIN)

    @property
    def link(self) -> str:
        return self.raw.get("alternateLink", "")

    def evidence(self) -> dict:
        return {
            "course_id": self.course_id,
            "course_name": self.course_name,
            "id": self.id,
            "coursework": self.raw,
            "material_text": self.material_text,
        }

    def fingerprint(self, model: str) -> str:
        # Date changes are handled by the monitor without paying for daily reclassification.
        data = {"version": 1, "model": model, "evidence": self.evidence()}
        return hashlib.sha256(
            json.dumps(data, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()


class Analysis(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    is_assessment: bool
    assessment_type: str
    confidence: int = Field(ge=0, le=100)
    reason: str
    relevant_date: str | None
    important_details: list[str]
    category: Literal["definite", "possible", "homework"]

    @model_validator(mode="after")
    def consistent(self) -> "Analysis":
        if self.is_assessment != (self.category != "homework"):
            raise ValueError("Assessment flag and category disagree")
        if self.relevant_date is not None:
            date.fromisoformat(self.relevant_date)
        return self
