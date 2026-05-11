from __future__ import annotations

import html
import io
import uuid

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from src.checker import check_report
from src.models import ChildRecord
from src.parsers import read_class_list, read_report_file


st.set_page_config(page_title="Report Quick Check", layout="wide")

st.title("Report Quick Check")
st.caption("Fast rule-based report checking for teachers.")


# ---------------------------------------------------
# HELPERS
# ---------------------------------------------------


def render_copy_button(text: str) -> None:
    button_id = f"copy_{uuid.uuid4().hex}"
    escaped = html.escape(text)

    components.html(
        f"""
        <div>
            <textarea id="{button_id}_text" style="display:none;">{escaped}</textarea>

            <button
                onclick="
                    navigator.clipboard.writeText(
                        document.getElementById('{button_id}_text').value
                    ).then(() => {{
                        const msg = document.getElementById('{button_id}_msg');
                        msg.innerText = 'Copied';
                        setTimeout(() => msg.innerText = '', 2000);
                    }});
                "

                style="
                    padding: 8px 16px;
                    border: none;
                    border-radius: 8px;
                    background: #2e7d32;
                    color: white;
                    cursor: pointer;
                    font-size: 14px;
                "
            >
                Copy Feedback
            </button>

            <span
                id="{button_id}_msg"
                style="margin-left: 10px; color: #8bc34a;"
            ></span>
        </div>
        """,
        height=50,
    )


def build_feedback_text(results) -> str:
    blocks = []

    for result in results:
        child_name = (
            result.matched_child_name
            or result.detected_report_name
            or result.report_file
        )

        lines = [child_name]

        if not result.issues:
            lines.append("No issues found.")
        else:
            for issue in result.issues:
                location = (
                    issue.location_hint or issue.suggested_check or "Check report"
                )

                readable_issue = issue.issue_type.replace("_", " ")

                if issue.snippet:
                    lines.append(
                        f'- {readable_issue.capitalize()} ({location}): "{issue.snippet}"'
                    )
                else:
                    lines.append(f"- {readable_issue.capitalize()} ({location})")

        blocks.append("\n".join(lines))

    return "\n\n".join(blocks)


# ---------------------------------------------------
# QUICK CHECK
# ---------------------------------------------------

st.divider()

st.header("Single Report Quick Check")

quick_report = st.file_uploader(
    "Upload report",
    type=["doc", "docx", "txt"],
    key="quick_report_upload",
)

c1, c2, c3 = st.columns(3)

with c1:
    quick_name = st.text_input("Student Name")

with c2:
    quick_dob = st.text_input("Date of Birth")

with c3:
    quick_pronoun = st.selectbox(
        "Pronoun",
        ["", "he", "she", "they"],
    )

if st.button("Run Quick Check", type="primary"):
    if quick_report is None:
        st.error("Please upload a report.")
        st.stop()

    report_text = read_report_file(quick_report)

    class_df = pd.DataFrame(
        [
            {
                "Name": quick_name.strip(),
                "Report Name": quick_name.strip(),
                "Date of Birth": quick_dob.strip(),
                "Pronoun": quick_pronoun.strip(),
            }
        ]
    )

    # Remove blank fields
    if not quick_name.strip():
        class_df["Name"] = ""
        class_df["Report Name"] = ""

    if not quick_dob.strip():
        class_df["Date of Birth"] = pd.NA

    if not quick_pronoun.strip():
        class_df["Pronoun"] = ""

    result = check_report(
        quick_report.name,
        report_text,
        class_df,
    )

    feedback = build_feedback_text([result])

    st.subheader("Feedback")
    st.text_area(
        "Feedback",
        value=feedback,
        height=250,
        label_visibility="collapsed",
    )

    render_copy_button(feedback)


# ---------------------------------------------------
# BATCH CHECK
# ---------------------------------------------------

st.divider()

st.header("Batch Check")

batch_class_list = st.file_uploader(
    "Upload Excel class list",
    type=["csv", "xlsx"],
    key="batch_class_list",
)

batch_reports = st.file_uploader(
    "Upload reports",
    type=["doc", "docx", "txt"],
    accept_multiple_files=True,
    key="batch_reports",
)

if st.button("Run Batch Check"):
    if batch_class_list is None:
        st.error("Please upload a class list.")
        st.stop()

    if not batch_reports:
        st.error("Please upload reports.")
        st.stop()

    class_df = read_class_list(batch_class_list)

    results = []

    progress = st.progress(0)

    for idx, uploaded_file in enumerate(batch_reports):
        report_text = read_report_file(uploaded_file)

        result = check_report(
            uploaded_file.name,
            report_text,
            class_df,
        )

        results.append(result)

        progress.progress((idx + 1) / len(batch_reports))

    feedback = build_feedback_text(results)

    st.subheader("Feedback")

    st.text_area(
        "Feedback",
        value=feedback,
        height=450,
        label_visibility="collapsed",
    )

    render_copy_button(feedback)
