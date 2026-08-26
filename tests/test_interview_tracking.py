from datetime import datetime
import os
import tempfile
import unittest
from zoneinfo import ZoneInfo

from jobradar.hh_inbox import classify_employer_message
from jobradar.interviews import detect_interview_datetime, reminder_times
from jobradar.storage import VacancyStore


class InterviewTrackingTests(unittest.TestCase):
    def test_classifies_positive_and_rejection(self):
        self.assertEqual(classify_employer_message("Приглашаем вас на собеседование"), "positive")
        self.assertEqual(classify_employer_message("К сожалению, мы выбрали другого кандидата"), "rejection")
        self.assertEqual(classify_employer_message("Здравствуйте, уточните пожалуйста опыт"), "message")

    def test_detects_explicit_russian_interview_datetime(self):
        detection = detect_interview_datetime(
            "Приглашаем на собеседование 29 августа в 15:30",
            "2026-08-26T10:00:00+03:00",
            "Europe/Moscow",
        )
        self.assertIsNotNone(detection)
        self.assertEqual(detection.scheduled_at.isoformat(), "2026-08-29T15:30:00+03:00")
        self.assertEqual(detection.confidence, "high")

    def test_detects_tomorrow_relative_to_message_time(self):
        detection = detect_interview_datetime(
            "Давайте созвонимся завтра в 12:00",
            "2026-08-26T10:00:00+03:00",
            "Europe/Moscow",
        )
        self.assertIsNotNone(detection)
        self.assertEqual(detection.scheduled_at.isoformat(), "2026-08-27T12:00:00+03:00")

    def test_persists_application_event_and_interview_once(self):
        fd, path = tempfile.mkstemp(prefix="jobradar-track-", suffix=".db")
        os.close(fd)
        store = VacancyStore(path)
        try:
            created, application_id = store.record_employer_event(
                external_vacancy_id="123",
                source_event_id="hh-chat:1:77",
                event_type="positive",
                text="Собеседование 29.08.2026 в 15:30",
                event_at="2026-08-26T10:00:00+03:00",
                chat_id="1",
            )
            self.assertTrue(created)
            duplicate, same_application = store.record_employer_event(
                external_vacancy_id="123",
                source_event_id="hh-chat:1:77",
                event_type="positive",
                text="Собеседование 29.08.2026 в 15:30",
                event_at="2026-08-26T10:00:00+03:00",
                chat_id="1",
            )
            self.assertFalse(duplicate)
            self.assertEqual(application_id, same_application)

            scheduled = datetime(2026, 8, 29, 15, 30, tzinfo=ZoneInfo("Europe/Moscow"))
            interview_id = store.schedule_interview(
                application_id=application_id,
                scheduled_at=scheduled,
                timezone="Europe/Moscow",
                confidence="high",
                evidence="29.08.2026 в 15:30",
                source_event_id="hh-chat:1:77",
                reminders=reminder_times(scheduled),
            )
            second_id = store.schedule_interview(
                application_id=application_id,
                scheduled_at=scheduled,
                timezone="Europe/Moscow",
                confidence="high",
                evidence="29.08.2026 в 15:30",
                source_event_id="hh-chat:1:77",
                reminders=reminder_times(scheduled),
            )
            self.assertEqual(interview_id, second_id)
            self.assertEqual(store.application_stats()["invited"], 1)
            self.assertEqual(store.application_stats()["interviews"], 1)
        finally:
            store.close()
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
