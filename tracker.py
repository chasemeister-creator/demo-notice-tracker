"""Demo 2 -- public notices tracker: fetch -> normalise -> diff -> CSV.

Self-built demo. Not client work. No client data appears here, and no personal
data is collected: the only fields kept are a government document number, its
title, its type, the issuing agency names, its publication date and its public
URL.

Source
------
The Federal Register API (https://www.federalregister.gov/developers/api).
US government public-domain data. No key, no login, no account. The JSON API is
used directly -- no HTML is scraped -- and one run makes exactly one request for
at most 100 results.

Two sources are supported, both on that same API:

``documents``          /api/v1/documents.json          (default; server-side term search)
``public-inspection``  /api/v1/public-inspection-documents/current.json
                                                       (fallback; term filtered here)

The fallback exists because the ``documents`` endpoint answered HTTP 500 to
every request on 2026-09-16 (see README). Same API, same publisher, same
public-domain status, identical field names.

Pipeline
--------
fetch -> normalise -> load previous ``state.json`` -> classify each row as
new / changed / unchanged, plus ``removed`` for anything in the state that the
fetch no longer returns -> write the CSV and the new state -> print one summary
line.

Standard library only: argparse, csv, json, os, sys, urllib. Python 3.9+.

Public API
----------
build_url(term, source=SOURCE_DOCUMENTS, per_page=100)
fetch_rows(term, source=..., per_page=100, fetcher=http_fetch)
parse_payload(body)
normalise(raw)
classify(rows, previous)
build_state(rows)
load_state(path) / save_state(path, state)
write_csv(path, rows)
summarise(rows)

``fetcher`` is an injected callable taking a URL and returning the response body
as text, so the test suite runs against fakes with no network at all.
"""

import argparse
import csv
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

__all__ = [
    "TrackerError",
    "SOURCE_DOCUMENTS",
    "SOURCE_PUBLIC_INSPECTION",
    "CSV_HEADER",
    "build_url",
    "http_fetch",
    "parse_payload",
    "normalise",
    "fetch_rows",
    "classify",
    "build_state",
    "load_state",
    "save_state",
    "write_csv",
    "summarise",
    "run",
    "main",
]

SOURCE_DOCUMENTS = "documents"
SOURCE_PUBLIC_INSPECTION = "public-inspection"

API_ROOT = "https://www.federalregister.gov/api/v1"
ENDPOINTS = {
    SOURCE_DOCUMENTS: API_ROOT + "/documents.json",
    SOURCE_PUBLIC_INSPECTION: API_ROOT + "/public-inspection-documents/current.json",
}

USER_AGENT = "demo2-notice-tracker/1.0 (self-built demo; public-domain data)"

FIELDS = (
    "document_number",
    "title",
    "type",
    "agency_names",
    "publication_date",
    "html_url",
)
CSV_HEADER = list(FIELDS) + ["change"]

MAX_PER_PAGE = 100

NEW = "new"
CHANGED = "changed"
UNCHANGED = "unchanged"
REMOVED = "removed"


class TrackerError(Exception):
    """Anything the tracker can fail on, reported as one clean message."""


def build_url(term, source=SOURCE_DOCUMENTS, per_page=100):
    """Return the request URL. One request per run, at most 100 results."""
    if source not in ENDPOINTS:
        raise TrackerError(
            "unknown source %r (expected one of: %s)"
            % (source, ", ".join(sorted(ENDPOINTS)))
        )
    if per_page < 1 or per_page > MAX_PER_PAGE:
        raise TrackerError("per_page must be between 1 and %d" % MAX_PER_PAGE)

    base = ENDPOINTS[source]
    if source == SOURCE_PUBLIC_INSPECTION:
        # The "current" feed takes no search conditions; it returns today's
        # filings and the term is applied locally in fetch_rows.
        return base

    query = [
        ("conditions[term]", term),
        ("order", "newest"),
        ("per_page", str(per_page)),
    ]
    query.extend(("fields[]", field) for field in FIELDS)
    return base + "?" + urllib.parse.urlencode(query)


