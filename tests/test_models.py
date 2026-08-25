import unittest

from jobradar.models import Vacancy


class VacancyModelTests(unittest.TestCase):
    def test_hh_apply_url_is_preserved(self):
        vacancy = Vacancy.from_hh_item(
            {
                "id": "123",
                "name": "Junior System Analyst",
                "alternate_url": "https://hh.ru/vacancy/123",
                "apply_alternate_url": "https://hh.ru/applicant/vacancy_response?vacancyId=123",
                "published_at": "2026-08-25T10:00:00+0300",
                "employer": {"name": "Example"},
                "area": {"name": "Москва"},
                "schedule": {"name": "Удаленная работа"},
                "experience": {"name": "Нет опыта"},
                "snippet": {"requirement": "SQL REST", "responsibility": "API"},
            }
        )
        self.assertEqual(
            vacancy.application_url,
            "https://hh.ru/applicant/vacancy_response?vacancyId=123",
        )

    def test_application_url_falls_back_to_vacancy_url(self):
        vacancy = Vacancy(
            source="hh",
            external_id="1",
            title="Role",
            company="Example",
            url="https://hh.ru/vacancy/1",
            published_at="2026-08-25T10:00:00+0300",
        )
        self.assertEqual(vacancy.application_url, vacancy.url)


if __name__ == "__main__":
    unittest.main()
