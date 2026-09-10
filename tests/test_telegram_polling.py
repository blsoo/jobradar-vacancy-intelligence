import os
import tempfile
import unittest

from jobradar.app import handle_update
from jobradar.config import Settings
from jobradar.models import RankedVacancy, ScoreResult, Vacancy
from jobradar.storage import VacancyStore
from jobradar.telegram import BOT_COMMANDS, MAIN_KEYBOARD, TelegramClient


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
        self.home_messages = []
        self.ui_configured = 0

    @property
    def enabled(self):
        return True

    @property
    def can_send(self):
        return True

    def configure_ui(self):
        self.ui_configured += 1

    def answer_callback(self, callback_query_id, text):
        self.answers.append((callback_query_id, text))

    def send_text(self, text, *, reply_markup=None, chat_id=None):
        self.messages.append((text, reply_markup))

    def send_home(self, text):
        self.home_messages.append((text, MAIN_KEYBOARD))


class TelegramPollingTests(unittest.TestCase):
    def test_get_updates_recovers_webhook_and_installs_command_menu_once(self):
        telegram = RecordingTelegram()
        telegram.get_updates(offset=10, timeout=0)
        telegram.get_updates(offset=11, timeout=0)
        methods = [method for method, _ in telegram.calls]
        self.assertEqual(
            methods,
            ["deleteWebhook", "setMyCommands", "setChatMenuButton", "getUpdates", "getUpdates"],
        )
        self.assertEqual(telegram.calls[0][1], {"drop_pending_updates": False})
        self.assertEqual(telegram.calls[1][1]["commands"], BOT_COMMANDS)

    def test_main_keyboard_has_only_working_primary_sections(self):
        labels = [button["text"] for row in MAIN_KEYBOARD["keyboard"] for button in row]
        self.assertEqual(
            labels,
            [
                "🎯 Вакансии",
                "🔄 Обновить",
                "📌 Сохранённые",
                "🔥 Отклики",
                "📬 Ответы",
                "📊 Статистика",
                "🚫 Стоп-лист",
                "ℹ️ Помощь",
            ],
        )
        commands = {entry["command"] for entry in BOT_COMMANDS}
        self.assertIn("responses", commands)
        self.assertNotIn("hh_status", commands)
        self.assertTrue(MAIN_KEYBOARD["is_persistent"])


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

    def callback(self, action, extra=None):
        data = f"{action}:{self.local_id}"
        if extra is not None:
            data += f":{extra}"
        return {
            "update_id": 1,
            "callback_query": {
                "id": f"cb-{action}-{extra or 'none'}",
                "data": data,
                "message": {"chat": {"id": 42}},
            },
        }

    def message(self, text):
        return {"update_id": 2, "message": {"text": text, "chat": {"id": 42}}}

    def decision(self):
        row = self.store.conn.execute("SELECT decision FROM vacancies WHERE id=?", (self.local_id,)).fetchone()
        return row["decision"]

    def decision_reason(self):
        row = self.store.conn.execute("SELECT decision_reason FROM vacancies WHERE id=?", (self.local_id,)).fetchone()
        return row["decision_reason"]

    def test_save_button_persists_decision_and_acknowledges_callback(self):
        handle_update(self.settings, self.store, self.telegram, self.callback("save"))
        self.assertEqual(self.decision(), "saved")

    def test_skip_button_asks_reason_before_persisting(self):
        handle_update(self.settings, self.store, self.telegram, self.callback("skip"))
        self.assertIsNone(self.decision())
        text, markup = self.telegram.messages[-1]
        self.assertIn("Почему мимо", text)
        callback_data = [button["callback_data"] for row in markup["inline_keyboard"] for button in row]
        self.assertIn(f"skipr:{self.local_id}:salary", callback_data)
        self.assertIn(f"skipr:{self.local_id}:direction", callback_data)
        handle_update(self.settings, self.store, self.telegram, self.callback("skipr", "direction"))
        self.assertEqual(self.decision(), "skipped")
        self.assertEqual(self.decision_reason(), "direction")

    def test_apply_button_builds_vacancy_specific_letter(self):
        handle_update(self.settings, self.store, self.telegram, self.callback("apply"))
        self.assertEqual(self.decision(), "apply_requested")
        text, markup = self.telegram.messages[-1]
        self.assertIn("Junior System Analyst", text)
        self.assertIn("REST API", text)
        self.assertNotIn("HH OAuth", text)
        self.assertEqual(markup["inline_keyboard"][1][0]["callback_data"], f"applied:{self.local_id}")

    def test_applied_button_offers_manual_response_tracking(self):
        handle_update(self.settings, self.store, self.telegram, self.callback("apply"))
        handle_update(self.settings, self.store, self.telegram, self.callback("applied"))
        self.assertEqual(self.decision(), "applied")
        text, markup = self.telegram.messages[-1]
        self.assertIn("Когда работодатель ответит", text)
        data = [button.get("callback_data") for row in markup["inline_keyboard"] for button in row]
        self.assertIn(f"resp:{self.local_id}:positive", data)
        self.assertIn(f"resp:{self.local_id}:message", data)
        self.assertIn(f"resp:{self.local_id}:rejection", data)

    def test_manual_response_updates_application_without_hh_oauth(self):
        handle_update(self.settings, self.store, self.telegram, self.callback("applied"))
        handle_update(self.settings, self.store, self.telegram, self.callback("resp", "rejection"))
        self.assertEqual(self.store.application_stats().get("rejected"), 1)
        row = self.store.conn.execute("SELECT event_type FROM employer_events ORDER BY id DESC LIMIT 1").fetchone()
        self.assertEqual(row["event_type"], "rejection")

    def test_panel_stats_button_routes_without_slash_command(self):
        handle_update(self.settings, self.store, self.telegram, self.message("📊 Статистика"))
        self.assertIn("📊 JobRadar", self.telegram.messages[-1][0])

    def test_responses_panel_explains_non_oauth_tracking(self):
        handle_update(self.settings, self.store, self.telegram, self.message("📬 Ответы"))
        text = self.telegram.messages[-1][0]
        self.assertIn("Ответы работодателей", text)
        self.assertIn("ручные статусы работают", text)
        self.assertNotIn("client_id", text)

    def test_help_button_restores_persistent_home_panel(self):
        handle_update(self.settings, self.store, self.telegram, self.message("ℹ️ Помощь"))
        text, markup = self.telegram.home_messages[-1]
        self.assertIn("🔥 Отклик", text)
        self.assertNotIn("OAuth", text)
        self.assertTrue(markup["is_persistent"])


if __name__ == "__main__":
    unittest.main()
