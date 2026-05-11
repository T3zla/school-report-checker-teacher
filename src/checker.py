from __future__ import annotations

import re

import pandas as pd

from src.models import Issue, ReportCheckResult
from src.parsers import normalize_name, normalize_pronoun


KNOWN_HEADINGS = [
    "Personal, Social and Emotional Development",
    "Communication and Language",
    "Physical Development",
    "Literacy",
    "Mathematics",
    "Understanding the World",
    "Expressive Arts and Design",
    "Mandarin",
    "Summary",
]


def find_nearest_heading(text: str, position: int) -> str:
    text_before = text[:position]
    lines = text_before.splitlines()

    for line in reversed(lines):
        clean_line = line.strip()

        if not clean_line:
            continue

        for heading in KNOWN_HEADINGS:
            if heading.lower() in clean_line.lower():
                return f"{heading} section"

    return "Body of report — section not detected"


def get_sentence_around_position(text: str, position: int) -> str:
    start = max(
        text.rfind(".", 0, position),
        text.rfind("!", 0, position),
        text.rfind("?", 0, position),
        text.rfind("\n", 0, position),
    )

    end_candidates = [
        text.find(".", position),
        text.find("!", position),
        text.find("?", position),
        text.find("\n", position),
    ]
    end_candidates = [idx for idx in end_candidates if idx != -1]

    end = min(end_candidates) if end_candidates else len(text)

    return text[start + 1 : end + 1].strip()


def detect_pronoun_mismatch(report_text: str, expected_pronoun: str) -> list[dict]:
    expected_pronoun = normalize_pronoun(expected_pronoun)

    if not expected_pronoun:
        return []

    text = report_text.lower()

    wrong_sets = {
        "he": [r"\bshe\b", r"\bher\b", r"\bhers\b"],
        "she": [r"\bhe\b", r"\bhim\b", r"\bhis\b"],
        "they": [
            r"\bhe\b",
            r"\bhim\b",
            r"\bhis\b",
            r"\bshe\b",
            r"\bher\b",
            r"\bhers\b",
        ],
    }

    hits = []

    for pattern in wrong_sets.get(expected_pronoun, []):
        for match in re.finditer(pattern, text):
            sentence = get_sentence_around_position(report_text, match.start())
            location_hint = find_nearest_heading(report_text, match.start())

            hits.append(
                {
                    "snippet": sentence,
                    "location_hint": location_hint,
                }
            )

    return hits


def detect_wrong_names(
    report_text: str,
    expected_names: list[str],
    all_names: list[str],
) -> list[dict]:
    text = report_text.lower()
    expected_norms = {normalize_name(name) for name in expected_names if name}
    hits = []

    for name in all_names:
        if not name:
            continue

        name_norm = normalize_name(name)

        if not name_norm:
            continue

        if name_norm in expected_norms:
            continue

        match = re.search(rf"\b{re.escape(name.lower())}\b", text)

        if match:
            hits.append(
                {
                    "name": name,
                    "location_hint": find_nearest_heading(report_text, match.start()),
                    "snippet": get_sentence_around_position(report_text, match.start()),
                }
            )

    return hits


def detect_expected_name_missing(report_text: str, expected_name: str) -> bool:
    if not expected_name:
        return False

    expected_parts = normalize_name(expected_name).split()

    if not expected_parts:
        return False

    text_norm = normalize_name(report_text)

    return not any(part in text_norm for part in expected_parts)


def find_matching_row(report_file_name: str, report_text: str, class_df: pd.DataFrame):
    if len(class_df) == 1:
        return class_df.iloc[0]

    file_and_text = f"{report_file_name}\n{report_text}".lower()

    # 1. Prefer full-name matches first.
    # This helps with duplicate report names like "Isabelle".
    for _, row in class_df.iterrows():
        full_name = str(row.get("Name", "")).strip()

        if full_name and full_name.lower() in file_and_text:
            return row

    # 2. Then try unique report names only.
    report_name_counts = (
        class_df["Report Name"]
        .astype(str)
        .str.strip()
        .str.lower()
        .value_counts()
        .to_dict()
    )

    for _, row in class_df.iterrows():
        report_name = str(row.get("Report Name", "")).strip()
        report_name_key = report_name.lower()

        if not report_name:
            continue

        if report_name_counts.get(report_name_key, 0) != 1:
            continue

        if report_name_key in file_and_text:
            return row

    return None


