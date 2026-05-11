from __future__ import annotations

import re

import pandas as pd

from src.models import Issue, ReportCheckResult
from src.parsers import (
    extract_report_header_fields,
    normalize_name,
    normalize_pronoun,
)


KNOWN_HEADINGS = [
    "Personal, Social and Emotional Development",
    "Communication and Language",
    "Physical Development",
    "Literacy",
    "Mathematics",
    "Understanding the World",
    "Expressive Arts and Design",
    "Mandarin",
    "The following goals have been set",
]


def find_matching_child(header: dict, class_df: pd.DataFrame):
    full_name_norm = normalize_name(header["full_name_in_report"])
    report_name_norm = normalize_name(header["report_name_in_report"])
    dob = header["dob_in_report"]

    if full_name_norm:
        matches = class_df[class_df["name_norm"] == full_name_norm]
        if len(matches) == 1:
            return matches.iloc[0]

    if report_name_norm and pd.notna(dob):
        matches = class_df[
            (class_df["report_name_norm"] == report_name_norm)
            & (class_df["Date of Birth"] == dob)
        ]
        if len(matches) == 1:
            return matches.iloc[0]

    if report_name_norm:
        matches = class_df[class_df["report_name_norm"] == report_name_norm]
        if len(matches) == 1:
            return matches.iloc[0]

    if pd.notna(dob):
        matches = class_df[class_df["Date of Birth"] == dob]
        if len(matches) == 1:
            return matches.iloc[0]

    return None


def find_nearest_heading(text: str, position: int) -> str:
    """
    Find the closest known heading above the given text position.
    """
    text_before = text[:position]
    lines = text_before.splitlines()

    for line in reversed(lines):
        clean_line = line.strip()
        if not clean_line:
            continue

        for heading in KNOWN_HEADINGS:
            if heading.lower() in clean_line.lower():
                if heading == "The following goals have been set":
                    return "Goals section"
                return f"{heading} section"

    return "Narrative paragraph"


def detect_pronoun_mismatch(report_text: str, expected_pronoun: str):
    text = report_text.lower()

    wrong_sets = {
        "he": [r"\bshe\b", r"\bher\b", r"\bhers\b"],
        "she": [r"\bhe\b", r"\bhim\b", r"\bhis\b"],
        "they": [r"\bhe\b", r"\bhim\b", r"\bhis\b", r"\bshe\b", r"\bher\b", r"\bhers\b"],
    }

    patterns = wrong_sets.get(expected_pronoun, [])
    hits = []

    for pattern in patterns:
        for match in re.finditer(pattern, text):
            start = max(match.start() - 50, 0)
            end = min(match.end() + 80, len(report_text))
            snippet = report_text[start:end]
            location_hint = find_nearest_heading(report_text, match.start())

            hits.append(
                {
                    "snippet": snippet,
                    "location_hint": location_hint,
                }
            )

    return hits


def detect_copy_paste_names(report_text: str, matched_row: pd.Series, class_df: pd.DataFrame):
    text = report_text.lower()
    matched_full = str(matched_row["Name"])
    matched_report = str(matched_row["Report Name"]).lower()

    hits = []

    for _, row in class_df.iterrows():
        other_full = str(row["Name"])
        other_report = str(row["Report Name"])

        if other_full == matched_full:
            continue

        if other_report.lower() == matched_report:
            continue

        match = re.search(rf"\b{re.escape(other_report.lower())}\b", text)
        if other_report and match:
            location_hint = find_nearest_heading(report_text, match.start())
            hits.append(
                {
                    "name": other_report,
                    "location_hint": location_hint,
                }
            )

    return hits


def deduplicate_issues(issues: list[Issue]) -> list[Issue]:
    """
    Collapse identical issues so the teacher-facing output is cleaner.
    """
    grouped: dict[tuple[str, str | None], list[Issue]] = {}

    for issue in issues:
        key = (issue.issue_type, issue.location_hint)
        grouped.setdefault(key, []).append(issue)

    deduped: list[Issue] = []

    for (issue_type, location_hint), group in grouped.items():
        if issue_type == "pronoun_mismatch" and len(group) > 1:
            deduped.append(
                Issue(
                    issue_type="pronoun_mismatch",
                    severity="high",
                    snippet=group[0].snippet,
                    explanation=f"Multiple pronoun inconsistencies were found.",
                    suggested_check="Check the child's pronouns carefully in this section.",
                    location_hint=location_hint,
                )
            )
        else:
            deduped.extend(group)

    return deduped


