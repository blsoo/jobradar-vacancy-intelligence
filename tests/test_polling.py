import os
import tempfile
import unittest

from jobradar.app import poll_updates
from jobradar.config import Settings
from jobradar.storage import VacancyStore


class FakeTelegram:
    def __init__(self):
        self.chat_id = ""
        self.offsets = []
        self.messages = []

    @property
    def enabled(self):
        return True

    @property
    def can_send(self):
        return bool(self.chat_id)

    def bind_chat(self, chat_id):
        self.chat_id = str(chat_id)

    def get_updates(self, *, offset=0, timeout=1):
        self.offsets.append(offset)
        if offset != 0:
            return []
        return [
            {"update_id": 100, "message": {"chat": {"id": 42}, "text": "/start"}},
            {"update_id": 101, "message": {"chat": {"id": 42}, "text": "/stats"}},
        ]

    def send_text(self, text, *, reply_markup=None, chat_id=None):
        self.messages.append(text)

    def send_vacancy(self, item):
        raise AssertionError("empty test store must not push vacancies")

    def answer_callback(self, callback_query_id, text):
        pass


class PollingTests(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(prefix="jobradar-poll-", suffix=".db")
        os.close(fd)
        self.store = VacancyStore(self.path)
        self.settings = Settings(
            telegram_bot_token="test-token",
            telegram_chat_id="",
            db_path=self.path,
            poll_seconds=300,
            score_threshold=55,
            max_push_per_cycle=5,
            target_salary_rub=70000,
            remote_preferred=True,
            hh_search_queries=("junior system analyst",),
            hh_area="113",
            hh_per_page=10,
            hh_user_agent="JobRadarTests/1.0",
            hh_oauth_token="",
            hh_resume_id="",
        )

    def tearDown(self):
        self.store.close()
        os.unlink(self.path)

    def test_update_offset_and_owner_binding_survive_worker_restart(self):
        telegram = FakeTelegram()
        processed = poll_updates(self.settings, self.store, telegram, timeout=0)

        self.assertEqual(processed, 2)
        self.assertEqual(self.store.get_setting("telegram_chat_id"), "42")
        self.assertEqual(self.store.get_setting("telegram_update_offset"), "102")
        self.assertEqual(telegram.offsets, [0])
        self.assertEqual(len(telegram.messages), 2)

        processed_again = poll_updates(self.settings, self.store, telegram, timeout=0)
        self.assertEqual(processed_again, 0)
        self.assertEqual(telegram.offsets, [0, 102])
        self.assertEqual(len(telegram.messages), 2)


if __name__ == "__main__":
    unittest.main()
