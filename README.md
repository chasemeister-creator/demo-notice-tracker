# Demo -- public notices tracker: scrape-free feed, deduped, with change detection

**Demo**

This is a self-built demo, not client work. No client data, no client
credentials, no scraped personal data, and no secrets of any kind appear in this
directory. It exists so a prospect can see a working pipeline -- fetch, dedupe,
diff, schedule -- before hiring, rather than take it on trust.

## The data source, and why it is safe to use

[The Federal Register API](https://www.federalregister.gov/developers/api),
`https://www.federalregister.gov/api/v1/documents.json`.

- **Public domain.** Federal Register documents are works of the United States
  Government. The Office of the Federal Register publishes this API for reuse
  and asks only that it not be hammered.
- **No key, no login, no account.** Nothing to leak.
- **No HTML scraping.** This reads the documented JSON API. Nothing parses a
  page, nothing pretends to be a browser, no terms-of-service click-through is
  bypassed.
- **No personal data.** Six fields are kept per row and no others:
  `document_number`, `title`, `type`, `agency_names`, `publication_date`,
  `html_url`. Agency names are organisations. The shipped
  `sample_run.csv` contains no names of individuals, no emails and no phone
  numbers.
- **Polite.** One HTTP request per run, capped at 100 results
  (`per_page` is refused above 100), on a once-daily schedule.

The default search term is `heat pump`, which is what makes this relevant to
the HVAC niche. Any term works: `--term "solar tax credit"`.

## What it does

```
fetch (1 request)  ->  normalise  ->  load state.json  ->  classify  ->  write CSV + state  ->  one-line summary
```

- **Normalise.** Each API result becomes a flat row of strings.
  `agency_names` arrives as a list and is joined with `"; "` so a row stays one
  CSV line; runs of whitespace in titles are collapsed;
  missing optional fields become `""`.
- **Dedupe.** `document_number` is the key. If the same number appears twice in
  one response, the first copy wins and the second is dropped.
- **Change detection.** Against the previous run's `state.json`:
  | tag | meaning |
  |---|---|
  | `new` | the document number was not in the previous state |
  | `changed` | it was, but the title or the publication date differs |
  | `unchanged` | it was, and both match |
  | `removed` | it is in the previous state but the fetch no longer returns it |
  `removed` rows are written last, from their stored values, then drop out of
  the next state.
- **Output.** `sample_run.csv` -- the six fields plus a `change` column -- and
  `state.json`, keyed by document number.

## How to run it

Python 3.9 or newer. **No dependencies**: standard library only (`argparse`,
`csv`, `json`, `os`, `sys`, `urllib`). Nothing to `pip install`, nothing to age
out.

```bash
python3 tracker.py                                  # term "heat pump" -> sample_run.csv + state.json
python3 tracker.py --term "solar tax credit"
python3 tracker.py --per-page 25 --csv today.csv --state today.json
python3 tracker.py --source public-inspection       # fallback feed, see below
```

Run it twice to watch the diff work: the first run tags everything `new`, the
second tags the same rows `unchanged`.

Tests:

```bash
python3 -m unittest -v test_tracker
```

28 tests, no network -- every fetch is an injected fake, so the suite is
deterministic and runs offline in hundredths of a second. Saved output is in
`test_output.txt`. Covered: first run all-new, unchanged second run, a changed
title, a changed date, a removed document, malformed JSON, a corrupt state file,
an HTTP failure surfacing its status, empty results, key uniqueness, CSV header
and row shape, URL construction, and the one-request-per-run cap.

## The live run in this directory

`sample_run.csv` and `state.json` are a real run against the live API on
2026-09-16, not fixtures:

```
100 rows: 100 new, 0 changed, 0 unchanged, 0 removed -> sample_run.csv
```

A second live fetch against that state:

```
100 rows: 0 new, 0 changed, 100 unchanged, 0 removed
```

And the same fetch against a state seeded with one stale title and one document
that has since dropped out:

```
101 rows: 0 new, 1 changed, 99 unchanged, 1 removed
```

**Source note.** Earlier on 2026-09-16 the `documents.json` endpoint answered
**HTTP 500** ("Internal Server Error") to every request, for around twenty
minutes, while sibling endpoints on the same API stayed up. It recovered, and
the run above is against `documents.json` as intended. Because an outage there
is evidently possible, `--source public-inspection` reads
`/api/v1/public-inspection-documents/current.json` instead -- same API, same
publisher, same public-domain status, identical field names -- with the search
term applied locally, since that feed takes no search conditions. An HTTP
failure on either source exits `1` with the status in the message
(`tracker failed: source returned HTTP 500 for ...`) and writes nothing, so a
bad day never overwrites good state.

## Scheduling

`.github/workflows/tracker.yml` runs the tests, then the tracker, daily at
07:15 UTC (plus a manual **Run workflow** button that takes a term), and commits
`sample_run.csv` and `state.json` when they change. Because the state file is
committed, the diff is against yesterday's real run.

**Publishing.** GitHub only reads workflows from `.github/workflows` at a
repository's root, so the schedule runs once this directory is published as its
own public repo -- which is also where a prospect is meant to read it. Inside
this monorepo the file is inert.

## Pointing it at Google Sheets

Nothing here calls a Google API and no credentials exist in this repo.

**The easy way, no credentials at all:** `sample_run.csv` is a plain CSV.
In the Sheet, **File -> Import -> Upload**, choose it, and pick *Replace current
sheet* (the `change` column is already there) or *Append to current sheet* to
keep a history. Filter on `change != unchanged` to see only what moved.

**The automated way:** `sheets_append.py` is a ten-line stub of the Sheets API
call, with `REPLACE_ME_SPREADSHEET_ID` and `REPLACE_ME_TAB_NAME` placeholders.
It is not executed and not imported by the tracker or the tests. To use it:
`pip install google-api-python-client google-auth`, create a service account,
keep its key file outside the repo and point `GOOGLE_APPLICATION_CREDENTIALS` at
it, then share the Sheet with the service account's email. In CI the key belongs
in an Actions secret, never in the repo.

## What this demo is not

It tracks one public government feed. It is not a general scraper for arbitrary
sites, it holds no login, and it will not be pointed at a source whose terms
forbid automated access or at pages carrying personal data.
