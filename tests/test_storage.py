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

    def ranked(self, external_id="1", score=80, company="Example"):
        vacancy = Vacancy(
            source="hh",
            external_id=external_id,
            title="Junior System Analyst",
            company=company,
            url=f"https://hh.ru/vacancy/{external_id}",
            published_at="2026-08-25T10:00:00+0300",
        )
        return RankedVacancy(vacancy=vacancy, score=ScoreResult(total=score, matched=("SQL",)))

    def _record_rejection(self, external_id: str, company: str, event_suffix: str) -> int:
        local_id = self.store.upsert(self.ranked(external_id, company=company))
        self.store.decide(local_id, "applied")
        self.store.record_employer_event(
            external_vacancy_id=external_id,
            source_event_id=f"reject-{event_suffix}",
            event_type="rejection",
            text="Спасибо, но мы выбрали другого кандидата",
            event_at="2026-09-01T10:00:00+03:00",
        )
        return local_id

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

    def test_runtime_setting_is_persistent(self):
        self.assertIsNone(self.store.get_setting("telegram_chat_id"))
        self.store.set_setting("telegram_chat_id", "123456")
        self.assertEqual(self.store.get_setting("telegram_chat_id"), "123456")
        self.store.set_setting("telegram_chat_id", "654321")
        self.assertEqual(self.store.get_setting("telegram_chat_id"), "654321")

    def test_company_is_suppressed_after_three_cold_rejections(self):
        self._record_rejection("101", "ООО Example Corp", "101")
        self._record_rejection("102", "Example Corp", "102")
        self._record_rejection("103", "«Example Corp»", "103")

        candidate = self.store.upsert(self.ranked("104", company="Example Corp"))
        self.assertIn("example corp", self.store.suppressed_companies())
        self.assertNotIn(candidate, [item.local_id for item in self.store.unsent(55, 20)])

    def test_two_rejections_do_not_suppress_company(self):
        self._record_rejection("201", "Good Corp", "201")
        self._record_rejection("202", "Good Corp", "202")

        candidate = self.store.upsert(self.ranked("203", company="Good Corp"))
        self.assertNotIn("good corp", self.store.suppressed_companies())
        self.assertIn(candidate, [item.local_id for item in self.store.unsent(55, 20)])

    def test_positive_engagement_prevents_rejection_from_being_cold(self):
        for index in range(1, 3):
            self._record_rejection(f"30{index}", "Maybe Corp", f"30{index}")

        local_id = self.store.upsert(self.ranked("303", company="Maybe Corp"))
        self.store.decide(local_id, "applied")
        self.store.record_employer_event(
            external_vacancy_id="303",
            source_event_id="positive-303",
            event_type="positive",
            text="Приглашаем на интервью",
            event_at="2026-09-02T10:00:00+03:00",
        )
        self.store.record_employer_event(
            external_vacancy_id="303",
            source_event_id="reject-303",
            event_type="rejection",
            text="После интервью выбрали другого кандидата",
            event_at="2026-09-03T10:00:00+03:00",
        )

        candidate = self.store.upsert(self.ranked("304", company="Maybe Corp"))
        stats = self.store.company_feedback()["maybe corp"]
        self.assertEqual(stats["rejections"], 3)
        self.assertEqual(stats["positive"], 1)
        self.assertEqual(stats["cold_rejections"], 2)
        self.assertIn(candidate, [item.local_id for item in self.store.unsent(55, 20)])


if __name__ == "__main__":
    unittest.main()
