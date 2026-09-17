"""Tests for the demo 2 notices tracker. No network: every fetch is injected.

Run:  python3 -m unittest -v test_tracker
"""

import csv
import json
import os
import shutil
import tempfile
import unittest

import tracker


def fake_fetcher(payload):
    """Return a fetcher that answers any URL with this payload.

    ``payload`` may be a dict/list (encoded to JSON) or a raw string, so the
    malformed-input tests can hand back something that is not JSON at all.
    """

    def fetch(url, timeout=30):
        fetch.urls.append(url)
        if isinstance(payload, str):
            return payload
        return json.dumps(payload)

    fetch.urls = []
    return fetch


def doc(number, title="Energy Conservation Program", date="2026-09-17", type_="Notice"):
    return {
        "document_number": number,
        "title": title,
        "type": type_,
        "agency_names": ["Energy Department"],
        "publication_date": date,
        "html_url": "https://www.federalregister.gov/documents/%s" % number,
    }


def results(*docs):
    return {"count": len(docs), "results": list(docs)}


class TempDirCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="demo2-")
        self.csv_path = os.path.join(self.tmp, "run.csv")
        self.state_path = os.path.join(self.tmp, "state.json")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def run_once(self, payload, term="heat pump", source=tracker.SOURCE_DOCUMENTS):
        return tracker.run(
            term,
            source,
            100,
            self.csv_path,
            self.state_path,
            fetcher=fake_fetcher(payload),
        )

    def read_csv(self):
        with open(self.csv_path, newline="", encoding="utf-8") as handle:
            return list(csv.reader(handle))

    def changes(self, rows):
        return [(row["document_number"], row["change"]) for row in rows]


class FirstRunTest(TempDirCase):
    def test_first_run_tags_every_row_new(self):
        rows = self.run_once(results(doc("2026-001"), doc("2026-002")))
        self.assertEqual(
            self.changes(rows), [("2026-001", "new"), ("2026-002", "new")]
        )

    def test_first_run_writes_state_keyed_by_document_number(self):
        self.run_once(results(doc("2026-001"), doc("2026-002")))
        with open(self.state_path, encoding="utf-8") as handle:
            state = json.load(handle)
        self.assertEqual(sorted(state), ["2026-001", "2026-002"])
        self.assertEqual(state["2026-001"]["publication_date"], "2026-09-17")


class SecondRunTest(TempDirCase):
    def test_identical_second_run_is_all_unchanged(self):
        payload = results(doc("2026-001"), doc("2026-002"))
        self.run_once(payload)
        rows = self.run_once(payload)
        self.assertEqual(
            self.changes(rows),
            [("2026-001", "unchanged"), ("2026-002", "unchanged")],
        )

    def test_changed_title_is_tagged_changed(self):
        self.run_once(results(doc("2026-001", title="Energy Conservation Program")))
        rows = self.run_once(
            results(doc("2026-001", title="Energy Conservation Program; Correction"))
        )
        self.assertEqual(self.changes(rows), [("2026-001", "changed")])

    def test_changed_publication_date_is_tagged_changed(self):
        self.run_once(results(doc("2026-001", date="2026-09-17")))
        rows = self.run_once(results(doc("2026-001", date="2026-09-18")))
        self.assertEqual(self.changes(rows), [("2026-001", "changed")])

    def test_a_new_row_beside_an_old_one_is_tagged_new(self):
        self.run_once(results(doc("2026-001")))
        rows = self.run_once(results(doc("2026-001"), doc("2026-003")))
        self.assertEqual(
            self.changes(rows), [("2026-001", "unchanged"), ("2026-003", "new")]
        )


