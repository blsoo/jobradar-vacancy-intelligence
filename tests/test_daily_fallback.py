import os
import tempfile
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from jobradar.app import push_daily_fallback
from jobradar.config import Settings
from jobradar.models import RankedVacancy, ScoreResult, Vacancy
from jobradar.storage import VacancyStore


class FakeTelegram:
    chat_id = "42"

    def __init__(self):
        self.digests = []

    @property
    def can_send(self):
        return True

    def send_digest(self, items, *, target_salary_rub=70000):
        self.digests.append([item.score.total for item in items])


class DailyFallbackTests(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(prefix="jobradar-fallback-", suffix=".db")
        os.close(fd)
        self.store = VacancyStore(self.path)
        self.settings = Settings(
            telegram_bot_token="test",
            telegram_chat_id="42",
            db_path=self.path,
            poll_seconds=300,
            score_threshold=70,
            max_push_per_cycle=3,
            target_salary_rub=70000,
            remote_preferred=True,
            hh_search_queries=("junior system analyst",),
            hh_area="113",
            hh_per_page=10,
            hh_user_agent="JobRadarTests/1.0",
            hh_oauth_token="",
            hh_resume_id="",
            timezone="Europe/Moscow",
        )
        self.telegram = FakeTelegram()

    def tearDown(self):
        self.store.close()
        os.unlink(self.path)

    def add(self, external_id, score):
        vacancy = Vacancy(
            source="hh",
            external_id=str(external_id),
            title="Junior System Analyst",
            company="Example",
            url=f"https://hh.ru/vacancy/{external_id}",
            published_at="2026-08-26T10:00:00+03:00",
        )
        self.store.upsert(
            RankedVacancy(
                vacancy=vacancy,
                score=ScoreResult(total=score, matched=("SQL",)),
            )
        )

    @staticmethod
    def epoch(hour):
        dt = datetime(2026, 8, 26, hour, 0, tzinfo=ZoneInfo("Europe/Moscow"))
        return int(dt.timestamp())

    def test_fallback_waits_until_evening_and_only_uses_60_to_69(self):
        self.add(1, 68)
        self.add(2, 64)
        self.add(3, 59)
        self.add(4, 75)
        self.assertEqual(
            push_daily_fallback(
                self.settings,
                self.store,
                self.telegram,
                now_epoch=self.epoch(17),
            ),
            0,
        )
        self.assertEqual(
            push_daily_fallback(
                self.settings,
                self.store,
                self.telegram,
                now_epoch=self.epoch(18),
            ),
            2,
        )
        self.assertEqual(self.telegram.digests, [[68, 64]])

    def test_fallback_runs_only_once_per_day(self):
        self.add(1, 68)
        self.add(2, 66)
        self.assertEqual(
            push_daily_fallback(
                self.settings,
                self.store,
                self.telegram,
                now_epoch=self.epoch(18),
            ),
            2,
        )
        self.add(3, 65)
        self.assertEqual(
            push_daily_fallback(
                self.settings,
                self.store,
                self.telegram,
                now_epoch=self.epoch(20),
            ),
            0,
        )

    def test_primary_digest_today_blocks_fallback(self):
        self.add(1, 68)
        self.store.set_setting("last_primary_digest_date", "2026-08-26")
        self.assertEqual(
            push_daily_fallback(
                self.settings,
                self.store,
                self.telegram,
                now_epoch=self.epoch(20),
            ),
            0,
        )


if __name__ == "__main__":
    unittest.main()
