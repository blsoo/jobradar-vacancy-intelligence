import unittest

from jobradar.cover_letter import build_cover_letter
from jobradar.models import RankedVacancy, ScoreResult, Vacancy


class CoverLetterTests(unittest.TestCase):
    def item(self, *, title, company, snippet, matched):
        vacancy = Vacancy(
            source="hh",
            external_id="1",
            title=title,
            company=company,
            url="https://hh.ru/vacancy/1",
            published_at="2026-09-09T10:00:00+03:00",
            snippet=snippet,
        )
        return RankedVacancy(
            vacancy=vacancy,
            score=ScoreResult(total=85, matched=tuple(matched)),
        )

    def test_letter_is_tailored_to_api_and_integrations(self):
        letter = build_cover_letter(
            self.item(
                title="Junior Integration Analyst",
                company="Acme",
                snippet="REST API, OpenAPI, интеграции между сервисами",
                matched=("junior", "REST", "Swagger/OpenAPI", "integrations"),
            )
        )
        self.assertIn("Junior Integration Analyst", letter)
        self.assertIn("Acme", letter)
        self.assertIn("REST API", letter)
        self.assertIn("интеграциями между сервисами", letter)
        self.assertIn("FlowBridge", letter)
        self.assertNotIn("PostgreSQL-ориентированную", letter)

    def test_letter_uses_data_evidence_only_when_relevant(self):
        letter = build_cover_letter(
            self.item(
                title="Системный аналитик",
                company="Data Team",
                snippet="Требования, SQL, PostgreSQL и модели данных",
                matched=("system analysis", "SQL", "PostgreSQL", "requirements"),
            )
        )
        self.assertIn("системным анализом", letter)
        self.assertIn("SQL и работой с данными", letter)
        self.assertIn("BullSignal", letter)
        self.assertIn("DevWork", letter)
        self.assertIn("PostgreSQL-ориентированную модель данных", letter)

    def test_english_c1_is_added_only_when_vacancy_mentions_english(self):
        english = build_cover_letter(
            self.item(
                title="System Analyst",
                company="Global Team",
                snippet="English B2+, REST API",
                matched=("system analysis", "REST"),
            )
        )
        russian = build_cover_letter(
            self.item(
                title="Системный аналитик",
                company="Local Team",
                snippet="REST API и требования",
                matched=("system analysis", "REST", "requirements"),
            )
        )
        self.assertIn("Английский C1 подтверждён тестом HH", english)
        self.assertNotIn("Английский C1 подтверждён тестом HH", russian)

    def test_non_junior_title_does_not_force_junior_positioning(self):
        letter = build_cover_letter(
            self.item(
                title="Технический аналитик",
                company="Platform Team",
                snippet="API и интеграции",
                matched=("API", "integrations"),
            )
        )
        self.assertNotIn("Ищу junior/стажёрскую позицию", letter)
        self.assertIn("Буду рад обсудить вакансию", letter)


if __name__ == "__main__":
    unittest.main()
