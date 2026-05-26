#!/usr/bin/env python3
"""
Government tech job scraper for Birmingham, AL and Huntsville, AL.
Targets federal, state, city, and defense-sector IT roles requiring
Network+ or Security+ certifications.

Sources:
  - USAJobs.gov  (federal jobs — free API key required, see below)
  - Indeed RSS   (broader gov/defense tech listings)

USAJobs API key (free):
  1. Register at https://developer.usajobs.gov/
  2. Set the environment variable before running:
       export USAJOBS_API_KEY="your-key-here"
       export USAJOBS_USER_AGENT="your@email.com"
  Without a key the script skips USAJobs and uses Indeed only.

Usage:
    python job_scraper.py
    python job_scraper.py --save            # save results to CSV
    python job_scraper.py --location bham   # Birmingham only
    python job_scraper.py --location hsv    # Huntsville only
    python job_scraper.py --all-tech        # drop cert requirement, show all gov tech
"""

import argparse
import csv
import json
import os
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
    "hsv":  "Huntsville, AL",
}

# USAJobs location codes (used by the federal API)
USAJOBS_LOCATIONS = {
    "bham": "Birmingham, AL",
    "hsv":  "Huntsville, AL",
}

INDEED_QUERIES = [
    "government IT Network+",
    "government IT Security+",
    "federal cybersecurity Network+",
    "federal cybersecurity Security+",
    "DoD information technology Network+",
    "DoD information technology Security+",
    "city government network administrator",
    "state government IT security",
    "defense contractor IT Network+",
    "defense contractor cybersecurity Security+",
]

CERT_KEYWORDS = [
    "network+", "security+", "comptia network", "comptia security",
]

GOV_KEYWORDS = [
    "federal", "government", "govt", "dod", "department of", "agency",
    "nasa", "redstone", "clearance", "public sector", "city of",
    "county", "state of alabama", "defense", "contractor", "ssa",
    "army", "navy", "air force", "corps of engineers",
]

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    )
}

RATE_LIMIT_SECONDS = 2.0


# --------------------------------------------------------------------------- #
# USAJobs.gov (federal jobs API)
# --------------------------------------------------------------------------- #

def fetch_usajobs(location: str, require_certs: bool = True) -> list[dict]:
    """
    Query the USAJobs.gov API for IT/cybersecurity roles in a given city.
    Requires USAJOBS_API_KEY and USAJOBS_USER_AGENT environment variables.
    """
    api_key = os.environ.get("USAJOBS_API_KEY", "")
    user_agent = os.environ.get("USAJOBS_USER_AGENT", "")

    if not api_key or not user_agent:
        return []

    keywords = "Network+ OR Security+ OR cybersecurity OR information technology"
    if not require_certs:
        keywords = "cybersecurity OR information technology OR network administrator OR IT specialist"

    params = {
        "Keyword": keywords,
        "LocationName": location,
        "ResultsPerPage": 50,
        "Fields": "minimum",
    }

    headers = {
        "Host": "data.usajobs.gov",
        "User-Agent": user_agent,
        "Authorization-Key": api_key,
    }

    try:
        resp = requests.get(
            f"https://data.usajobs.gov/api/search?{urlencode(params)}",
            headers=headers,
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, json.JSONDecodeError) as e:
        print(f"  [!] USAJobs error ({location}): {e}")
        return []

    jobs = []
    items = data.get("SearchResult", {}).get("SearchResultItems", [])
    for item in items:
        mv = item.get("MatchedObjectDescriptor", {})
        title = mv.get("PositionTitle", "Unknown")
        org = mv.get("OrganizationName", "Federal Agency")
        pos_locations = mv.get("PositionLocation", [])
        loc_str = pos_locations[0].get("LocationName", location) if pos_locations else location
        url = mv.get("PositionURI", "")
        salary_min = mv.get("PositionRemuneration", [{}])[0].get("MinimumRange", "")
        salary_max = mv.get("PositionRemuneration", [{}])[0].get("MaximumRange", "")
        salary = f"${salary_min}–${salary_max}" if salary_min and salary_max else "See listing"
        posted = mv.get("PublicationStartDate", "")[:10]

        jobs.append({
            "title": title,
            "company": org,
            "location": loc_str,
            "description": f"Salary: {salary}",
            "url": url,
            "posted": posted,
            "source": "USAJobs",
        })

    return jobs


# --------------------------------------------------------------------------- #
# Indeed RSS
# --------------------------------------------------------------------------- #