class RemovedTest(TempDirCase):
    def test_document_absent_from_the_fetch_is_tagged_removed_and_keeps_its_values(self):
        self.run_once(results(doc("2026-001"), doc("2026-002", title="Sunshine Act")))
        rows = self.run_once(results(doc("2026-001")))
        self.assertEqual(
            self.changes(rows), [("2026-001", "unchanged"), ("2026-002", "removed")]
        )
        removed = rows[-1]
        self.assertEqual(removed["title"], "Sunshine Act")
        self.assertEqual(removed["agency_names"], "Energy Department")

    def test_removed_rows_drop_out_of_the_next_state(self):
        self.run_once(results(doc("2026-001"), doc("2026-002")))
        self.run_once(results(doc("2026-001")))
        with open(self.state_path, encoding="utf-8") as handle:
            state = json.load(handle)
        self.assertEqual(sorted(state), ["2026-001"])


class MalformedInputTest(TempDirCase):
    def test_malformed_json_raises_a_clean_tracker_error(self):
        with self.assertRaises(tracker.TrackerError) as caught:
            self.run_once("<html>502 Bad Gateway</html>")
        self.assertIn("did not return valid JSON", str(caught.exception))

    def test_results_of_the_wrong_shape_raises_a_clean_tracker_error(self):
        with self.assertRaises(tracker.TrackerError) as caught:
            self.run_once({"count": 1, "results": "nope"})
        self.assertIn("expected a list", str(caught.exception))

    def test_a_result_without_a_document_number_raises_a_clean_tracker_error(self):
        broken = doc("2026-001")
        broken["document_number"] = ""
        with self.assertRaises(tracker.TrackerError) as caught:
            self.run_once(results(broken))
        self.assertIn("no document_number", str(caught.exception))

    def test_a_corrupt_state_file_raises_a_clean_tracker_error(self):
        with open(self.state_path, "w", encoding="utf-8") as handle:
            handle.write("{not json")
        with self.assertRaises(tracker.TrackerError) as caught:
            self.run_once(results(doc("2026-001")))
        self.assertIn("not valid JSON", str(caught.exception))

    def test_an_http_failure_reports_its_status(self):
        def failing(url, timeout=30):
            raise tracker.TrackerError("source returned HTTP 500 for %s" % url)

        with self.assertRaises(tracker.TrackerError) as caught:
            tracker.run(
                "heat pump",
                tracker.SOURCE_DOCUMENTS,
                100,
                self.csv_path,
                self.state_path,
                fetcher=failing,
            )
        self.assertIn("HTTP 500", str(caught.exception))


class EmptyResultTest(TempDirCase):
    def test_empty_result_on_a_first_run_writes_a_header_only_csv(self):
        rows = self.run_once(results())
        self.assertEqual(rows, [])
        self.assertEqual(self.read_csv(), [tracker.CSV_HEADER])
        self.assertEqual(tracker.summarise(rows), "0 rows: 0 new, 0 changed, 0 unchanged, 0 removed")

    def test_empty_result_after_a_populated_run_marks_everything_removed(self):
        self.run_once(results(doc("2026-001"), doc("2026-002")))
        rows = self.run_once(results())
        self.assertEqual(
            self.changes(rows), [("2026-001", "removed"), ("2026-002", "removed")]
        )

    def test_a_missing_results_key_is_treated_as_empty(self):
        rows = self.run_once({"count": 0})
        self.assertEqual(rows, [])


class KeyUniquenessTest(TempDirCase):
    def test_a_repeated_document_number_is_kept_once_first_wins(self):
        payload = results(
            doc("2026-001", title="First copy"),
            doc("2026-002"),
            doc("2026-001", title="Duplicate copy"),
        )
        rows = self.run_once(payload)
        numbers = [row["document_number"] for row in rows]
        self.assertEqual(numbers, ["2026-001", "2026-002"])
        self.assertEqual(rows[0]["title"], "First copy")

    def test_a_duplicate_does_not_reappear_as_changed_on_the_next_run(self):
        payload = results(doc("2026-001", title="First copy"), doc("2026-001", title="Duplicate copy"))
        self.run_once(payload)
        rows = self.run_once(payload)
        self.assertEqual(self.changes(rows), [("2026-001", "unchanged")])


