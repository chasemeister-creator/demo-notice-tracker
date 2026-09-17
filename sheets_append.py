"""Stub: append the latest run to a Google Sheet. NOT EXECUTED in this demo.

No credentials exist in this repo and no Google API is called anywhere in it.
Fill the two placeholders, `pip install google-api-python-client google-auth`,
and point GOOGLE_APPLICATION_CREDENTIALS at a service-account key kept outside
the repo. Share the Sheet with that service account's email first.
"""

SPREADSHEET_ID = "REPLACE_ME_SPREADSHEET_ID"   # the /d/<this>/edit part of the Sheet URL
RANGE = "REPLACE_ME_TAB_NAME!A1"               # e.g. "notices!A1"


def append_rows(rows):
    """rows: list of lists, in tracker.CSV_HEADER order."""
    from googleapiclient.discovery import build  # placeholder import; not installed here

    sheets = build("sheets", "v4").spreadsheets().values()
    return sheets.append(
        spreadsheetId=SPREADSHEET_ID,
        range=RANGE,
        valueInputOption="RAW",
        body={"values": rows},
    ).execute()
