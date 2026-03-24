"""Resume PDF parsing and structured extraction via Claude."""
import io
import json
from typing import Optional

import pdfplumber
import anthropic

from backend.config import get_settings

settings = get_settings()


def extract_text_from_pdf(file_bytes: bytes) -> str:
    """Extract plain text from a PDF using pdfplumber."""
    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        pages = []
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                pages.append(text)
    return "\n".join(pages)


def structure_resume(raw_text: str) -> dict:
    """Use Claude to parse raw resume text into structured JSON."""
    if not raw_text.strip():
        return {}

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    prompt = f"""Parse the following resume text into structured JSON.

Resume text:
{raw_text[:6000]}

Return a JSON object with exactly these keys:
- "summary": string, a 2-3 sentence professional summary
- "skills": array of strings (technical + soft skills)
- "experience": array of objects with keys: role, company, start, end (strings), bullets (array of strings)
- "education": array of objects with keys: degree, institution, year (string)

Return only valid JSON, no markdown fences."""

    response = client.messages.create(
        model=settings.claude_model,
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}],
    )

    text = response.content[0].text.strip()
    # Strip markdown fences if present
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    text = text.strip().rstrip("```")

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Return minimal structure if parsing fails
        return {"summary": raw_text[:500], "skills": [], "experience": [], "education": []}
