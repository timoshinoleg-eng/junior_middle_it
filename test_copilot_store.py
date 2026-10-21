import re
import unittest

from copilot import store as copilot_store
from copilot.store import CopilotStore, CopilotStoreUnavailable


class FakeCursor:
    def __init__(self, owner):
        self.owner = owner
        self.rows = []
        self.index = 0
        self.rowcount = -1

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, query, params=()):
        self.owner.queries.append((" ".join(str(query).split()), tuple(params or ())))
        self.rows = self.owner.rows_for(query)
        self.index = 0
        self.rowcount = len(self.rows)
        return self

    def fetchone(self):
        if self.index >= len(self.rows):
            return None
        row = self.rows[self.index]
        self.index += 1
        return row

    def fetchall(self):
        rows = self.rows[self.index:]
        self.index = len(self.rows)
        return rows


class FakeConnection:
    def __init__(self, owner):
        self.owner = owner

    def cursor(self):
        return FakeCursor(self.owner)


class FakeGrowthStore:
    def __init__(self, responses=None):
        self.queries = []
        self.responses = dict(responses or {})

    def _ensure_conn(self):
        return FakeConnection(self)

    def rows_for(self, query):
        flat = " ".join(str(query).split())
        for needle, rows in self.responses.items():
            if needle in flat:
                return list(rows)
        if "RETURNING" in flat.upper():
            return [(1,)]
        return []


class BrokenGrowthStore:
    def _ensure_conn(self):
        raise RuntimeError("backend down")


RELATION_RE = re.compile(
    r"\b(?:FROM|JOIN|INTO|UPDATE\s+(?!SET\b)|DELETE\s+FROM)\s+(?:public\.)?([A-Za-z_][A-Za-z0-9_]*)",
    re.IGNORECASE,
)


class CopilotStoreTests(unittest.TestCase):
    def _store(self, responses=None):
        self.backend = FakeGrowthStore(responses)
        return CopilotStore(self.backend)

    def test_store_requires_a_durable_backend(self):
        with self.assertRaises(CopilotStoreUnavailable):
            CopilotStore(None)

    def test_from_database_without_postgres_returns_none(self):
        class Db:
            pass

        self.assertIsNone(CopilotStore.from_database(Db()))

    def test_from_database_uses_the_growth_store(self):
        backend = FakeGrowthStore()
        db = type("Db", (), {"_growth_store": backend})()
        store = CopilotStore.from_database(db)
        self.assertIsNotNone(store)
        self.assertIs(store._store, backend)

    def test_backend_failure_is_wrapped(self):
        store = CopilotStore(BrokenGrowthStore())
        with self.assertRaises(CopilotStoreUnavailable):
            store.get_balance(1)

    def test_writes_only_touch_growth_relations(self):
        store = self._store()
        store.save_profile(1, grade="", skills="python", source="settings")
        store.save_evaluation(1, "hash", fit_score=70, matched_keywords=["python"])
        store.save_grade_audit("hash", {"declared_grade": "junior", "real_grade": "junior"})
        store.save_enrichment("hash", "https://example.com", "text")
        store.ensure_credit_row(1)
        store.log_credit_event(1, -1, "spend", "pdf")
        self.assertTrue(self.backend.queries)
        for query, _params in self.backend.queries:
            for relation in RELATION_RE.findall(query):
                self.assertTrue(
                    relation.lower().startswith("growth_"),
                    f"unexpected relation in query: {relation}",
                )

    def test_statements_are_single_and_comment_free(self):
        store = self._store()
        store.save_profile(1, grade="", skills="python", source="settings")
        store.get_profile(1)
        store.prune_expired()
        for query, _params in self.backend.queries:
            self.assertNotIn("--", query)
            self.assertNotIn("/*", query)
            self.assertNotIn("*/", query)
            self.assertNotIn(";", query)

    def test_parameters_are_bound_and_not_interpolated(self):
        store = self._store()
        store.save_profile(42, grade="", skills="python", source="settings")
        query, params = self.backend.queries[0]
        self.assertIn("%s", query)
        self.assertNotIn("42", query)
        self.assertEqual(params[0], 42)
        self.assertIn("python", params)

    def test_no_personal_document_data_is_persisted(self):
        store = self._store()
        store.save_profile(1, grade="", skills="python", source="settings")
        store.save_evaluation(1, "hash", fit_score=10, missing_skills=["docker"])
        store.save_enrichment("hash", "https://example.com", "page text")
        for query, _params in self.backend.queries:
            lowered = query.lower()
            for forbidden in ("resume_text", "resume_content", "file_content", "cv_text"):
                self.assertNotIn(forbidden, lowered)

    def test_spend_returns_none_when_the_balance_is_too_low(self):
        store = self._store({"UPDATE growth_copilot_credits SET balance = balance -": []})
        self.assertIsNone(store.spend_credits(1, 1))

    def test_spend_returns_the_remaining_balance(self):
        store = self._store({"UPDATE growth_copilot_credits SET balance = balance -": [(4,)]})
        self.assertEqual(store.spend_credits(1, 1), 4)

    def test_daily_allowance_is_granted_only_once_per_day(self):
        store = self._store({"UPDATE growth_copilot_credits": [(5,)]})
        self.assertEqual(store.grant_daily_allowance(1, 3), 5)
        store = self._store({"UPDATE growth_copilot_credits": []})
        self.assertIsNone(store.grant_daily_allowance(1, 3))

    def test_balance_is_read_from_the_counter_row(self):
        store = self._store({"SELECT balance FROM growth_copilot_credits": [(9,)]})
        self.assertEqual(store.get_balance(1), 9)

    def test_ai_report_is_cached(self):
        store = self._store()
        self.assertTrue(
            store.save_ai_report(
                1, "hash", claim="claim:1", model="m", answer="answer", used_resume=False
            )
        )
        query, params = self.backend.queries[-1]
        self.assertIn("growth_copilot_ai_reports", query)
        self.assertIn("answer", params)

    def test_ai_report_needs_the_claim_of_its_owner(self):
        store = self._store()
        self.assertTrue(store.claim_ai_report(1, "hash"))
        query, params = self.backend.queries[-1]
        self.assertIn("growth_copilot_ai_reports", query)
        self.assertIn("claim:", params[2])

    def test_ai_report_is_read_back(self):
        store = self._store(
            {"FROM growth_copilot_ai_reports": [("m", "answer", False, "2026-01-01")]}
        )
        report = store.get_ai_report(1, "hash")
        self.assertEqual(report["model"], "m")
        self.assertEqual(report["answer"], "answer")
        self.assertFalse(report["used_resume"])

    def test_prune_removes_only_growth_tables(self):
        store = self._store()
        removed = store.prune_expired()
        self.assertTrue(removed)
        for table in removed:
            self.assertTrue(table.startswith("growth_"))
        deletes = [q for q, _ in self.backend.queries if q.upper().startswith("DELETE")]
        self.assertEqual(len(deletes), 7)
        joined = " ".join(deletes)
        self.assertIn("growth_copilot_ai_reports", joined)
        self.assertIn("growth_copilot_forwarded_jobs", joined)
        self.assertIn("growth_copilot_applications", joined)


if __name__ == "__main__":
    unittest.main()
