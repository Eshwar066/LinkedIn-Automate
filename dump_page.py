"""Dump a LinkedIn page's HTML and probe candidate selectors, to debug automation breakage.

Usage:
  python dump_page.py                      # uses config.json query
  python dump_page.py --url "<full url>"   # dump a specific page
"""

import argparse
import json
import os
import urllib.parse

from playwright.sync_api import sync_playwright

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/128.0.0.0 Safari/537.36"
)

CANDIDATES = [
    "li.scaffold-layout__list-item",
    "div[data-job-id]",
    "li[data-occludable-job-id]",
    "div.job-card-container",
    "li.jobs-search-results__list-item",
    "a.job-card-container__link",
    "a[href*='/jobs/view/']",
    "a[href*='currentJobId=']",
    "div[role='button'][componentkey]",
    "div[componentkey]",
    "[role='listitem']",
    "ul[role='list'] > li",
    "main li",
    "button.jobs-apply-button",
    "button:has-text('Easy Apply')",
]

# Finds the repeated DOM shape that makes up the results list: the deepest
# elements whose text contains an "applicant"/"Easy Apply" style job summary.
PROBE_JS = """
() => {
  const out = { componentkeys: [], jobIdAttrs: [], listShapes: [] };

  document.querySelectorAll('[componentkey]').forEach(el => {
    out.componentkeys.push({
      key: el.getAttribute('componentkey'),
      role: el.getAttribute('role'),
      tag: el.tagName.toLowerCase(),
      text: (el.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 80),
    });
  });

  const attrNames = new Set();
  document.querySelectorAll('*').forEach(el => {
    for (const a of el.attributes) {
      if (/job/i.test(a.name)) attrNames.add(a.name);
    }
  });
  out.jobIdAttrs = [...attrNames];

  // Group siblings by parent to spot the results list container.
  const counts = new Map();
  document.querySelectorAll('li, div[role="button"], div[role="listitem"]').forEach(el => {
    const parent = el.parentElement;
    if (!parent) return;
    const sig = parent.tagName.toLowerCase()
      + '.' + (parent.className || '').toString().split(/\\s+/).slice(0, 2).join('.')
      + ' > ' + el.tagName.toLowerCase()
      + (el.getAttribute('role') ? `[role=${el.getAttribute('role')}]` : '');
    counts.set(sig, (counts.get(sig) || 0) + 1);
  });
  out.listShapes = [...counts.entries()]
    .filter(([, n]) => n >= 3)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 15)
    .map(([sig, n]) => ({ shape: sig, count: n }));

  return out;
}
"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", help="Page to dump; defaults to the jobs search for config.json")
    parser.add_argument("--out", default="debug_page.html")
    parser.add_argument("--wait", type=int, default=6000, help="Extra settle time in ms")
    args = parser.parse_args()

    if not os.path.exists("linkedin_state.json"):
        raise SystemExit("linkedin_state.json missing. Run linkedin_login.py first.")

    url = args.url
    if not url:
        with open("config.json", "r", encoding="utf-8") as f:
            cfg = json.load(f)
        query = cfg["query_template"].format(role=cfg["role"], applicants=cfg["applicants"])
        params = {"keywords": query, "f_AL": "true", "f_TPR": "r86400"}
        if cfg.get("location"):
            params["location"] = cfg["location"]
        url = "https://www.linkedin.com/jobs/search/?" + urllib.parse.urlencode(params)

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
        page = context.new_page()
        print(f"Navigating: {url}")
        page.goto(url)
        try:
            page.wait_for_load_state("networkidle", timeout=20000)
        except Exception:
            pass
        page.wait_for_timeout(args.wait)

        # Scroll the list so lazily-rendered cards attach to the DOM.
        for _ in range(3):
            page.mouse.wheel(0, 1200)
            page.wait_for_timeout(800)

        print(f"Final URL: {page.url}\n")

        html = page.content()
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"HTML ({len(html)} chars) -> {args.out}")
        page.screenshot(path=args.out.replace(".html", ".png"), full_page=False)

        print("\n--- candidate selector counts ---")
        for sel in CANDIDATES:
            try:
                print(f"{page.locator(sel).count():5}  {sel}")
            except Exception as e:
                print(f"  err  {sel}  ({e})")

        probe = page.evaluate(PROBE_JS)

        print("\n--- job-ish attributes present ---")
        print(probe["jobIdAttrs"])

        print("\n--- repeated list shapes (count >= 3) ---")
        for item in probe["listShapes"]:
            print(f"{item['count']:5}  {item['shape']}")

        print("\n--- componentkeys (first 40) ---")
        for item in probe["componentkeys"][:40]:
            print(f"  {item['tag']}[role={item['role']}] key={item['key']!r} text={item['text']!r}")

        with open("debug_probe.json", "w", encoding="utf-8") as f:
            json.dump(probe, f, indent=2, ensure_ascii=False)
        print("\nFull probe -> debug_probe.json")

        context.close()
        browser.close()


if __name__ == "__main__":
    main()
