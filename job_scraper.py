#!/usr/bin/env python3
"""
Government tech job scraper for Birmingham, AL and Huntsville, AL.
Sources: USAJobs.gov (federal) + Adzuna (broader gov/defense tech).

Setup (one-time, both free):

  USAJobs.gov — https://developer.usajobs.gov/
    export USAJOBS_API_KEY="your-key-here"
    export USAJOBS_USER_AGENT="your@email.com"

  Adzuna — https://developer.adzuna.com/
    export ADZUNA_APP_ID="your-app-id"
    export ADZUNA_APP_KEY="your-app-key"

  Add those lines to ~/.zprofile then run: source ~/.zprofile

Usage:
    python3 job_scraper.py                   # both cities, both sources
    python3 job_scraper.py --save            # save results to CSV
    python3 job_scraper.py --location bham   # Birmingham only
    python3 job_scraper.py --location hsv    # Huntsville only
"""

import argparse
import csv
import json
import os
import time
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

# Keyword sets shared by both sources
KEYWORD_SETS = [
    "cybersecurity information technology",
    "network administrator IT specialist",
    "information security analyst",
    "network engineer systems administrator",
    "IT support help desk technician",
]

# Adzuna results cover private sector too, so filter for gov/defense signals
GOV_KEYWORDS = [
    "federal", "government", "dept of", "department of", "dod", "agency",
    "nasa", "redstone", "clearance", "public sector", "city of", "county",
    "state of alabama", "defense", "contractor", "army", "navy", "air force",
    "corps of engineers", "va ", "veterans", "ssa", "civil service",
]

RATE_LIMIT_SECONDS = 1.5


# --------------------------------------------------------------------------- #
# USAJobs.gov API
# --------------------------------------------------------------------------- #

def usajobs_headers() -> dict:
    api_key    = os.environ.get("USAJOBS_API_KEY", "")
    user_agent = os.environ.get("USAJOBS_USER_AGENT", "")
    return {
        "Host":              "data.usajobs.gov",
        "User-Agent":        user_agent,
        "Authorization-Key": api_key,
    }


def check_credentials() -> tuple[bool, bool]:
    """
    Returns (has_usajobs, has_adzuna).
    Prints setup instructions for any missing credentials.
    Exits if neither source is configured.
    """
    has_usajobs = bool(os.environ.get("USAJOBS_API_KEY")) and bool(os.environ.get("USAJOBS_USER_AGENT"))
    has_adzuna  = bool(os.environ.get("ADZUNA_APP_ID")) and bool(os.environ.get("ADZUNA_APP_KEY"))

    if not has_usajobs:
        print("  [USAJobs] Not configured — get a free key at https://developer.usajobs.gov/")
        print('    export USAJOBS_API_KEY="your-key-here"')
        print('    export USAJOBS_USER_AGENT="your@email.com"')
    if not has_adzuna:
        print("  [Adzuna]  Not configured — get a free key at https://developer.adzuna.com/")
        print('    export ADZUNA_APP_ID="your-app-id"')
        print('    export ADZUNA_APP_KEY="your-app-key"')
    if not has_usajobs and not has_adzuna:
        print("\nNo sources configured. Add at least one API key to ~/.zprofile then run: source ~/.zprofile")
        return False, False
    if not has_usajobs or not has_adzuna:
        print()

    return has_usajobs, has_adzuna


