from __future__ import annotations

import html
import json
import uuid

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from src.checker import check_report
from src.database import (
    clear_class_checks,
    create_class,
    delete_class,
    get_class_by_id,
    get_class_list_df,
    get_classes_by_campus,
    get_latest_check_for_student,
    get_students_for_class,
    init_db,
    save_check_result,
    seed_default_classes,
    upsert_students_for_class,
)
from src.parsers import read_class_list, read_report_file


DEFAULT_CLASSES = {
    "Qingpu Campus": [
        "Nursery Pink",
        "Nursery Aqua",
        "Nursery Purple",
        "Nursery Red",
        "Kindergarten Red",
        "Kindergarten Purple",
        "Reception Red",
        "Reception Purple",
        "Year One Red",
        "Year One Purple",
    ],
    "Hongmei Campus": [
        "Nursery Blue",
        "Nursery Green",
        "Nursery Yellow",
        "Nursery Orange",
        "Nursery Rainbow",
        "Kindergarten Blue",
        "Kindergarten Green",
        "Kindergarten Orange",
        "Reception Yellow",
        "Reception Blue",
        "Reception Green",
        "Montessori Blue",
        "Montessori Green",
        "Year One Yellow",
        "Year One Blue",
        "Year One Green",
    ],
}


def pretty_status(status: str) -> str:
    mapping = {
        "report_not_submitted": "Report not submitted",
        "issues_found": "Issues found",
        "human_review_needed": "Needs human review",
        "ok": "OK",
    }
    return mapping.get(status, status)


def build_combined_summary(results) -> str:
    blocks = []

    for result in results:
        child_name = result.matched_child_name or result.detected_report_name or result.report_file
        lines = [child_name]

        if not result.issues:
            lines.append("No issues found.")
            blocks.append("\n".join(lines))
            continue

        lines.append(f"{len(result.issues)} issue(s) found:")

        for issue in result.issues:
            where = issue.location_hint or issue.suggested_check
            readable_type = issue.issue_type.replace("_", " ")
            lines.append(f"- {readable_type.capitalize()}: {issue.explanation} ({where})")

        blocks.append("\n".join(lines))

    return "\n\n---\n\n".join(blocks)


def set_flash(message: str, level: str = "success") -> None:
    st.session_state.flash_message = message
    st.session_state.flash_level = level


def render_flash() -> None:
    message = st.session_state.pop("flash_message", None)
    level = st.session_state.pop("flash_level", "success")

    if not message:
        return

    if level == "success":
        st.success(message)
    elif level == "error":
        st.error(message)
    elif level == "warning":
        st.warning(message)
    else:
        st.info(message)


def render_copy_button(text: str) -> None:
    button_id = f"copy_{uuid.uuid4().hex}"
    escaped = html.escape(text)

    components.html(
        f"""
        <div>
            <textarea id="{button_id}_text" style="display:none;">{escaped}</textarea>
            <button
                onclick="
                    navigator.clipboard.writeText(document.getElementById('{button_id}_text').value).then(() => {{
                        const msg = document.getElementById('{button_id}_msg');
                        msg.innerText = 'Copied to clipboard';
                        setTimeout(() => msg.innerText = '', 2000);
                    }});
                "
                style="
                    padding: 8px 14px;
                    border: none;
                    border-radius: 8px;
                    background: #2e7d32;
                    color: white;
                    cursor: pointer;
                    font-size: 14px;
                "
            >
                Copy to Clipboard
            </button>
            <span id="{button_id}_msg" style="margin-left: 12px; color: #8bc34a; font-size: 14px;"></span>
        </div>
        """,
        height=50,
    )


def reset_check_reports_view() -> None:
    st.session_state.latest_check_summary = ""
    st.session_state.latest_check_class_name = ""
    st.session_state.report_uploader_version += 1


init_db()
seed_default_classes(DEFAULT_CLASSES)

if "selected_campus" not in st.session_state:
    st.session_state.selected_campus = "Qingpu Campus"

if "latest_check_summary" not in st.session_state:
    st.session_state.latest_check_summary = ""

if "latest_check_class_name" not in st.session_state:
    st.session_state.latest_check_class_name = ""

if "report_uploader_version" not in st.session_state:
    st.session_state.report_uploader_version = 0

st.set_page_config(page_title="School Report Checker", layout="wide")
st.title("School Report Checker")
st.caption("Rule-based school report checker with a simple campus/class workflow.")

render_flash()

tab1, tab2, tab3 = st.tabs(["Check Reports", "Class Details", "Admin"])

