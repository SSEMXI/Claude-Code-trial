#!/usr/bin/env python3
"""
Job scraper for internships and entry-level positions in Birmingham, AL
and Huntsville, AL requiring Network+ or Security+ certifications.

Usage:
    python job_scraper.py
    python job_scraper.py --save          # save results to CSV
    python job_scraper.py --location bham # only Birmingham
    python job_scraper.py --location hsv  # only Huntsville
"""

import argparse
import csv
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from urllib.parse import urlencode

import requests

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

LOCATIONS = {
    "bham": "Birmingham, AL",
    "hsv": "Huntsville, AL",
}

SEARCH_QUERIES = [
    "Network+ internship",
    "Security+ internship",
    "Network+ entry level IT",
    "Security+ entry level IT",
    "CompTIA Network+ junior",
    "CompTIA Security+ junior",
]

CERT_KEYWORDS = ["network+", "security+", "comptia network", "comptia security"]

LEVEL_KEYWORDS = [
    "internship", "intern", "entry level", "entry-level",
    "junior", "associate", "jr.", "jr ",
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    )
}

RATE_LIMIT_SECONDS = 2.0   # pause between requests — be respectful


# --------------------------------------------------------------------------- #
# Fetching
# --------------------------------------------------------------------------- #

def fetch_indeed_rss(query: str, location: str) -> list[dict]:
    """Pull jobs from Indeed's RSS feed for a single query + location."""
    params = {"q": query, "l": location, "sort": "date", "limit": 25}
    url = f"https://www.indeed.com/rss?{urlencode(params)}"

    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"  [!] Request failed ({query} / {location}): {e}")
        return []

    jobs = []
    try:
        root = ET.fromstring(resp.content)
        channel = root.find("channel")
        if channel is None:
            return []

        for item in channel.findall("item"):
            raw_title = item.findtext("title", "").strip()
            link = item.findtext("link", "").strip()
            description = item.findtext("description", "").strip()
            pub_date = item.findtext("pubDate", "").strip()

            # Indeed RSS title format: "Job Title - Company Name"
            parts = raw_title.rsplit(" - ", 1)
            job_title = parts[0].strip()
            company = parts[1].strip() if len(parts) > 1 else "Unknown"

            jobs.append({
                "title": job_title,
                "company": company,
                "location": location,
                "description": description[:400],
                "url": link,
                "posted": pub_date,
                "source": "Indeed",
            })
    except ET.ParseError as e:
        print(f"  [!] XML parse error: {e}")

    return jobs


# --------------------------------------------------------------------------- #
# Filtering
# --------------------------------------------------------------------------- #

def is_relevant(job: dict) -> bool:
    """Return True if the job mentions a cert AND an entry-level signal."""
    text = f"{job['title']} {job['description']}".lower()
    has_cert = any(kw in text for kw in CERT_KEYWORDS)
    is_entry = any(kw in text for kw in LEVEL_KEYWORDS)
    return has_cert and is_entry


def deduplicate(jobs: list[dict]) -> list[dict]:
    """Drop duplicate listings by URL."""
    seen: set[str] = set()
    unique = []
    for job in jobs:
        if job["url"] not in seen:
            seen.add(job["url"])
            unique.append(job)
    return unique


# --------------------------------------------------------------------------- #
# Output
# --------------------------------------------------------------------------- #

def print_results(jobs: list[dict]) -> None:
    if not jobs:
        print("\nNo matching jobs found. Try again later — listings change daily.")
        return

    divider = "=" * 70
    print(f"\n{divider}")
    print(f"  {len(jobs)} matching job(s) found")
    print(f"{divider}\n")

    for i, job in enumerate(jobs, 1):
        print(f"[{i}] {job['title']}")
        print(f"    Company  : {job['company']}")
        print(f"    Location : {job['location']}")
        print(f"    Posted   : {job['posted']}")
        print(f"    URL      : {job['url']}")
        print()


def save_to_csv(jobs: list[dict]) -> str:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"jobs_{timestamp}.csv"
    fields = ["title", "company", "location", "posted", "url", "description", "source"]
    with open(filename, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(jobs)
    return filename


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #

def main() -> None:
    parser = argparse.ArgumentParser(description="Job scraper for Network+/Security+ roles in AL")
    parser.add_argument(
        "--location",
        choices=["bham", "hsv"],
        default=None,
        help="Limit search to one city (bham = Birmingham, hsv = Huntsville). Default: both.",
    )
    parser.add_argument("--save", action="store_true", help="Save results to a CSV file.")
    args = parser.parse_args()

    locations = (
        [LOCATIONS[args.location]] if args.location else list(LOCATIONS.values())
    )

    print("Searching for Network+/Security+ internships and entry-level jobs...")
    print(f"Cities : {', '.join(locations)}")
    print(f"Queries: {len(SEARCH_QUERIES)} search terms\n")

    all_jobs: list[dict] = []
    total = len(SEARCH_QUERIES) * len(locations)
    step = 0

    for query in SEARCH_QUERIES:
        for location in locations:
            step += 1
            print(f"[{step}/{total}] '{query}' in {location} ...", end=" ", flush=True)
            jobs = fetch_indeed_rss(query, location)
            relevant = [j for j in jobs if is_relevant(j)]
            all_jobs.extend(relevant)
            print(f"{len(relevant)} relevant")
            time.sleep(RATE_LIMIT_SECONDS)

    unique = deduplicate(all_jobs)
    print_results(unique)

    if args.save and unique:
        filename = save_to_csv(unique)
        print(f"Saved to: {filename}")
    elif args.save and not unique:
        print("Nothing to save.")


if __name__ == "__main__":
    main()
