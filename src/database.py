from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pandas as pd

DB_PATH = Path("school_report_checker.db")


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def column_exists(conn: sqlite3.Connection, table_name: str, column_name: str) -> bool:
    cur = conn.cursor()
    cur.execute(f"PRAGMA table_info({table_name})")
    rows = cur.fetchall()
    return any(row["name"] == column_name for row in rows)


def init_db() -> None:
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS classes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            class_name TEXT NOT NULL,
            campus TEXT NOT NULL DEFAULT 'Unassigned',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS students (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            class_id INTEGER NOT NULL,
            full_name TEXT NOT NULL,
            report_name TEXT NOT NULL,
            date_of_birth TEXT,
            pronoun TEXT,
            UNIQUE(class_id, full_name),
            FOREIGN KEY (class_id) REFERENCES classes (id)
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS checks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            class_id INTEGER NOT NULL,
            student_id INTEGER,
            uploaded_file_name TEXT NOT NULL,
            detected_full_name TEXT,
            detected_report_name TEXT,
            matched_child_name TEXT,
            matched_report_name TEXT,
            overall_status TEXT NOT NULL,
            issue_count INTEGER NOT NULL,
            issues_json TEXT NOT NULL,
            teacher_summary TEXT,
            checked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_final_ok INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (class_id) REFERENCES classes (id),
            FOREIGN KEY (student_id) REFERENCES students (id)
        )
        """
    )

    if not column_exists(conn, "classes", "current_summary"):
        cur.execute("ALTER TABLE classes ADD COLUMN current_summary TEXT NOT NULL DEFAULT ''")

    if not column_exists(conn, "students", "current_status"):
        cur.execute(
            "ALTER TABLE students ADD COLUMN current_status TEXT NOT NULL DEFAULT 'report_not_submitted'"
        )

    if not column_exists(conn, "students", "last_checked_at"):
        cur.execute("ALTER TABLE students ADD COLUMN last_checked_at TIMESTAMP")

    if not column_exists(conn, "classes", "campus"):
        cur.execute("ALTER TABLE classes ADD COLUMN campus TEXT NOT NULL DEFAULT 'Unassigned'")

    conn.commit()
    conn.close()


def create_class(class_name: str, campus: str) -> int:
    class_name = class_name.strip()
    campus = campus.strip()

    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT id
        FROM classes
        WHERE class_name = ?
        LIMIT 1
        """,
        (class_name,),
    )
    existing = cur.fetchone()

    if existing is not None:
        class_id = int(existing["id"])
        cur.execute(
            """
            UPDATE classes
            SET campus = ?
            WHERE id = ?
            """,
            (campus, class_id),
        )
        conn.commit()
        conn.close()
        return class_id

    cur.execute(
        """
        INSERT INTO classes (class_name, campus)
        VALUES (?, ?)
        """,
        (class_name, campus),
    )
    class_id = int(cur.lastrowid)
    conn.commit()
    conn.close()
    return class_id


def seed_default_classes(default_classes_by_campus: dict[str, list[str]]) -> None:
    for campus, class_names in default_classes_by_campus.items():
        for class_name in class_names:
            create_class(class_name, campus)


def delete_class(class_id: int) -> None:
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("DELETE FROM checks WHERE class_id = ?", (class_id,))
    cur.execute("DELETE FROM students WHERE class_id = ?", (class_id,))
    cur.execute("DELETE FROM classes WHERE id = ?", (class_id,))

    conn.commit()
    conn.close()


def clear_class_checks(class_id: int) -> None:
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("DELETE FROM checks WHERE class_id = ?", (class_id,))
    cur.execute(
        """
        UPDATE students
        SET current_status = 'report_not_submitted',
            last_checked_at = NULL
        WHERE class_id = ?
        """,
        (class_id,),
    )

    conn.commit()
    conn.close()
    rebuild_class_summary(class_id)


def get_class_by_id(class_id: int) -> sqlite3.Row | None:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, class_name, campus, created_at, current_summary
        FROM classes
        WHERE id = ?
        LIMIT 1
        """,
        (class_id,),
    )
    row = cur.fetchone()
    conn.close()
    return row


def get_classes_by_campus(campus: str) -> list[sqlite3.Row]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, class_name, campus, created_at, current_summary
        FROM classes
        WHERE campus = ?
        ORDER BY LOWER(class_name) ASC
        """,
        (campus,),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def upsert_students_for_class(class_id: int, class_df: pd.DataFrame) -> None:
    conn = get_connection()
    cur = conn.cursor()

    for _, row in class_df.iterrows():
        dob_value = row["Date of Birth"]
        dob_text = None
        if pd.notna(dob_value):
            dob_text = pd.to_datetime(dob_value).strftime("%Y-%m-%d")

        full_name = str(row["Name"]).strip()
        report_name = str(row["Report Name"]).strip()
        pronoun = str(row["Pronoun"]).strip()

        cur.execute(
            """
            INSERT INTO students (
                class_id,
                full_name,
                report_name,
                date_of_birth,
                pronoun,
                current_status
            ) VALUES (?, ?, ?, ?, ?, 'report_not_submitted')
            ON CONFLICT(class_id, full_name) DO UPDATE SET
                report_name = excluded.report_name,
                date_of_birth = excluded.date_of_birth,
                pronoun = excluded.pronoun
            """,
            (
                class_id,
                full_name,
                report_name,
                dob_text,
                pronoun,
            ),
        )

    conn.commit()
    conn.close()
    rebuild_class_summary(class_id)


