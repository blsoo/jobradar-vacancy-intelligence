import unittest

from jobradar.interview_prep import build_interview_prep
from jobradar.models import RankedVacancy, ScoreResult, Vacancy


class InterviewPrepTests(unittest.TestCase):
    def test_system_analyst_gets_core_sql_rest_requirements_prep(self):
        item = RankedVacancy(
            vacancy=Vacancy(
                source="hh",
                external_id="1",
                title="Junior System Analyst",
                company="Company",
                url="https://example.test/vacancy/1",
                published_at="2026-08-26",
                snippet="REST API, HTTP, SQL, PostgreSQL, requirements, BPMN, Kafka",
            ),
            score=ScoreResult(total=84),
        )
        prep = build_interview_prep(item)
        joined = " ".join(prep.priorities).lower()
        self.assertIn("sql", joined)
        self.assertIn("http/rest", joined)
        self.assertIn("требован", joined)
        self.assertIn("bpmn", joined)
        self.assertIn("kafka", joined)
        self.assertGreaterEqual(len(prep.likely_questions), 3)

    def test_analyst_defaults_exist_even_when_description_is_thin(self):
        item = RankedVacancy(
            vacancy=Vacancy(
                source="hh",
                external_id="2",
                title="Стажер системный аналитик",
                company="Company",
                url="https://example.test/vacancy/2",
                published_at="2026-08-26",
                snippet="",
            ),
            score=ScoreResult(total=75),
        )
        prep = build_interview_prep(item)
        joined = " ".join(prep.priorities).lower()
        self.assertIn("sql", joined)
        self.assertIn("http/rest", joined)
        self.assertIn("пользовательские сценарии", joined)


if __name__ == "__main__":
    unittest.main()