with tab1:
    st.subheader("Check reports")

    c1, c2 = st.columns(2)
    with c1:
        if st.button("Qingpu Campus", use_container_width=True, key="check_qingpu"):
            st.session_state.selected_campus = "Qingpu Campus"
            st.rerun()
    with c2:
        if st.button("Hongmei Campus", use_container_width=True, key="check_hongmei"):
            st.session_state.selected_campus = "Hongmei Campus"
            st.rerun()

    st.write(f"**Selected campus:** {st.session_state.selected_campus}")

    campus_classes = get_classes_by_campus(st.session_state.selected_campus)

    if not campus_classes:
        st.info("No classes available for this campus yet.")
    else:
        class_options = {row["class_name"]: row["id"] for row in campus_classes}

        selected_class_name = st.selectbox(
            "Select class",
            list(class_options.keys()),
            key="check_class_select",
        )
        selected_class_id = class_options[selected_class_name]

        report_files = st.file_uploader(
            "Upload reports",
            type=["doc", "docx", "txt"],
            accept_multiple_files=True,
            key=f"report_uploads_{st.session_state.report_uploader_version}",
        )

        check_col, next_col = st.columns([2, 1])

        with check_col:
            if st.button("Check reports", type="primary", key="check_reports_button"):
                class_df = get_class_list_df(selected_class_id)

                if class_df.empty:
                    st.error("This class has no class list loaded yet. Please upload the class list in Admin.")
                    st.stop()

                if not report_files:
                    st.error("Please upload at least one report.")
                    st.stop()

                results = []
                read_errors = []

                with st.spinner("Checking reports and updating the class summary..."):
                    for uploaded_file in report_files:
                        try:
                            report_text = read_report_file(uploaded_file)
                            result = check_report(uploaded_file.name, report_text, class_df)
                            save_check_result(selected_class_id, result)
                            results.append(result)
                        except Exception as e:
                            read_errors.append((uploaded_file.name, str(e)))

                if read_errors:
                    combined_errors = "\n".join(f"{file_name}: {error_text}" for file_name, error_text in read_errors)
                    set_flash(f"Some files could not be processed:\n{combined_errors}", "warning")
                elif results:
                    st.session_state.latest_check_summary = build_combined_summary(results)
                    st.session_state.latest_check_class_name = selected_class_name
                    set_flash("Reports checked successfully and class summary updated.", "success")
                    st.session_state.report_uploader_version += 1
                else:
                    set_flash("No reports were successfully processed.", "warning")

                st.rerun()

        with next_col:
            if st.button("Check next class", key="check_next_class_button"):
                reset_check_reports_view()
                set_flash("Ready for the next class.", "success")
                st.rerun()

    if st.session_state.latest_check_summary:
        st.markdown("### Latest checked reports summary")
        st.caption(f"Most recent checked batch: {st.session_state.latest_check_class_name}")
        st.text_area(
            "Summary",
            value=st.session_state.latest_check_summary,
            height=320,
            key="latest_summary_display",
        )
        render_copy_button(st.session_state.latest_check_summary)

with tab2:
    st.subheader("Class details")

    c1, c2 = st.columns(2)
    with c1:
        if st.button("Qingpu Campus", key="details_qingpu", use_container_width=True):
            st.session_state.selected_campus = "Qingpu Campus"
            st.rerun()
    with c2:
        if st.button("Hongmei Campus", key="details_hongmei", use_container_width=True):
            st.session_state.selected_campus = "Hongmei Campus"
            st.rerun()

    st.write(f"**Selected campus:** {st.session_state.selected_campus}")

    campus_classes = get_classes_by_campus(st.session_state.selected_campus)

    if not campus_classes:
        st.info("No classes available for this campus yet.")
    else:
        class_options = {row["class_name"]: row["id"] for row in campus_classes}

        selected_class_name = st.selectbox(
            "Select class",
            list(class_options.keys()),
            key="details_class_select",
        )
        selected_class_id = class_options[selected_class_name]
        selected_class = get_class_by_id(selected_class_id)
        students = get_students_for_class(selected_class_id)

        total_students = len(students)
        ok_count = sum(1 for s in students if s["current_status"] == "ok")
        issues_found = sum(1 for s in students if s["current_status"] == "issues_found")
        human_review = sum(1 for s in students if s["current_status"] == "human_review_needed")
        not_submitted = sum(1 for s in students if s["current_status"] == "report_not_submitted")

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Students", total_students)
        m2.metric("OK", ok_count)
        m3.metric("Issues", issues_found + human_review)
        m4.metric("Not submitted", not_submitted)

        st.markdown("### Class overview")

        overview_rows = []
        for student in students:
            latest_check = get_latest_check_for_student(selected_class_id, student["id"])
            display_name = student["report_name"] or student["full_name"]

            if latest_check is None:
                short_summary = "No report checked yet"
            else:
                try:
                    issues = json.loads(latest_check["issues_json"]) if latest_check["issues_json"] else []
                except Exception:
                    issues = []

                if not issues:
                    short_summary = "No issues found"
                else:
                    parts = []
                    for issue in issues[:3]:
                        issue_name = str(issue.get("issue_type", "")).replace("_", " ")
                        location = issue.get("location_hint") or issue.get("suggested_check") or "check report"
                        parts.append(f"{issue_name} ({location})")
                    short_summary = "; ".join(parts)

            overview_rows.append(
                {
                    "Report Name": display_name,
                    "Full Name": student["full_name"],
                    "Status": pretty_status(student["current_status"]),
                    "Summary": short_summary,
                }
            )

        if overview_rows:
            st.dataframe(pd.DataFrame(overview_rows), use_container_width=True)
        else:
            st.info("No students loaded for this class yet.")

        st.markdown("### Current class summary")
        st.text_area(
            "Summary",
            value=selected_class["current_summary"] or "",
            height=220,
            label_visibility="collapsed",
        )

        with st.expander("Student details", expanded=False):
            if not students:
                st.info("No students loaded for this class yet.")
            else:
                for student in students:
                    latest_check = get_latest_check_for_student(selected_class_id, student["id"])
                    display_name = student["report_name"] or student["full_name"]
                    status_label = pretty_status(student["current_status"])

                    with st.expander(f"{display_name} — {status_label}", expanded=False):
                        if latest_check is None:
                            st.write("No report has been checked for this student yet.")
                            continue

                        try:
                            issues = json.loads(latest_check["issues_json"]) if latest_check["issues_json"] else []
                        except Exception:
                            issues = []

                        if not issues:
                            st.success("No issues found in the latest checked report.")
                        else:
                            for idx, issue in enumerate(issues, start=1):
                                issue_name = str(issue.get("issue_type", "")).replace("_", " ").capitalize()
                                st.markdown(f"**Issue {idx}: {issue_name}**")
                                st.write(f"**Where to look:** {issue.get('location_hint') or issue.get('suggested_check') or '—'}")
                                st.write(f"**Explanation:** {issue.get('explanation', '')}")
                                snippet = issue.get("snippet", "")
                                if snippet:
                                    st.code(snippet)

