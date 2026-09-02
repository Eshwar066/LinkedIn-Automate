"""Extract a structured profile from the resume PDF once and cache it as JSON."""

import json
import os

from pypdf import PdfReader

from gemini_client import Gemini

PROFILE_PATH = "resume_profile.json"

EXTRACTION_SYSTEM = (
    "You extract structured facts from resumes for filling out job application forms. "
    "Only use information present in the resume. Use null for anything missing. "
    "Never invent employers, degrees, or contact details."
)

EXTRACTION_PROMPT = """Extract the following fields from this resume and return JSON with exactly these keys:

{{
  "full_name": string|null,
  "first_name": string|null,
  "last_name": string|null,
  "email": string|null,
  "phone": string|null,
  "phone_country_code": string|null,
  "city": string|null,
  "state": string|null,
  "country": string|null,
  "linkedin_url": string|null,
  "github_url": string|null,
  "portfolio_url": string|null,
  "headline": string|null,
  "summary": string|null,
  "total_years_experience": number|null,
  "current_title": string|null,
  "current_company": string|null,
  "highest_degree": string|null,
  "field_of_study": string|null,
  "university": string|null,
  "graduation_year": number|null,
  "skills": [string],
  "skill_years": {{"skill name": number}},
  "certifications": [string],
  "languages": [string],
  "work_authorization": string|null,
  "requires_visa_sponsorship": boolean|null,
  "willing_to_relocate": boolean|null,
  "notice_period_days": number|null,
  "current_ctc": string|null,
  "expected_ctc": string|null
}}

"skill_years" should map each significant skill to your best estimate of years of
hands-on experience, inferred from the dated roles and projects in the resume.

Resume text:
---
{resume_text}
---
"""


def extract_pdf_text(path):
    reader = PdfReader(path)
    parts = [page.extract_text() or "" for page in reader.pages]
    text = "\n".join(parts).strip()
    if not text:
        raise ValueError(f"No text extracted from {path}. Is it a scanned image PDF?")
    return text


def read_resume_text(path):
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        return extract_pdf_text(path)
    if ext in (".txt", ".md"):
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()
    raise ValueError(f"Unsupported resume format '{ext}'. Use PDF or TXT.")


def build_profile(resume_path, gemini=None):
    print(f"Reading resume: {resume_path}")
    resume_text = read_resume_text(resume_path)
    print(f"Extracted {len(resume_text)} characters. Asking Gemini for structured insights...")

    gemini = gemini or Gemini()
    profile = gemini.generate_json(
        EXTRACTION_PROMPT.format(resume_text=resume_text[:60000]),
        system=EXTRACTION_SYSTEM,
    )
    profile["_resume_path"] = os.path.abspath(resume_path)
    profile["_resume_text"] = resume_text[:20000]
    return profile


def save_profile(profile, path=PROFILE_PATH):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(profile, f, indent=2, ensure_ascii=False)
    print(f"Profile saved to {path}")


def load_profile(path=PROFILE_PATH):
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def get_or_build_profile(resume_path, force=False, gemini=None):
    """Return the cached profile, rebuilding it if missing, stale, or forced."""
    profile = load_profile()
    if profile and not force:
        cached = profile.get("_resume_path")
        if cached and os.path.abspath(resume_path) == cached:
            print(f"Using cached resume profile from {PROFILE_PATH}")
            return profile
        print("Cached profile was built from a different resume; rebuilding.")

    if not os.path.exists(resume_path):
        raise FileNotFoundError(f"Resume not found: {resume_path}")

    profile = build_profile(resume_path, gemini=gemini)
    save_profile(profile)
    return profile


def profile_for_prompt(profile):
    """Strip internal keys before sending the profile to the model."""
    return {k: v for k, v in profile.items() if not k.startswith("_")}


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Extract resume insights into resume_profile.json")
    parser.add_argument("resume", nargs="?", help="Path to resume PDF (defaults to config.json resume_path)")
    parser.add_argument("--force", action="store_true", help="Rebuild even if a cached profile exists")
    args = parser.parse_args()

    resume = args.resume
    if not resume:
        with open("config.json", "r", encoding="utf-8") as f:
            resume = json.load(f).get("resume_path")
    if not resume:
        raise SystemExit("Provide a resume path or set 'resume_path' in config.json")

    result = get_or_build_profile(resume, force=args.force)
    print(json.dumps(profile_for_prompt(result), indent=2, ensure_ascii=False))
