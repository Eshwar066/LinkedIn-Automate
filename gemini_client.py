"""Thin wrapper around the Gemini API used for resume parsing and answering form questions."""

import json
import os
import re

from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()

DEFAULT_MODEL = "gemini-2.5-flash"


class GeminiError(RuntimeError):
    pass


class Gemini:
    def __init__(self, model=None):
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise GeminiError("GEMINI_API_KEY not set. Copy .env.example to .env and add your key.")
        self.client = genai.Client(api_key=api_key)
        self.model = model or os.getenv("GEMINI_MODEL") or DEFAULT_MODEL

    def generate(self, prompt, system=None, temperature=0.2):
        config = types.GenerateContentConfig(temperature=temperature)
        if system:
            config.system_instruction = system
        response = self.client.models.generate_content(
            model=self.model,
            contents=prompt,
            config=config,
        )
        return (response.text or "").strip()

    def generate_json(self, prompt, system=None, temperature=0.1):
        config = types.GenerateContentConfig(
            temperature=temperature,
            response_mime_type="application/json",
        )
        if system:
            config.system_instruction = system
        response = self.client.models.generate_content(
            model=self.model,
            contents=prompt,
            config=config,
        )
        text = (response.text or "").strip()
        return _parse_json(text)


def _parse_json(text):
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Model occasionally wraps JSON in a fenced block despite the mime type.
    match = re.search(r"\{.*\}|\[.*\]", text, re.DOTALL)
    if not match:
        raise GeminiError(f"Gemini did not return JSON:\n{text[:500]}")
    return json.loads(match.group(0))
