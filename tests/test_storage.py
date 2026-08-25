import os
import tempfile
import unittest

from jobradar.models import RankedVacancy, ScoreResult, Vacancy
from jobradar.storage import VacancyStore


class StorageTests(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(prefix="jobradar-", suffix=".db")
        os.close(fd)
        self.store = VacancyStore(self.path)

    def tearDown(self):
        self.store.close()
        os.unlink(self.path)

    def ranked(self, external_id="1", score=80):
        vacancy = Vacancy(
            source="hh",
            external_id=external_id,
            title="Junior System Analyst",
            company="Example",
            url=f"https://hh.ru/vacancy/{external_id}",
            published_at="2026-08-25T10:00:00+0300",
        )
        return RankedVacancy(vacancy=vacancy, score=ScoreResult(total=score, matched=("SQL",)))

    def test_upsert_deduplicates_source_and_external_id(self):
        first_id = self.store.upsert(self.ranked())
        second_id = self.store.upsert(self.ranked(score=90))
        self.assertEqual(first_id, second_id)
        self.assertEqual(self.store.stats()["total"], 1)
        self.assertEqual(self.store.get(first_id).score.total, 90)

    def test_sent_vacancy_leaves_delivery_queue(self):
        local_id = self.store.upsert(self.ranked())
        self.assertEqual(len(self.store.unsent(55, 10)), 1)
        self.store.mark_sent(local_id)
        self.assertEqual(self.store.unsent(55, 10), [])

    def test_decision_is_recorded(self):
        local_id = self.store.upsert(self.ranked())
        changed = self.store.decide(local_id, "saved")
        self.assertTrue(changed)
        self.assertEqual(self.store.stats()["saved"], 1)

    def test_repeated_same_decision_is_idempotent(self):
        local_id = self.store.upsert(self.ranked())
        self.assertTrue(self.store.decide(local_id, "saved"))
        self.assertFalse(self.store.decide(local_id, "saved"))
        self.assertEqual(self.store.decision_event_count(local_id), 1)

    def test_skip_reason_is_aggregated(self):
        first = self.store.upsert(self.ranked("1"))
        second = self.store.upsert(self.ranked("2"))
        self.store.decide(first, "skipped", reason="salary")
        self.store.decide(second, "skipped", reason="salary")
        self.assertEqual(self.store.skip_reason_stats(), {"salary": 2})


if __name__ == "__main__":
    unittest.main()
