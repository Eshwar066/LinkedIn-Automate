"""Easy Apply to a single job, dumping the modal HTML at every wizard step.

Default is a dry run: fields are detected and answered, and the wizard advances
through Next/Review, but Submit is never clicked. Pass --submit to actually send
the application.

  python apply_one.py --url "<job url>"            # dry run, dumps HTML
  python apply_one.py --url "<job url>" --no-fill  # just dump, no Gemini calls
  python apply_one.py --url "<job url>" --submit   # really apply
"""

import argparse
import json
import os
import sys

from playwright.sync_api import sync_playwright

from easy_apply import (
    application_sent,
    click_step_button,
    collect_fields,
    dismiss_modal,
    fill_field,
    find_modal,
    has_errors,
    is_opt_out,
    step_signature,
    uncheck_opt_outs,
    wait_for_modal,
    answer_fields,
)
from gemini_client import Gemini
from linkedin_search import (
    STEALTH_SCRIPT,
    USER_AGENT,
    applicant_count,
    click_easy_apply,
    job_context,
    load_config,
)
from qa_store import QAStore
from resume_profile import get_or_build_profile

DUMP_DIR = "debug_modal"


def dump(modal, page, step, tag=""):
    os.makedirs(DUMP_DIR, exist_ok=True)
    suffix = f"{step:02d}{('-' + tag) if tag else ''}"
    html_path = os.path.join(DUMP_DIR, f"step{suffix}.html")
    png_path = os.path.join(DUMP_DIR, f"step{suffix}.png")
    try:
        html = modal.evaluate("el => el.outerHTML")
    except Exception:
        html = page.content()
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)
    try:
        page.screenshot(path=png_path)
    except Exception:
        pass
    print(f"  dumped -> {html_path} ({len(html)} chars)")
    return html


def describe(fields):
    if not fields:
        print("  (no fields detected on this step)")
        return
    for f in fields:
        opts = f.get("options") or []
        opts_txt = f" options={opts}" if opts else ""
        req = " *required*" if f.get("required") else ""
        print(f"    [{f['kind']}/{f.get('inputType') or '-'}]{req} {f['label']!r}{opts_txt}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True, help="Job URL (with currentJobId)")
    parser.add_argument("--submit", action="store_true", help="Actually submit the application")
    parser.add_argument("--no-fill", action="store_true", help="Only dump HTML; no Gemini, no typing")
    parser.add_argument("--max-steps", type=int, default=12)
    parser.add_argument("--hold", action="store_true", help="Keep the browser open until Enter")
    args = parser.parse_args()

    config = load_config()
    gemini = None
    profile = None
    store = QAStore()
    if not args.no_fill:
        gemini = Gemini(config)
        profile = get_or_build_profile(config["resume_path"], gemini=gemini)

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(channel="chrome", headless=False)
        except Exception:
            browser = p.chromium.launch(headless=False)
        context = browser.new_context(
            storage_state="linkedin_state.json",
            user_agent=USER_AGENT,
            viewport={"width": 1440, "height": 900},
        )
        context.add_init_script(STEALTH_SCRIPT)
        page = context.new_page()

        print(f"Opening: {args.url}")
        page.goto(args.url)
        try:
            page.wait_for_load_state("networkidle", timeout=20000)
        except Exception:
            pass
        page.wait_for_timeout(config.get("page_load_wait_ms", 3000))

        ctx = job_context(page)
        print(f"Job: {ctx['title']!r} @ {ctx['company']!r} | applicants={applicant_count(page)}")
        if not ctx["title"]:
            page.screenshot(path="error_no_job_loaded.png")
            raise SystemExit("Job details did not load; see error_no_job_loaded.png")

        if not click_easy_apply(page, timeout=15000):
            page.screenshot(path="error_no_easy_apply.png")
            raise SystemExit("Easy Apply button not found; see error_no_easy_apply.png")
        print("Clicked Easy Apply.")

        modal = wait_for_modal(page)
        print("Modal open.\n")

        mode = "SUBMIT" if args.submit else ("DUMP-ONLY" if args.no_fill else "DRY RUN")
        print(f"--- mode: {mode} ---\n")

        last_signature = None
        stalls = 0

        for step in range(1, args.max_steps + 1):
            page.wait_for_timeout(1500)
            modal = find_modal(page) or modal

            if application_sent(page):
                print("Application was sent.")
                dump(modal, page, step, "sent")
                dismiss_modal(page)
                break

            print(f"Step {step}:")
            dump(modal, page, step)

            fields = collect_fields(modal)
            print(f"  {len(fields)} field(s) detected:")
            describe(fields)

            signature = step_signature(modal, fields)
            if signature == last_signature:
                stalls += 1
                if stalls >= 2:
                    print(f"  Stuck on this step (error: {has_errors(modal)}); stopping.")
                    dump(modal, page, step, "stalled")
                    break
            else:
                stalls = 0
            last_signature = signature

            if not args.no_fill and fields:
                uncheck_opt_outs(modal, fields)
                answers = answer_fields(gemini, profile, ctx, fields, store)
                for f in fields:
                    if is_opt_out(f["label"]):
                        continue
                    ans = answers.get(f["index"])
                    if ans is None:
                        print(f"  [warn] no answer for {f['label']!r}")
                        continue
                    try:
                        fill_field(modal, f, ans)
                    except Exception as e:
                        print(f"  [warn] fill failed for {f['label']!r}: {e}")
                dump(modal, page, step, "filled")

            buttons = modal.evaluate(
                """(el) => Array.from(el.querySelectorAll('button')).map(b => ({
                    text: (b.innerText || '').trim(),
                    aria: b.getAttribute('aria-label'),
                    disabled: b.disabled,
                }))"""
            )
            print(f"  buttons: {json.dumps(buttons, ensure_ascii=False)}")

            is_submit_step = any(
                (b.get("aria") or "").lower().startswith("submit")
                or (b.get("text") or "").lower().startswith("submit")
                for b in buttons
            )
            if is_submit_step and not args.submit:
                print("\n  Reached the Submit step. Stopping (pass --submit to send).")
                dump(modal, page, step, "submit-step")
                dismiss_modal(page)
                break

            action = click_step_button(modal)
            if action is None:
                print("  No next/review/submit button available; stopping.")
                dump(modal, page, step, "stuck")
                break

            page.wait_for_timeout(2000)
            err = has_errors(find_modal(page) or modal)
            if err:
                print(f"  [validation error] {err}")

            if action == "submit":
                page.wait_for_timeout(config.get("page_load_wait_ms", 3000))
                if application_sent(page) or find_modal(page) is None:
                    print("\nApplication submitted.")
                    dismiss_modal(page)
                    break

        print(f"\nCached Q/A pairs now: {len(store)}")
        if args.hold:
            try:
                input("Press Enter to close the browser...")
            except (EOFError, KeyboardInterrupt):
                pass
        context.close()
        browser.close()


if __name__ == "__main__":
    main()