def http_fetch(url, timeout=30):
    """Fetch a URL and return the body as text. The only network call here."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        raise TrackerError("source returned HTTP %s for %s" % (exc.code, url))
    except urllib.error.URLError as exc:
        raise TrackerError("could not reach %s: %s" % (url, exc.reason))


def parse_payload(body):
    """Parse a response body into a list of raw result dicts."""
    try:
        payload = json.loads(body)
    except ValueError as exc:
        raise TrackerError("source did not return valid JSON: %s" % exc)
    if not isinstance(payload, dict):
        raise TrackerError(
            "source returned %s, expected a JSON object" % type(payload).__name__
        )
    results = payload.get("results", [])
    if results is None:
        results = []
    if not isinstance(results, list):
        raise TrackerError("'results' was %s, expected a list" % type(results).__name__)
    return results


def _text(value):
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return "; ".join(_text(item) for item in value if item is not None)
    return str(value).strip()


def normalise(raw):
    """Turn one raw API result into a flat row of strings.

    ``document_number`` is the key and must be present; everything else
    defaults to an empty string. ``agency_names`` is a list upstream and is
    joined with "; " so the row stays one line of CSV.
    """
    if not isinstance(raw, dict):
        raise TrackerError("expected a JSON object per result, got %s" % type(raw).__name__)
    row = {field: _text(raw.get(field)) for field in FIELDS}
    # Collapse the runs of whitespace the source leaves in some titles.
    row["title"] = " ".join(row["title"].split())
    if not row["document_number"]:
        raise TrackerError("a result had no document_number; cannot key it")
    return row


def _matches_term(row, term):
    needle = term.strip().lower()
    if not needle:
        return True
    haystack = " ".join(
        (row["title"], row["agency_names"], row["type"])
    ).lower()
    return needle in haystack


def fetch_rows(term, source=SOURCE_DOCUMENTS, per_page=100, fetcher=http_fetch):
    """One request, normalised rows, deduped on document_number (first wins)."""
    body = fetcher(build_url(term, source=source, per_page=per_page))
    rows = [normalise(raw) for raw in parse_payload(body)]
    if source == SOURCE_PUBLIC_INSPECTION:
        rows = [row for row in rows if _matches_term(row, term)]

    deduped = []
    seen = set()
    for row in rows:
        key = row["document_number"]
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    return deduped[:per_page]


def classify(rows, previous):
    """Tag each fetched row new/changed/unchanged; append removed rows.

    A row is ``changed`` when its title or its publication_date differs from
    the stored one. Rows present in ``previous`` but absent from this fetch are
    emitted last, tagged ``removed``, using their stored values.
    """
    previous = previous or {}
    out = []
    for row in rows:
        stored = previous.get(row["document_number"])
        tagged = dict(row)
        if stored is None:
            tagged["change"] = NEW
        elif (
            _text(stored.get("title")) != row["title"]
            or _text(stored.get("publication_date")) != row["publication_date"]
        ):
            tagged["change"] = CHANGED
        else:
            tagged["change"] = UNCHANGED
        out.append(tagged)

    present = {row["document_number"] for row in rows}
    for key, stored in previous.items():
        if key in present:
            continue
        gone = {field: _text(stored.get(field)) for field in FIELDS}
        gone["document_number"] = key
        gone["change"] = REMOVED
        out.append(gone)
    return out


def build_state(rows):
    """State is the fetched rows only -- removed rows drop out next run."""
    return {row["document_number"]: {field: row[field] for field in FIELDS} for row in rows}


def load_state(path):
    """Previous state, or {} on the first ever run."""
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            state = json.load(handle)
    except ValueError as exc:
        raise TrackerError("%s is not valid JSON: %s" % (path, exc))
    except OSError as exc:
        raise TrackerError("could not read %s: %s" % (path, exc))
    if not isinstance(state, dict):
        raise TrackerError("%s must hold a JSON object keyed by document_number" % path)
    return state


def save_state(path, state):
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=1, sort_keys=True)
        handle.write("\n")


def write_csv(path, rows):
    """Write the CSV. Header shape is fixed, newline="" per the csv docs."""
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_HEADER, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in CSV_HEADER})


def summarise(rows):
    counts = {NEW: 0, CHANGED: 0, UNCHANGED: 0, REMOVED: 0}
    for row in rows:
        counts[row["change"]] = counts.get(row["change"], 0) + 1
    return "%d rows: %d new, %d changed, %d unchanged, %d removed" % (
        len(rows),
        counts[NEW],
        counts[CHANGED],
        counts[UNCHANGED],
        counts[REMOVED],
    )


def run(term, source, per_page, csv_path, state_path, fetcher=http_fetch):
    rows = fetch_rows(term, source=source, per_page=per_page, fetcher=fetcher)
    previous = load_state(state_path)
    classified = classify(rows, previous)
    write_csv(csv_path, classified)
    save_state(state_path, build_state(rows))
    return classified


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Demo: track Federal Register notices, tagging new/changed/removed."
    )
    parser.add_argument("--term", default="heat pump", help="search term (default: 'heat pump')")
    parser.add_argument(
        "--source",
        default=SOURCE_DOCUMENTS,
        choices=sorted(ENDPOINTS),
        help="which Federal Register endpoint to read (default: documents)",
    )
    parser.add_argument("--per-page", type=int, default=MAX_PER_PAGE, help="max results, 1-100")
    parser.add_argument("--csv", default="sample_run.csv", help="output CSV path")
    parser.add_argument("--state", default="state.json", help="previous-run state path")
    args = parser.parse_args(argv)

    try:
        rows = run(args.term, args.source, args.per_page, args.csv, args.state)
    except TrackerError as exc:
        sys.stderr.write("tracker failed: %s\n" % exc)
        return 1
    print("%s -> %s" % (summarise(rows), args.csv))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
