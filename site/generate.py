#!/usr/bin/env python3
"""Fills site/template.html with live build metadata and writes dist/index.html.

Reads job outputs from the calling workflow via env vars. For any plugin whose
build didn't succeed (or whose outputs are missing), falls back to the most
recent matching release from the GitHub API so the page always shows a real,
downloadable jar.
"""
import json
import os
import urllib.request
from datetime import datetime, timezone

REPO = os.environ.get("REPO", "Earth1283/prem-build")
TOKEN = os.environ.get("GITHUB_TOKEN", "")
TEMPLATE_PATH = os.environ.get("TEMPLATE_PATH", "site/template.html")
OUT_DIR = os.environ.get("OUT_DIR", "dist")

PLUGINS = {
    "MCMMO": {"tag_prefix": "mcMMO-build"},
    "IRIS": {"tag_prefix": "iris-build"},
    "ECO": {"tag_prefix": "auto-build"},
    "COREPROTECT": {"tag_prefix": "coreprotect-build"},
    "NASCRAFT": {"tag_prefix": "nascraft-build"},
}


def human_size(num_bytes):
    try:
        n = float(num_bytes)
    except (TypeError, ValueError):
        return "—"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} {unit}"
        n /= 1024
    return "—"


def human_date(iso_or_none):
    if not iso_or_none:
        return datetime.now(timezone.utc).strftime("%-d %b %Y")
    try:
        dt = datetime.strptime(iso_or_none, "%Y-%m-%dT%H:%M:%SZ")
        return dt.strftime("%-d %b %Y")
    except ValueError:
        return datetime.now(timezone.utc).strftime("%-d %b %Y")


def fetch_latest_release(tag_prefix):
    """Fallback: ask the GitHub API for the newest release matching a tag prefix.

    This repo's release history has tag numbers that don't always increase
    monotonically with time (workflow re-runs / renames reset run_number in
    the past), so `published_at` on its own isn't a trustworthy recency
    signal. Instead we page through releases, collect every match for this
    prefix, and pick the one with the highest numeric suffix in its tag
    (e.g. mcMMO-build-373 > mcMMO-build-99, auto-build-20260812 > ...-20260811).
    """
    headers = {"Accept": "application/vnd.github+json"}
    if TOKEN:
        headers["Authorization"] = f"Bearer {TOKEN}"

    matches = []
    for page in range(1, 9):  # up to 800 releases
        url = f"https://api.github.com/repos/{REPO}/releases?per_page=100&page={page}"
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                releases = json.load(resp)
        except Exception as exc:  # network / API issues shouldn't break the deploy
            print(f"warning: release lookup failed for {tag_prefix} (page {page}): {exc}")
            break
        if not releases:
            break
        matches.extend(r for r in releases if r.get("tag_name", "").startswith(tag_prefix))

    if not matches:
        return None

    def suffix(release):
        tag = release.get("tag_name", "")
        digits = tag[len(tag_prefix):].lstrip("-")
        try:
            return int(digits)
        except ValueError:
            return -1

    matches.sort(key=suffix, reverse=True)
    latest = matches[0]
    assets = latest.get("assets") or []
    if not assets:
        return None
    asset = assets[0]
    return {
        "tag": latest["tag_name"],
        "jar": asset["name"],
        "url": asset["browser_download_url"],
        "size": asset["size"],
        "date": latest.get("published_at"),
        "release_url": latest.get("html_url", f"https://github.com/{REPO}/releases"),
    }


def resolve_plugin(key):
    result = os.environ.get(f"{key}_RESULT", "")
    tag = os.environ.get(f"{key}_TAG", "")
    jar = os.environ.get(f"{key}_JAR", "")
    url = os.environ.get(f"{key}_URL", "")
    size = os.environ.get(f"{key}_SIZE", "")

    used_fallback = result != "success" or not (tag and jar and url)

    if used_fallback:
        fallback = fetch_latest_release(PLUGINS[key]["tag_prefix"])
        if fallback:
            tag, jar, url, size = fallback["tag"], fallback["jar"], fallback["url"], fallback["size"]
            date_str = human_date(fallback["date"])
            release_url = fallback["release_url"]
        else:
            tag, jar, url = tag or "unavailable", jar or "—", url or "#"
            size = size or 0
            date_str = human_date(None)
            release_url = f"https://github.com/{REPO}/releases"
        status_class = "status--fail" if result == "failure" else "status--unknown"
        status_text = "Failed" if result == "failure" else "Unknown"
    else:
        date_str = human_date(None)
        release_url = f"https://github.com/{REPO}/releases/tag/{tag}"
        status_class = "status--pass"
        status_text = "Passing"

    return {
        "STATUS_CLASS": status_class,
        "STATUS_TEXT": status_text,
        "TAG": tag,
        "JAR": jar,
        "URL": url,
        "SIZE": human_size(size),
        "DATE": date_str,
        "RELEASE_URL": release_url,
    }


def main():
    with open(TEMPLATE_PATH, "r", encoding="utf-8") as f:
        html = f.read()

    replacements = {
        "REPO_URL": f"https://github.com/{REPO}",
        "YEAR": str(datetime.now(timezone.utc).year),
        "DEPLOY_TIME": datetime.now(timezone.utc).strftime("%-d %b %Y, %H:%M UTC"),
    }

    for key in PLUGINS:
        data = resolve_plugin(key)
        for field, value in data.items():
            replacements[f"{key}_{field}"] = str(value)

    for placeholder, value in replacements.items():
        html = html.replace("{{" + placeholder + "}}", value)

    os.makedirs(OUT_DIR, exist_ok=True)
    with open(os.path.join(OUT_DIR, "index.html"), "w", encoding="utf-8") as f:
        f.write(html)

    with open(os.path.join(OUT_DIR, ".nojekyll"), "w", encoding="utf-8") as f:
        f.write("")

    redirect_html = """<!doctype html>
<html><head>
<meta charset="utf-8">
<meta http-equiv="refresh" content="0; url=/prem-build/">
<link rel="canonical" href="/prem-build/">
<script>location.replace('/prem-build/');</script>
<title>Redirecting…</title>
</head><body>Redirecting to <a href="/prem-build/">/prem-build/</a>…</body></html>
"""
    with open(os.path.join(OUT_DIR, "404.html"), "w", encoding="utf-8") as f:
        f.write(redirect_html)

    print(f"Wrote {OUT_DIR}/index.html, 404.html, .nojekyll")


if __name__ == "__main__":
    main()
