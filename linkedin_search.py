"""Search LinkedIn jobs and run Easy Apply on the results using Gemini for the form answers."""

import argparse
import json
import os
import re
import sys
import time
import urllib.parse

from playwright.sync_api import sync_playwright

from easy_apply import apply_to_current_job, dismiss_modal
from gemini_client import Gemini, GeminiError
from qa_store import QAStore
from resume_profile import get_or_build_profile

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/128.0.0.0 Safari/537.36"
)

STEALTH_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
window.chrome = { runtime: {} };
"""

# `/jobs/search/` renders the classic list: every card is an <li> carrying
# data-occludable-job-id, but only the ones near the viewport are hydrated,
# so each must be scrolled into view before its contents exist.
JOB_LIST_ITEM_SELECTORS = [
    "li[data-occludable-job-id]",
    "li.scaffold-layout__list-item",
    "div.job-card-container",
    "div[data-job-id]",
]

JOB_LIST_CONTAINER_SELECTORS = [
    "div.scaffold-layout__list > ul",
    "ul.scaffold-layout__list-container",
    ".jobs-search-results-list",
]

APPLICANT_COUNT_SELECTORS = [
    ".job-details-jobs-unified-top-card__primary-description-container",
    ".jobs-unified-top-card__applicant-count",
    ".job-details-jobs-unified-top-card__tertiary-description-container",
]

JOB_TITLE_SELECTORS = [
    ".job-details-jobs-unified-top-card__job-title",
    ".jobs-unified-top-card__job-title",
    "h1.t-24",
    "h1",
]

JOB_COMPANY_SELECTORS = [
    ".job-details-jobs-unified-top-card__company-name",
    ".jobs-unified-top-card__company-name",
]


def load_config(path="config.json"):
    if not os.path.exists(path):
        print(f"{path} not found. Create it first.")
        sys.exit(1)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def first_visible(scope, selectors, limit=10):
    for sel in selectors:
        locs = scope.locator(sel)
        try:
            count = locs.count()
        except Exception:
            continue
        for i in range(min(count, limit)):
            loc = locs.nth(i)
            try:
                if loc.is_visible():
                    return loc
            except Exception:
                continue
    return None


def text_of(page, selectors):
    loc = first_visible(page, selectors, limit=3)
    if not loc:
        return ""
    try:
        return (loc.inner_text() or "").strip()
    except Exception:
        return ""


def find_easy_apply_button(page):
    """The details panel uses hashed class names, so match the button by role/name."""
    candidates = [
        page.get_by_role("button", name=re.compile(r"Easy Apply", re.I)),
        page.locator("button.jobs-apply-button"),
        page.locator('button:has-text("Easy Apply")'),
    ]
    for locs in candidates:
        try:
            count = locs.count()
        except Exception:
            continue
        for i in range(min(count, 5)):
            loc = locs.nth(i)
            try:
                if loc.is_visible() and loc.is_enabled():
                    return loc
            except Exception:
                continue
    return None


def click_easy_apply(page, timeout=20000):
    start = time.time()
    while (time.time() - start) * 1000 < timeout:
        loc = find_easy_apply_button(page)
        if loc:
            loc.scroll_into_view_if_needed()
            try:
                loc.click()
            except Exception:
                loc.evaluate("el => el.click()")
            return True
        page.wait_for_timeout(500)
    return False


def search_url(keywords, config):
    """Build a classic /jobs/search/ URL.

    This matters: the global search bar routes to /jobs/search-results/, LinkedIn's
    server-driven-UI page whose cards are hashed `componentkey` divs with no job id
    and no anchor. /jobs/search/ still serves the classic, automatable DOM.
    """
    params = {"keywords": keywords, "f_AL": "true"}
    if config.get("past_24_hours", True):
        params["f_TPR"] = "r86400"
    if config.get("location"):
        params["location"] = config["location"]
    return "https://www.linkedin.com/jobs/search/?" + urllib.parse.urlencode(params)


def build_keywords_with_companies(role, target_companies, max_companies_per_query=10):
    """Build keywords query with role and batched target companies using OR logic.

    LinkedIn supports boolean OR in keywords. We batch companies to avoid URL length limits.
    Returns a list of keyword strings, one per batch.
    """
    if not target_companies:
        return [role]

    # Escape special chars for LinkedIn search
    escaped_companies = [c.replace('"', '\\"') for c in target_companies]
    batches = []
    for i in range(0, len(escaped_companies), max_companies_per_query):
        batch = escaped_companies[i:i + max_companies_per_query]
        companies_or = " OR ".join(f'"{c}"' for c in batch)
        # Format: (role) AND (company1 OR company2 OR ...)
        keywords = f"({role}) AND ({companies_or})"
        batches.append(keywords)
    return batches


def open_jobs_search(page, keywords, config):
    url = search_url(keywords, config)
    print(f"Opening: {url}")
    page.goto(url)
    try:
        page.wait_for_load_state("networkidle", timeout=20000)
    except Exception:
        pass
    page.wait_for_timeout(config.get("page_load_wait_ms", 3000))

    if "/jobs/search-results/" in page.url:
        print("LinkedIn redirected to the SDUI results page; forcing the classic list.")
        page.goto(url)
        page.wait_for_timeout(config.get("page_load_wait_ms", 3000))

    print(f"Jobs URL: {page.url}")


def hydrate_cards(page, passes=4):
    """Scroll the results list so occluded cards render, then report the count."""
    container = first_visible(page, JOB_LIST_CONTAINER_SELECTORS, limit=2)
    last = 0
    for _ in range(passes):
        cards = job_cards(page)
        count = cards.count() if cards else 0
        if count and count == last:
            break
        last = count
        if container:
            try:
                container.evaluate("el => el.scrollTo(0, el.scrollHeight)")
            except Exception:
                page.mouse.wheel(0, 1500)
        else:
            page.mouse.wheel(0, 1500)
        page.wait_for_timeout(1200)

    if container:
        try:
            container.evaluate("el => el.scrollTo(0, 0)")
        except Exception:
            pass
    page.wait_for_timeout(800)
    cards = job_cards(page)
    return cards.count() if cards else 0


def job_cards(page):
    for sel in JOB_LIST_ITEM_SELECTORS:
        locs = page.locator(sel)
        try:
            if locs.count() > 0:
                return locs
        except Exception:
            continue
    return None


def applicant_count(page):
    """Parse the '36 applicants' figure from the details panel, if shown."""
    for sel in APPLICANT_COUNT_SELECTORS:
        loc = page.locator(sel)
        try:
            for i in range(min(loc.count(), 3)):
                text = (loc.nth(i).inner_text() or "")
                match = re.search(r"([\d,]+)\s+applicant", text, re.I)
                if match:
                    return int(match.group(1).replace(",", ""))
        except Exception:
            continue
    return None


def job_context(page):
    return {
        "title": text_of(page, JOB_TITLE_SELECTORS),
        "company": text_of(page, JOB_COMPANY_SELECTORS),
        "url": page.url,
    }


def is_target_company(company_name, target_companies):
    """Check if the job's company matches any target company (case-insensitive, partial match)."""
    if not company_name or not target_companies:
        return False
    company_lower = company_name.lower()
    for target in target_companies:
        if target.lower() in company_lower or company_lower in target.lower():
            return True
    return False