def check_report(report_file_name: str, report_text: str, class_df: pd.DataFrame) -> ReportCheckResult:
    header = extract_report_header_fields(report_text)

    result = ReportCheckResult(
        report_file=report_file_name,
        student_raw=header["student_raw"],
        detected_full_name=header["full_name_in_report"],
        detected_report_name=header["report_name_in_report"],
    )

    if not header["student_raw"]:
        result.issues.append(
            Issue(
                issue_type="missing_reference_field",
                severity="high",
                explanation="Could not find the student name in the report header.",
                suggested_check="Check that the report contains a Student field in the header.",
                location_hint="Student header",
            )
        )

    if pd.isna(header["dob_in_report"]):
        result.issues.append(
            Issue(
                issue_type="missing_reference_field",
                severity="high",
                explanation="Could not read a valid date of birth from the report header.",
                suggested_check="Check the date of birth in the header.",
                location_hint="Student header",
            )
        )

    matched_row = find_matching_child(header, class_df)

    if matched_row is None:
        result.issues.append(
            Issue(
                issue_type="human_check_needed",
                severity="high",
                explanation="The report could not be matched confidently to the class list.",
                suggested_check="Check the student name, report name, and date of birth manually.",
                location_hint="Student header",
            )
        )
        result.overall_status = "human_review_needed"
        result.teacher_summary = ""
        return result

    result.matched_child_name = str(matched_row["Name"])
    result.matched_report_name = str(matched_row["Report Name"])

    if header["report_name_in_report"]:
        expected_report_name = str(matched_row["Report Name"])
        if normalize_name(header["report_name_in_report"]) != normalize_name(expected_report_name):
            result.issues.append(
                Issue(
                    issue_type="name_mismatch",
                    severity="high",
                    snippet=header["student_raw"],
                    explanation=(
                        f"The report name '{header['report_name_in_report']}' does not match "
                        f"the class list name '{expected_report_name}'."
                    ),
                    suggested_check="Check the child name in the report header.",
                    location_hint="Student header",
                )
            )
    else:
        result.issues.append(
            Issue(
                issue_type="missing_reference_field",
                severity="medium",
                explanation="No report name in brackets was found in the student header.",
                suggested_check="Check the student line in the header.",
                location_hint="Student header",
            )
        )

    report_dob = header["dob_in_report"]
    class_dob = matched_row["Date of Birth"]

    if pd.notna(report_dob) and pd.notna(class_dob) and report_dob != class_dob:
        result.issues.append(
            Issue(
                issue_type="birthday_mismatch",
                severity="high",
                snippet=header["dob_in_report_raw"],
                explanation=(
                    f"The date of birth in the report does not match the class list "
                    f"({class_dob.strftime('%Y-%m-%d')})."
                ),
                suggested_check="Check the date of birth in the report header.",
                location_hint="Student header",
            )
        )

    expected_pronoun = normalize_pronoun(str(matched_row["Pronoun"]))
    pronoun_hits = detect_pronoun_mismatch(report_text, expected_pronoun)

    for hit in pronoun_hits:
        result.issues.append(
            Issue(
                issue_type="pronoun_mismatch",
                severity="high",
                snippet=hit["snippet"],
                explanation=f"A pronoun in the report may not match the expected pronoun '{expected_pronoun}'.",
                suggested_check="Check the child's pronouns in this section.",
                location_hint=hit.get("location_hint"),
            )
        )

    copy_paste_hits = detect_copy_paste_names(report_text, matched_row, class_df)
    for hit in copy_paste_hits:
        result.issues.append(
            Issue(
                issue_type="copy_paste_error",
                severity="high",
                snippet=hit["name"],
                explanation="Another child's name appears in this report, which may indicate a copy-paste error.",
                suggested_check="Check this section for the wrong child name.",
                location_hint=hit["location_hint"],
            )
        )

    result.issues = deduplicate_issues(result.issues)

    if any(issue.issue_type == "human_check_needed" for issue in result.issues):
        result.overall_status = "human_review_needed"
    elif result.issues:
        result.overall_status = "issues_found"
    else:
        result.overall_status = "ok"

    result.teacher_summary = ""

    return result