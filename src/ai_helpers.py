from __future__ import annotations

import json
import os

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


def build_class_feedback_report_with_ai(
    class_name: str,
    reports: list[dict],
) -> str:
    """
    Generate one teacher-facing feedback report for a whole class/batch.

    `reports` should already contain the structured rule-based issues.
    AI's job is only to turn them into clear natural-language feedback.
    """
    prompt = f"""
You are helping an academic coordinator send correction feedback to teachers.

Write a clear, concise feedback report for the class below.

Requirements:
- Use the provided issue data only
- Do not invent new issues
- Group feedback by child
- For each child, briefly state:
  1. the child's name
  2. how many issues were found
  3. what needs to be corrected
  4. where to look in the report
- Keep the wording practical and professional
- Make it easy for a teacher to fix the report quickly
- Do not use tables
- Do not mention AI
- If a child has no issues, you may omit them from the report
- Use simple headings and bullet points
- Keep it suitable for copying into a document or email

Class name: {class_name}

Checked reports data:
{json.dumps(reports, ensure_ascii=False, indent=2)}

Return plain text only.
"""

    response = client.responses.create(
        model="gpt-5",
        input=prompt,
    )
    return response.output_text.strip()