def get_students_for_class(class_id: int) -> list[sqlite3.Row]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT
            id,
            full_name,
            report_name,
            date_of_birth,
            pronoun,
            current_status,
            last_checked_at
        FROM students
        WHERE class_id = ?
        ORDER BY LOWER(report_name) ASC, LOWER(full_name) ASC
        """,
        (class_id,),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def get_class_list_df(class_id: int) -> pd.DataFrame:
    rows = get_students_for_class(class_id)

    data = []
    for row in rows:
        dob_value = row["date_of_birth"]
        parsed_dob = pd.to_datetime(dob_value, errors="coerce") if dob_value else pd.NaT

        data.append(
            {
                "Name": row["full_name"],
                "Report Name": row["report_name"],
                "Date of Birth": parsed_dob,
                "Pronoun": row["pronoun"],
            }
        )

    df = pd.DataFrame(data)

    if not df.empty:
        from src.parsers import normalize_name

        df["name_norm"] = df["Name"].map(normalize_name)
        df["report_name_norm"] = df["Report Name"].map(normalize_name)

    return df


def get_student_id_for_result(class_id: int, matched_child_name: str | None) -> int | None:
    if not matched_child_name:
        return None

    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id
        FROM students
        WHERE class_id = ? AND full_name = ?
        LIMIT 1
        """,
        (class_id, matched_child_name),
    )
    row = cur.fetchone()
    conn.close()

    if row is None:
        return None

    return int(row["id"])


def save_check_result(class_id: int, result) -> None:
    student_id = get_student_id_for_result(class_id, result.matched_child_name)

    issues_payload = [
        {
            "issue_type": issue.issue_type,
            "severity": issue.severity,
            "snippet": issue.snippet,
            "explanation": issue.explanation,
            "suggested_check": issue.suggested_check,
            "location_hint": issue.location_hint,
        }
        for issue in result.issues
    ]

    is_final_ok = 1 if result.overall_status == "ok" else 0

    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        INSERT INTO checks (
            class_id,
            student_id,
            uploaded_file_name,
            detected_full_name,
            detected_report_name,
            matched_child_name,
            matched_report_name,
            overall_status,
            issue_count,
            issues_json,
            teacher_summary,
            is_final_ok
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            class_id,
            student_id,
            result.report_file,
            result.detected_full_name,
            result.detected_report_name,
            result.matched_child_name,
            result.matched_report_name,
            result.overall_status,
            len(result.issues),
            json.dumps(issues_payload, ensure_ascii=False),
            result.teacher_summary,
            is_final_ok,
        ),
    )

    if student_id is not None:
        cur.execute(
            """
            UPDATE students
            SET current_status = ?, last_checked_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (result.overall_status, student_id),
        )

    conn.commit()
    conn.close()

    rebuild_class_summary(class_id)


def get_latest_check_for_student(class_id: int, student_id: int) -> sqlite3.Row | None:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT *
        FROM checks
        WHERE class_id = ? AND student_id = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (class_id, student_id),
    )
    row = cur.fetchone()
    conn.close()
    return row


def build_current_summary_text(class_id: int) -> str:
    class_row = get_class_by_id(class_id)
    students = get_students_for_class(class_id)

    if class_row is None:
        return ""

    class_name = class_row["class_name"]
    total = len(students)
    not_submitted = sum(1 for s in students if s["current_status"] == "report_not_submitted")
    issues_found = sum(1 for s in students if s["current_status"] == "issues_found")
    human_review = sum(1 for s in students if s["current_status"] == "human_review_needed")
    ok_count = sum(1 for s in students if s["current_status"] == "ok")

    lines = [
        class_name,
        "",
        f"Total students: {total}",
        f"OK: {ok_count}",
        f"Issues found: {issues_found}",
        f"Human review needed: {human_review}",
        f"Report not submitted: {not_submitted}",
        "",
        "Students needing attention:",
    ]

    needs_attention = False

    for student in students:
        status = student["current_status"]
        if status == "ok":
            continue

        needs_attention = True
        display_name = student["report_name"] or student["full_name"]

        if status == "report_not_submitted":
            lines.append(f"- {display_name}: report not submitted")
            continue

        latest_check = get_latest_check_for_student(class_id, student["id"])
        if latest_check is None:
            lines.append(f"- {display_name}: {status}")
            continue

        try:
            issues = json.loads(latest_check["issues_json"]) if latest_check["issues_json"] else []
        except Exception:
            issues = []

        if not issues:
            lines.append(f"- {display_name}: {status}")
            continue

        issue_descriptions = []
        for issue in issues[:5]:
            issue_type = str(issue.get("issue_type", "")).replace("_", " ")
            location = issue.get("location_hint") or issue.get("suggested_check") or "check report"
            issue_descriptions.append(f"{issue_type} ({location})")

        joined = "; ".join(issue_descriptions)
        lines.append(f"- {display_name}: {joined}")

    if not needs_attention:
        lines.append("- All students are currently OK.")

    return "\n".join(lines)


def rebuild_class_summary(class_id: int) -> None:
    summary_text = build_current_summary_text(class_id)

    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE classes
        SET current_summary = ?
        WHERE id = ?
        """,
        (summary_text, class_id),
    )
    conn.commit()
    conn.close()