"""Persistent cache of application questions and the answers we gave them.

Keyed by a normalised form of the question so trivial wording differences
(punctuation, casing, whitespace) still hit the cache instead of Gemini.
"""

import json
import os
import re

QA_PATH = "qa_cache.json"
MAX_STORED_OPTIONS = 25


def normalise(question):
    q = question.strip().lower()
    q = re.sub(r"\s+", " ", q)
    q = re.sub(r"[^\w\s]", "", q)
    return q


class QAStore:
    def __init__(self, path=QA_PATH):
        self.path = path
        self.data = {}
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                self.data = json.load(f)

    def get(self, question, field_type=None, options=None):
        entry = self.data.get(normalise(question))
        if not entry:
            return None
        # A cached answer is only reusable if it is still one of the offered options.
        if options and entry.get("answer") not in options:
            return None
        if field_type and entry.get("field_type") and entry["field_type"] != field_type:
            return None
        return entry.get("answer")

    def put(self, question, answer, field_type=None, options=None):
        # Stored options are informational only (get() validates against the live
        # options), so long lists like the 240-entry country-code dropdown are
        # truncated to keep the cache file readable.
        stored = options or None
        if stored and len(stored) > MAX_STORED_OPTIONS:
            stored = stored[:MAX_STORED_OPTIONS] + [f"... +{len(options) - MAX_STORED_OPTIONS} more"]
        self.data[normalise(question)] = {
            "question": question.strip(),
            "answer": answer,
            "field_type": field_type,
            "options": stored,
        }
        self.save()

    def save(self):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2, ensure_ascii=False)

    def __len__(self):
        return len(self.data)
