import os
import tempfile
import unittest

from jobradar.app import handle_update
from jobradar.config import Settings
from jobradar.models import RankedVacancy, ScoreResult, Vacancy
from jobradar.storage import VacancyStore
from jobradar.telegram import TelegramClient


class RecordingTelegram(TelegramClient):
    def __init__(self):
        super().__init__("test-token", "42")
        self.calls = []

    def _call(self, method, payload=None):
        self.calls.append((method, payload or {}))
        if method == "getUpdates":
            return {"ok": True, "result": []}
        return {"ok": True, "result": True}


class FakeCallbackTelegram:
    def __init__(self):
        self.chat_id = "42"
        self.answers = []
        self.messages = []

    @property
    def enabled(self):
        return True

    @property
    def can_send(self):
        return True

    def answer_callback(self, callback_query_id, text):
        self.answers.append((callback_query_id, text))

    def send_text(self, text, *, reply_markup=None, chat_id=None):
        self.messages.append((text, reply_markup))


class TelegramPollingTests(unittest.TestCase):
    def test_get_updates_removes_stale_webhook_once_without_dropping_updates(self):
        telegram = RecordingTelegram()

        telegram.get_updates(offset=10, timeout=0)
        telegram.get_updates(offset=11, timeout=0)

        methods = [method for method, _ in telegram.calls]
        self.assertEqual(methods, ["deleteWebhook", "getUpdates", "getUpdates"])
        self.assertEqual(
            telegram.calls[0][1],
            {"drop_pending_updates": False},
        )


class CallbackTests(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(prefix="jobradar-callback-", suffix=".db")
        os.close(fd)
        self.store = VacancyStore(self.path)
        self.settings = Settings(
            telegram_bot_token="test-token",
            telegram_chat_id="42",
            db_path=self.path,
            poll_seconds=300,
            score_threshold=70,
            max_push_per_cycle=3,
            target_salary_rub=70000,
            remote_preferred=True,
            hh_search_queries=("system analyst",),
            hh_area="113",
            hh_per_page=10,
            hh_user_agent="JobRadarTests/1.0",
            hh_oauth_token="",
            hh_resume_id="",
        )
        vacancy = Vacancy(
            source="hh",
            external_id="12345",
            title="Junior System Analyst",
            company="Example",
            url="https://hh.ru/vacancy/12345",
            apply_url="https://hh.ru/applicant/vacancy_response?vacancyId=12345",
            published_at="2026-09-09T12:00:00+0300",
            snippet="REST API SQL requirements integration",
        )
        ranked = RankedVacancy(
            vacancy=vacancy,
            score=ScoreResult(total=88, matched=("REST", "SQL", "requirements")),
        )
        self.local_id = self.store.upsert(ranked)
        self.telegram = FakeCallbackTelegram()

    def tearDown(self):
        self.store.close()
        os.unlink(self.path)

    def callback(self, action):
        return {
            "update_id": 1,
            "callback_query": {
                "id": f"cb-{action}",
                "data": f"{action}:{self.local_id}",
                "message": {"chat": {"id": 42}},
            },
        }

    def decision(self):
        row = self.store.conn.execute(
            "SELECT decision FROM vacancies WHERE id=?", (self.local_id,)
        ).fetchone()
        return row["decision"]

    def test_save_button_persists_decision_and_acknowledges_callback(self):
        handle_update(self.settings, self.store, self.telegram, self.callback("save"))
        self.assertEqual(self.decision(), "saved")
        self.assertEqual(self.telegram.answers[-1][0], "cb-save")

    def test_skip_button_persists_decision_and_acknowledges_callback(self):
        handle_update(self.settings, self.store, self.telegram, self.callback("skip"))
        self.assertEqual(self.decision(), "skipped")
        self.assertEqual(self.telegram.answers[-1][0], "cb-skip")

    def test_apply_button_builds_vacancy_specific_letter_and_followup_button(self):
        handle_update(self.settings, self.store, self.telegram, self.callback("apply"))
        self.assertEqual(self.decision(), "apply_requested")
        text, markup = self.telegram.messages[-1]
        self.assertIn("Junior System Analyst", text)
        self.assertIn("REST API", text)
        self.assertEqual(
            markup["inline_keyboard"][1][0]["callback_data"],
            f"applied:{self.local_id}",
        )

    def test_applied_button_moves_application_into_funnel(self):
        handle_update(self.settings, self.store, self.telegram, self.callback("apply"))
        handle_update(self.settings, self.store, self.telegram, self.callback("applied"))
        self.assertEqual(self.decision(), "applied")
        self.assertEqual(self.store.application_stats().get("applied"), 1)


if __name__ == "__main__":
    unittest.main()