def collect_all_possible_names(class_df: pd.DataFrame) -> list[str]:
    names = []

    for _, row in class_df.iterrows():
        full_name = str(row.get("Name", "")).strip()
        report_name = str(row.get("Report Name", "")).strip()

        if full_name:
            names.append(full_name)

        if report_name:
            names.append(report_name)

    return sorted(set(names))


def deduplicate_issues(issues: list[Issue]) -> list[Issue]:
    grouped: dict[tuple[str, str | None, str], Issue] = {}

    for issue in issues:
        key = (issue.issue_type, issue.location_hint, issue.snippet)
        grouped.setdefault(key, issue)

    return list(grouped.values())


def check_report(
    report_file_name: str,
    report_text: str,
    class_df: pd.DataFrame,
) -> ReportCheckResult:
    result = ReportCheckResult(report_file=report_file_name)

    matched_row = find_matching_row(report_file_name, report_text, class_df)

    if matched_row is None:
        result.issues.append(
            Issue(
                issue_type="human_check_needed",
                severity="high",
                explanation="Could not confidently match this report to a student.",
                suggested_check="Check the file name or student name manually.",
                location_hint="File name or report text",
            )
        )
        result.overall_status = "human_review_needed"
        return result

    expected_full_name = str(matched_row.get("Name", "")).strip()
    expected_report_name = str(matched_row.get("Report Name", "")).strip()
    expected_pronoun = str(matched_row.get("Pronoun", "")).strip()

    result.matched_child_name = expected_full_name or expected_report_name
    result.matched_report_name = expected_report_name
    result.detected_full_name = expected_full_name
    result.detected_report_name = expected_report_name

    expected_name_for_check = expected_report_name or expected_full_name

    if expected_name_for_check and detect_expected_name_missing(
        report_text,
        expected_name_for_check,
    ):
        result.issues.append(
            Issue(
                issue_type="name_mismatch",
                severity="medium",
                explanation=(
                    f"The expected student name '{expected_name_for_check}' "
                    "was not clearly found in the report."
                ),
                suggested_check="Check that the correct child's name appears in the report.",
                location_hint="Whole report",
            )
        )

    all_possible_names = collect_all_possible_names(class_df)

    valid_names_for_this_student = [
        expected_full_name,
        expected_report_name,
    ]

    wrong_name_hits = detect_wrong_names(
        report_text=report_text,
        expected_names=valid_names_for_this_student,
        all_names=all_possible_names,
    )

    for hit in wrong_name_hits:
        result.issues.append(
            Issue(
                issue_type="copy_paste_error",
                severity="high",
                snippet=hit.get("snippet") or hit["name"],
                explanation=f"Another student's name appears in this report: {hit['name']}.",
                suggested_check="Check whether this is a copy-paste mistake.",
                location_hint=hit["location_hint"],
            )
        )

    pronoun_hits = detect_pronoun_mismatch(report_text, expected_pronoun)

    for hit in pronoun_hits:
        result.issues.append(
            Issue(
                issue_type="pronoun_mismatch",
                severity="high",
                snippet=hit["snippet"],
                explanation=f"A pronoun may not match the expected pronoun '{expected_pronoun}'.",
                suggested_check="Check the pronouns in this section.",
                location_hint=hit["location_hint"],
            )
        )

    result.issues = deduplicate_issues(result.issues)

    if result.issues:
        result.overall_status = "issues_found"
    else:
        result.overall_status = "ok"

    return result