def fetch_usajobs(keywords: str, location: str) -> list[dict]:
    """Query USAJobs for a keyword set in one city."""
    params = {
        "Keyword":        keywords,
        "LocationName":   location,
        "ResultsPerPage": 50,
        "Fields":         "minimum",
    }
    url = f"https://data.usajobs.gov/api/search?{urlencode(params)}"

    try:
        resp = requests.get(url, headers=usajobs_headers(), timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except requests.HTTPError as e:
        print(f"  [!] HTTP error ({location}): {e}")
        return []
    except (requests.RequestException, json.JSONDecodeError) as e:
        print(f"  [!] Error ({location}): {e}")
        return []

    jobs = []
    items = data.get("SearchResult", {}).get("SearchResultItems", [])
    for item in items:
        mv    = item.get("MatchedObjectDescriptor", {})
        title = mv.get("PositionTitle", "Unknown")
        org   = mv.get("OrganizationName", "Federal Agency")
        locs  = mv.get("PositionLocation", [])
        loc   = locs[0].get("LocationName", location) if locs else location
        url   = mv.get("PositionURI", "")
        posted = mv.get("PublicationStartDate", "")[:10]

        pay = mv.get("PositionRemuneration", [{}])
        lo  = pay[0].get("MinimumRange", "") if pay else ""
        hi  = pay[0].get("MaximumRange", "") if pay else ""
        salary = f"${lo}–${hi}/yr" if lo and hi else "See listing"

        # Pull job summary for cert-keyword filtering
        summary = mv.get("UserArea", {}).get("Details", {}).get("MajorDuties", [""])
        summary_text = " ".join(summary) if isinstance(summary, list) else str(summary)

        jobs.append({
            "title":       title,
            "company":     org,
            "location":    loc,
            "salary":      salary,
            "description": summary_text[:400],
            "url":         url,
            "posted":      posted,
            "source":      "USAJobs",
        })

    return jobs


# --------------------------------------------------------------------------- #
# Adzuna API
# --------------------------------------------------------------------------- #

def fetch_adzuna(keywords: str, location: str) -> list[dict]:
    """Query Adzuna for a keyword set in one city, filtered to gov/defense roles."""
    app_id  = os.environ.get("ADZUNA_APP_ID", "")
    app_key = os.environ.get("ADZUNA_APP_KEY", "")

    # Adzuna uses city name only, not "City, ST" format
    city = location.split(",")[0].strip()

    params = {
        "app_id":           app_id,
        "app_key":          app_key,
        "results_per_page": 50,
        "what":             keywords,
        "where":            city,
        "country":          "us",
        "content-type":     "application/json",
    }
    url = f"https://api.adzuna.com/v1/api/jobs/us/search/1?{urlencode(params)}"

    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except requests.HTTPError as e:
        print(f"  [!] Adzuna HTTP error ({location}): {e}")
        return []
    except (requests.RequestException, json.JSONDecodeError) as e:
        print(f"  [!] Adzuna error ({location}): {e}")
        return []

    jobs = []
    for item in data.get("results", []):
        title       = item.get("title", "Unknown")
        company     = item.get("company", {}).get("display_name", "Unknown")
        description = item.get("description", "")[:400]
        url         = item.get("redirect_url", "")
        posted      = item.get("created", "")[:10]
        salary_min  = item.get("salary_min")
        salary_max  = item.get("salary_max")
        salary      = (
            f"${int(salary_min):,}–${int(salary_max):,}/yr"
            if salary_min and salary_max else "See listing"
        )

        # Filter to government/defense roles only
        text = f"{title} {company} {description}".lower()
        if not any(kw in text for kw in GOV_KEYWORDS):
            continue

        jobs.append({
            "title":       title,
            "company":     company,
            "location":    location,
            "salary":      salary,
            "description": description,
            "url":         url,
            "posted":      posted,
            "source":      "Adzuna",
        })

    return jobs


# --------------------------------------------------------------------------- #
# Filtering
# --------------------------------------------------------------------------- #

def deduplicate(jobs: list[dict]) -> list[dict]:
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
        print("\nNo matching jobs found. Check back tomorrow — listings update daily.")
        return

    divider = "=" * 70
    print(f"\n{divider}")
    print(f"  {len(jobs)} government tech job(s) found")
    print(f"{divider}\n")

    for i, job in enumerate(jobs, 1):
        print(f"[{i}] {job['title']}")
        print(f"    Agency   : {job['company']}")
        print(f"    Location : {job['location']}")
        print(f"    Salary   : {job['salary']}")
        print(f"    Posted   : {job['posted']}")
        print(f"    URL      : {job['url']}")
        print()


def save_to_csv(jobs: list[dict]) -> str:
    output_dir = os.path.expanduser("~/job-scraper")
    timestamp  = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename   = os.path.join(output_dir, f"gov_jobs_{timestamp}.csv")
    fields     = ["title", "company", "location", "salary", "posted", "url", "description"]
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
        description="Government tech job scraper — USAJobs.gov (Birmingham & Huntsville, AL)"
    )
    parser.add_argument(
        "--location", choices=["bham", "hsv"], default=None,
        help="One city only (bham = Birmingham, hsv = Huntsville). Default: both.",
    )
    parser.add_argument(
        "--save", action="store_true",
        help="Save results to a timestamped CSV in ~/job-scraper/.",
    )
    args = parser.parse_args()

    has_usajobs, has_adzuna = check_credentials()
    if not has_usajobs and not has_adzuna:
        return

    locations = [LOCATIONS[args.location]] if args.location else list(LOCATIONS.values())
    sources   = ([" USAJobs"] if has_usajobs else []) + (["Adzuna"] if has_adzuna else [])

    print(f"Searching government tech jobs")
    print(f"Cities  : {', '.join(locations)}")
    print(f"Sources : {', '.join(sources)}\n")

    all_jobs: list[dict] = []
    total = len(KEYWORD_SETS) * len(locations)
    step  = 0

    for keywords in KEYWORD_SETS:
        for location in locations:
            step += 1
            label = f"[{step}/{total}] {location} | '{keywords[:35]}'"
            if has_usajobs:
                print(f"{label} [USAJobs] ", end="", flush=True)
                jobs = fetch_usajobs(keywords, location)
                all_jobs.extend(jobs)
                print(f"{len(jobs)}", end="")
                time.sleep(RATE_LIMIT_SECONDS)
            if has_adzuna:
                print(f"  [Adzuna] ", end="", flush=True)
                jobs = fetch_adzuna(keywords, location)
                all_jobs.extend(jobs)
                print(f"{len(jobs)}", end="")
                time.sleep(RATE_LIMIT_SECONDS)
            print()

    unique = deduplicate(all_jobs)
    print_results(unique)

    if args.save:
        if unique:
            filename = save_to_csv(unique)
            print(f"Saved to: {filename}")
        else:
            print("Nothing to save.")


if __name__ == "__main__":
    main()
