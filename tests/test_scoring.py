import unittest

from jobradar.models import Vacancy
from jobradar.scoring import score_vacancy


class ScoringTests(unittest.TestCase):
    def test_junior_system_analyst_scores_high(self):
        vacancy = Vacancy(
            source="hh",
            external_id="1",
            title="Junior системный аналитик",
            company="Example",
            url="https://hh.ru/vacancy/1",
            published_at="2026-08-25T10:00:00+0300",
            schedule="Удаленная работа",
            experience="Нет опыта",
            salary_from=70000,
            salary_to=90000,
            salary_currency="RUR",
            snippet="SQL REST API JSON HTTP анализ требований интеграции",
        )
        result = score_vacancy(vacancy)
        self.assertGreaterEqual(result.total, 80)
        self.assertIn("SQL", result.matched)
        self.assertIn("remote", result.matched)

    def test_senior_role_is_penalized(self):
        vacancy = Vacancy(
            source="hh",
            external_id="2",
            title="Senior System Analyst",
            company="Example",
            url="https://hh.ru/vacancy/2",
            published_at="2026-08-25T10:00:00+0300",
            experience="От 3 до 6 лет",
            snippet="SQL REST API JSON",
        )
        result = score_vacancy(vacancy)
        self.assertLess(result.total, 55)
        self.assertIn("senior role", result.risks)

    def test_score_is_capped(self):
        vacancy = Vacancy(
            source="hh",
            external_id="3",
            title="Junior системный аналитик стажер",
            company="Example",
            url="https://hh.ru/vacancy/3",
            published_at="2026-08-25T10:00:00+0300",
            schedule="Удаленная работа",
            experience="Нет опыта",
            salary_from=100000,
            salary_currency="RUR",
            snippet="SQL REST HTTP JSON API Swagger OpenAPI требования UML BPMN интеграции PostgreSQL Git",
        )
        result = score_vacancy(vacancy)
        self.assertEqual(result.total, 100)


if __name__ == "__main__":
    unittest.main()