def already_applied(page):
    for sel in [
        '.jobs-s-apply span:has-text("Applied")',
        '.artdeco-inline-feedback:has-text("Applied")',
        'span:has-text("Application submitted")',
    ]:
        loc = page.locator(sel).first
        try:
            if loc.count() and loc.is_visible():
                return True
        except Exception:
            continue
    return False


def run(config):
    # LinkedIn's keyword matcher treats the whole template as literal terms, which
    # buries good results. The "recent" and "Easy Apply" parts are already handled
    # by the f_TPR/f_AL URL filters, and the applicant cap is enforced below from
    # the details panel, so search on the role alone by default.
    if config.get("keywords_mode", "role") == "template":
        base_keywords = config["query_template"].format(
            role=config["role"], applicants=config["applicants"]
        )
    else:
        base_keywords = config["role"]

    max_applicants = int(config.get("applicants") or 0) or None
    max_applications = int(config.get("max_applications", 5))
    resume_path = config.get("resume_path")
    if not resume_path:
        print("Set 'resume_path' in config.json to your resume PDF.")
        sys.exit(1)

    gemini = Gemini(config)
    profile = get_or_build_profile(resume_path, gemini=gemini)
    store = QAStore()
    print(f"Loaded {len(store)} cached question/answer pair(s).")

    if not os.path.exists("linkedin_state.json"):
        print("linkedin_state.json not found. Run linkedin_login.py first.")
        sys.exit(1)

    # Build keyword batches: if using target companies, split into batches to avoid URL limits
    use_target = config.get("use_target_companies", False)
    target_companies = config.get("target_companies", []) if use_target else []
    keyword_batches = build_keywords_with_companies(base_keywords, target_companies) if use_target else [base_keywords]

    with sync_playwright() as p:
        launch_kwargs = {
            "headless": bool(config.get("headless", False)),
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--disable-features=IsolateOrigins,site-per-process",
                "--disable-infobars",
                "--no-first-run",
                "--no-default-browser-check",
            ],
        }
        try:
            browser = p.chromium.launch(channel="chrome", **launch_kwargs)
        except Exception:
            browser = p.chromium.launch(**launch_kwargs)

        context = browser.new_context(
            storage_state="linkedin_state.json",
            user_agent=USER_AGENT,
            viewport={"width": 1440, "height": 900},
        )
        context.add_init_script(STEALTH_SCRIPT)
        page = context.new_page()

        applied = 0
        skipped = 0
        total_processed = 0

        for batch_idx, keywords in enumerate(keyword_batches):
            if applied >= max_applications:
                break

            print(f"\n=== Search batch {batch_idx + 1}/{len(keyword_batches)} ===")
            open_jobs_search(page, keywords, config)

            total = hydrate_cards(page)
            print(f"{total} job card(s) on this page.")
            if not total:
                page.screenshot(path=f"error_no_cards_batch{batch_idx}.png", full_page=True)
                print("No cards found. Saved screenshot; run dump_page.py to inspect the DOM.")
                continue

            for i in range(total):
                if applied >= max_applications:
                    break

                cards = job_cards(page)
                if not cards or i >= cards.count():
                    break

                card = cards.nth(i)
                try:
                    card.scroll_into_view_if_needed()
                    page.wait_for_timeout(500)
                    card.click()
                except Exception as e:
                    print(f"[{total_processed + i + 1}] could not open card: {e}")
                    continue

                page.wait_for_timeout(2500)
                ctx = job_context(page)
                count = applicant_count(page)
                label = f"{count} applicants" if count is not None else "applicant count unknown"
                print(f"\n[{total_processed + i + 1}/{total_processed + total}] {ctx['title']} @ {ctx['company']} ({label})")

                # When using target companies in search, we still verify (defense in depth)
                if use_target and target_companies and not is_target_company(ctx['company'], target_companies):
                    print(f"  Company not in target list; skipping.")
                    skipped += 1
                    continue

                if max_applicants and count is not None and count > max_applicants:
                    print(f"  Over the {max_applicants}-applicant cap; skipping.")
                    skipped += 1
                    continue

                if already_applied(page):
                    print("  Already applied; skipping.")
                    skipped += 1
                    continue

                if not click_easy_apply(page, timeout=8000):
                    print("  No Easy Apply button; skipping.")
                    skipped += 1
                    continue

                try:
                    sent = apply_to_current_job(page, gemini, profile, ctx, store=store)
                except GeminiError:
                    raise
                except Exception as e:
                    print(f"  [error] {e}")
                    page.screenshot(path=f"error_apply_{total_processed + i + 1}.png")
                    sent = False

                if sent:
                    applied += 1
                    print(f"  Applied ({applied}/{max_applications}).")
                else:
                    skipped += 1
                    print("  Not submitted; moving on.")
                    dismiss_modal(page)

                page.wait_for_timeout(2000)

            total_processed += total

        print(f"\nDone. Applied to {applied} job(s), skipped {skipped}.")

        print(f"\nDone. Applied to {applied} job(s), skipped {skipped}.")
        context.close()
        browser.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("role", nargs="?", help="Override config.json role")
    parser.add_argument("applicants", nargs="?", help="Override config.json applicants")
    parser.add_argument("--max", type=int, help="Override max_applications")
    args = parser.parse_args()

    config = load_config()
    if args.role:
        config["role"] = args.role
    if args.applicants:
        config["applicants"] = args.applicants
    if args.max:
        config["max_applications"] = args.max

    run(config)


if __name__ == "__main__":
    main()