class CsvShapeTest(TempDirCase):
    def test_csv_header_is_the_six_fields_plus_change(self):
        self.run_once(results(doc("2026-001")))
        self.assertEqual(
            self.read_csv()[0],
            [
                "document_number",
                "title",
                "type",
                "agency_names",
                "publication_date",
                "html_url",
                "change",
            ],
        )

    def test_every_data_row_has_seven_columns_in_header_order(self):
        self.run_once(results(doc("2026-001"), doc("2026-002")))
        rows = self.read_csv()
        self.assertEqual(len(rows), 3)
        for row in rows[1:]:
            self.assertEqual(len(row), len(tracker.CSV_HEADER))
        self.assertEqual(rows[1][0], "2026-001")
        self.assertEqual(rows[1][6], "new")


class NormaliseTest(unittest.TestCase):
    def test_agency_names_are_joined_and_whitespace_is_collapsed(self):
        row = tracker.normalise(
            {
                "document_number": "2026-001",
                "title": "Meetings;  Sunshine  Act  ",
                "type": "Notice",
                "agency_names": ["Energy Department", "Federal Register Office"],
                "publication_date": "2026-09-17",
                "html_url": "https://example.invalid/doc",
            }
        )
        self.assertEqual(row["title"], "Meetings; Sunshine Act")
        self.assertEqual(row["agency_names"], "Energy Department; Federal Register Office")

    def test_missing_optional_fields_become_empty_strings(self):
        row = tracker.normalise({"document_number": "2026-001"})
        self.assertEqual(row["title"], "")
        self.assertEqual(row["agency_names"], "")
        self.assertEqual(sorted(row), sorted(tracker.FIELDS))


class UrlTest(unittest.TestCase):
    def test_documents_url_carries_the_term_order_and_page_size(self):
        url = tracker.build_url("heat pump", per_page=100)
        self.assertIn("conditions%5Bterm%5D=heat+pump", url)
        self.assertIn("order=newest", url)
        self.assertIn("per_page=100", url)
        self.assertTrue(url.startswith("https://www.federalregister.gov/api/v1/documents.json?"))

    def test_public_inspection_url_is_the_plain_current_feed(self):
        self.assertEqual(
            tracker.build_url("heat pump", source=tracker.SOURCE_PUBLIC_INSPECTION),
            "https://www.federalregister.gov/api/v1/public-inspection-documents/current.json",
        )

    def test_an_unknown_source_or_page_size_is_refused(self):
        with self.assertRaises(tracker.TrackerError):
            tracker.build_url("heat pump", source="scrape-the-website")
        with self.assertRaises(tracker.TrackerError):
            tracker.build_url("heat pump", per_page=500)

    def test_one_run_makes_exactly_one_request(self):
        fetch = fake_fetcher(results(doc("2026-001")))
        tracker.fetch_rows("heat pump", fetcher=fetch)
        self.assertEqual(len(fetch.urls), 1)


class TermFilterTest(unittest.TestCase):
    def test_public_inspection_results_are_filtered_locally_by_term(self):
        payload = results(
            doc("2026-001", title="Energy Conservation Program for Heat Pumps"),
            doc("2026-002", title="Meetings; Sunshine Act"),
        )
        rows = tracker.fetch_rows(
            "heat pump",
            source=tracker.SOURCE_PUBLIC_INSPECTION,
            fetcher=fake_fetcher(payload),
        )
        self.assertEqual([row["document_number"] for row in rows], ["2026-001"])

    def test_documents_results_are_not_filtered_again_locally(self):
        payload = results(doc("2026-002", title="Meetings; Sunshine Act"))
        rows = tracker.fetch_rows(
            "heat pump", source=tracker.SOURCE_DOCUMENTS, fetcher=fake_fetcher(payload)
        )
        self.assertEqual([row["document_number"] for row in rows], ["2026-002"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