with tab3:
    st.subheader("Admin")

    with st.expander("Upload or update class list", expanded=True):
        admin_classes = []
        for campus_name in DEFAULT_CLASSES.keys():
            admin_classes.extend(get_classes_by_campus(campus_name))

        class_options = {
            f"{row['campus']} — {row['class_name']}": row["id"]
            for row in admin_classes
        }

        selected_admin_label = st.selectbox(
            "Select class for class list upload",
            list(class_options.keys()),
            key="admin_class_upload_select",
        )
        selected_admin_class_id = class_options[selected_admin_label]

        class_list_file = st.file_uploader(
            "Upload class list",
            type=["csv", "xlsx"],
            key="admin_class_list_upload",
        )

        if st.button("Save class list", key="save_class_list_button"):
            if class_list_file is None:
                st.error("Please upload a class list.")
            else:
                class_df = read_class_list(class_list_file)
                upsert_students_for_class(selected_admin_class_id, class_df)
                set_flash("Class list added successfully.")
                st.rerun()

    with st.expander("Create test class", expanded=False):
        new_test_class_name = st.text_input("Test class name")
        test_campus = st.selectbox(
            "Campus for test class",
            ["Qingpu Campus", "Hongmei Campus", "Test / Other"],
            key="test_campus_select",
        )

        if st.button("Create test class", key="create_test_class_button"):
            if not new_test_class_name.strip():
                st.error("Please enter a class name.")
            else:
                create_class(new_test_class_name.strip(), test_campus)
                set_flash("Class created successfully.")
                st.rerun()

    with st.expander("Reset or delete class", expanded=False):
        all_admin_classes = []
        for campus_name in ["Qingpu Campus", "Hongmei Campus", "Test / Other", "Unassigned"]:
            all_admin_classes.extend(get_classes_by_campus(campus_name))

        delete_options = {
            f"{row['campus']} — {row['class_name']}": row["id"]
            for row in all_admin_classes
        }

        selected_delete_label = st.selectbox(
            "Select class",
            list(delete_options.keys()),
            key="admin_delete_class_select",
        )
        selected_delete_class_id = delete_options[selected_delete_label]

        confirm_delete = st.checkbox("Confirm delete class", key="confirm_delete_class")
        if st.button("Delete class", key="delete_class_button"):
            if not confirm_delete:
                st.error("Please tick confirm first.")
            else:
                delete_class(selected_delete_class_id)
                set_flash("Class deleted successfully.")
                st.rerun()

        confirm_clear = st.checkbox("Confirm clear report history", key="confirm_clear_history")
        if st.button("Clear report history", key="clear_history_button"):
            if not confirm_clear:
                st.error("Please tick confirm first.")
            else:
                clear_class_checks(selected_delete_class_id)
                set_flash("Class report history cleared successfully.")
                st.rerun()