def fetch_indeed_rss(query: str, location: str) -> list[dict]:
    """Pull jobs from Indeed's RSS feed."""
    params = {"q": query, "l": location, "sort": "date", "limit": 25}
    url = f"https://www.indeed.com/rss?{urlencode(params)}"

    try:
        resp = requests.get(url, headers=BROWSER_HEADERS, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"  [!] Indeed request failed ({query} / {location}): {e}")
        return []

    jobs = []
    try:
        root = ET.fromstring(resp.content)
        channel = root.find("channel")
        if channel is None:
            return []

        for item in channel.findall("item"):
            raw_title = item.findtext("title", "").strip()
            link      = item.findtext("link", "").strip()
            desc      = item.findtext("description", "").strip()
            pub_date  = item.findtext("pubDate", "").strip()

            parts     = raw_title.rsplit(" - ", 1)
            job_title = parts[0].strip()
            company   = parts[1].strip() if len(parts) > 1 else "Unknown"

            jobs.append({
                "title":       job_title,
                "company":     company,
                "location":    location,
                "description": desc[:400],
                "url":         link,
                "posted":      pub_date,
                "source":      "Indeed",
            })
    except ET.ParseError as e:
        print(f"  [!] XML parse error: {e}")

    return jobs


# --------------------------------------------------------------------------- #
# Filtering
# --------------------------------------------------------------------------- #

def is_relevant(job: dict, require_certs: bool) -> bool:
    """
    A job is relevant if it has a government/defense signal.
    Cert check is applied unless --all-tech was passed.
    """
    text = f"{job['title']} {job['company']} {job['description']}".lower()

    is_gov = (
        job["source"] == "USAJobs"          # all USAJobs listings are federal
        or any(kw in text for kw in GOV_KEYWORDS)
    )

    if not require_certs:
        return is_gov

    has_cert = any(kw in text for kw in CERT_KEYWORDS)
    return is_gov and has_cert


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

    by_source: dict[str, list] = {}
    for job in jobs:
        by_source.setdefault(job["source"], []).append(job)

    divider = "=" * 70
    print(f"\n{divider}")
    print(f"  {len(jobs)} government tech job(s) found")
    for src, src_jobs in by_source.items():
        print(f"  {src}: {len(src_jobs)}")
    print(f"{divider}\n")

    for i, job in enumerate(jobs, 1):
        print(f"[{i}] {job['title']}")
        print(f"    Agency/Company : {job['company']}")
        print(f"    Location       : {job['location']}")
        print(f"    Posted         : {job['posted']}")
        print(f"    Source         : {job['source']}")
        print(f"    URL            : {job['url']}")
        print()


def save_to_csv(jobs: list[dict], output_dir: str) -> str:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename  = os.path.join(output_dir, f"gov_jobs_{timestamp}.csv")
    fields    = ["title", "company", "location", "posted", "source", "url", "description"]
    with open(filename, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(jobs)
    return filename


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Government tech job scraper for Birmingham & Huntsville, AL"
    )
    parser.add_argument(
        "--location",
        choices=["bham", "hsv"],
        default=None,
        help="Limit to one city (bham = Birmingham, hsv = Huntsville). Default: both.",
    )
    parser.add_argument(
        "--save",
        action="store_true",
        help="Save results to a CSV in ~/job-scraper/.",
    )
    parser.add_argument(
        "--all-tech",
        action="store_true",
        help="Skip the cert requirement — return all gov tech roles.",
    )
    args = parser.parse_args()

    require_certs = not args.all_tech
    locations     = [LOCATIONS[args.location]] if args.location else list(LOCATIONS.values())
    has_usajobs   = bool(os.environ.get("USAJOBS_API_KEY"))

    cert_note = "Network+/Security+" if require_certs else "all tech roles"
    print(f"Searching government tech jobs ({cert_note})...")
    print(f"Cities  : {', '.join(locations)}")
    print(f"Sources : Indeed RSS{' + USAJobs.gov' if has_usajobs else ' (add USAJOBS_API_KEY for federal listings)'}\n")

    all_jobs: list[dict] = []

    # --- USAJobs (federal) ---
    if has_usajobs:
        for location in locations:
            print(f"[USAJobs] {location} ...", end=" ", flush=True)
            jobs = fetch_usajobs(location, require_certs=require_certs)
            print(f"{len(jobs)} found")
            all_jobs.extend(jobs)
            time.sleep(RATE_LIMIT_SECONDS)

    # --- Indeed RSS ---
    total  = len(INDEED_QUERIES) * len(locations)
    step   = 0
    for query in INDEED_QUERIES:
        for location in locations:
            step += 1
            print(f"[Indeed {step}/{total}] '{query}' in {location} ...", end=" ", flush=True)
            jobs     = fetch_indeed_rss(query, location)
            relevant = [j for j in jobs if is_relevant(j, require_certs)]
            all_jobs.extend(relevant)
            print(f"{len(relevant)} relevant")
            time.sleep(RATE_LIMIT_SECONDS)

    unique = deduplicate(all_jobs)
    print_results(unique)

    if args.save:
        if unique:
            output_dir = os.path.expanduser("~/job-scraper")
            filename   = save_to_csv(unique, output_dir)
            print(f"Saved to: {filename}")
        else:
            print("Nothing to save.")


if __name__ == "__main__":
    main()
