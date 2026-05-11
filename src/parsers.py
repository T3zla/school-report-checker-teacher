from __future__ import annotations

import re
import subprocess
import tempfile
from io import BytesIO
from pathlib import Path

import pandas as pd
from docx import Document


REQUIRED_COLUMNS = ["Name", "Report Name", "Date of Birth", "Pronoun"]


def normalize_spaces(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def normalize_name(value: str) -> str:
    value = normalize_spaces(value).lower()
    value = re.sub(r"[^a-z\s]", "", value)
    return normalize_spaces(value)


def normalize_pronoun(value: str) -> str:
    value = normalize_spaces(value).lower()
    mapping = {
        "he": "he",
        "him": "he",
        "his": "he",
        "she": "she",
        "her": "she",
        "hers": "she",
        "they": "they",
        "them": "they",
        "their": "they",
    }
    return mapping.get(value, value)


def parse_date_flexible(value) -> pd.Timestamp | pd.NaT:
    if pd.isna(value):
        return pd.NaT
    return pd.to_datetime(value, errors="coerce", dayfirst=True)


def read_class_list(uploaded_file) -> pd.DataFrame:
    file_name = uploaded_file.name.lower()

    if file_name.endswith(".csv"):
        df = pd.read_csv(uploaded_file)
    elif file_name.endswith(".xlsx"):
        df = pd.read_excel(uploaded_file)
    else:
        raise ValueError("Unsupported class list format. Use CSV or XLSX.")

    missing = [col for col in REQUIRED_COLUMNS if col not in df.columns]
    if missing:
        raise ValueError(f"Missing class list columns: {missing}")

    df = df.copy()
    df["Name"] = df["Name"].astype(str).map(normalize_spaces)
    df["Report Name"] = df["Report Name"].astype(str).map(normalize_spaces)
    df["Pronoun"] = df["Pronoun"].astype(str).map(normalize_pronoun)
    df["Date of Birth"] = df["Date of Birth"].map(parse_date_flexible)

    df["name_norm"] = df["Name"].map(normalize_name)
    df["report_name_norm"] = df["Report Name"].map(normalize_name)

    return df


def _read_docx_bytes(file_bytes: bytes) -> str:
    doc = Document(BytesIO(file_bytes))
    return "\n".join(p.text for p in doc.paragraphs)


def _read_doc_bytes_via_textutil(file_bytes: bytes, original_name: str) -> str:
    suffix = Path(original_name).suffix.lower() or ".doc"

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        input_path = tmpdir / f"input{suffix}"
        output_path = tmpdir / "output.txt"

        input_path.write_bytes(file_bytes)

        result = subprocess.run(
            ["textutil", "-convert", "txt", str(input_path), "-output", str(output_path)],
            capture_output=True,
            text=True,
        )

        if result.returncode != 0 or not output_path.exists():
            raise ValueError(
                f"Could not read legacy Word file '{original_name}'. "
                f"textutil failed: {result.stderr.strip()}"
            )

        return output_path.read_text(encoding="utf-8", errors="ignore")


def read_report_file(uploaded_file) -> str:
    file_name = uploaded_file.name.lower()
    file_bytes = uploaded_file.read()

    if file_name.endswith(".txt"):
        return file_bytes.decode("utf-8", errors="ignore")

    if file_name.endswith(".docx"):
        return _read_docx_bytes(file_bytes)

    if file_name.endswith(".doc"):
        return _read_doc_bytes_via_textutil(file_bytes, uploaded_file.name)

    raise ValueError(f"Unsupported report format: {uploaded_file.name}")


def extract_report_header_fields(report_text: str) -> dict:
    student_match = re.search(
        r"Student:\s*(.+?)\s+Date of Birth:",
        report_text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    dob_match = re.search(
        r"Date of Birth:\s*([^\n\r]+)",
        report_text,
        flags=re.IGNORECASE,
    )
    class_match = re.search(
        r"Class:\s*([^\n\r]+)",
        report_text,
        flags=re.IGNORECASE,
    )

    student_raw = normalize_spaces(student_match.group(1)) if student_match else ""
    dob_raw = normalize_spaces(dob_match.group(1)) if dob_match else ""
    class_raw = normalize_spaces(class_match.group(1)) if class_match else ""

    nickname_match = re.search(r"\(([^)]+)\)", student_raw)
    report_name = normalize_spaces(nickname_match.group(1)) if nickname_match else ""

    full_name = normalize_spaces(re.sub(r"\s*\([^)]+\)", "", student_raw))

    return {
        "student_raw": student_raw,
        "full_name_in_report": full_name,
        "report_name_in_report": report_name,
        "dob_in_report_raw": dob_raw,
        "dob_in_report": parse_date_flexible(dob_raw),
        "class_in_report": class_raw,
    }