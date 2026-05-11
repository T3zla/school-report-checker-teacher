from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


IssueType = Literal[
    "name_mismatch",
    "pronoun_mismatch",
    "birthday_mismatch",
    "copy_paste_error",
    "missing_reference_field",
    "human_check_needed",
]

Severity = Literal["high", "medium", "low"]
OverallStatus = Literal["ok", "issues_found", "human_review_needed"]


class ChildRecord(BaseModel):
    full_name: str
    report_name: str
    date_of_birth: str
    pronoun: Literal["he", "she", "they"]


class Issue(BaseModel):
    issue_type: IssueType
    severity: Severity
    snippet: str = ""
    explanation: str
    suggested_check: str
    location_hint: str | None = None


class ReportCheckResult(BaseModel):
    report_file: str
    student_raw: str = ""
    detected_full_name: str = ""
    detected_report_name: str = ""
    matched_child_name: str | None = None
    matched_report_name: str | None = None
    issues: list[Issue] = Field(default_factory=list)
    overall_status: OverallStatus = "ok"
    teacher_summary: str = ""