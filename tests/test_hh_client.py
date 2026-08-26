import unittest
import xml.etree.ElementTree as ET

from jobradar.hh_client import HHClient


class HHRssParsingTests(unittest.TestCase):
    def test_rss_item_maps_to_vacancy(self):
        item = ET.fromstring(
            """
            <item>
              <title>Junior системный аналитик</title>
              <link>https://hh.ru/vacancy/136200894</link>
              <pubDate>2026-08-25T10:06:26.523+03:00</pubDate>
              <description><![CDATA[
                <p>Вакансия компании: ALLIO</p>
                <p>Создана: 25.08.2026</p>
                <p>Регион: Москва</p>
                <p>Предполагаемый уровень месячного дохода: от 80 000 ₽</p>
              ]]></description>
            </item>
            """
        )
        vacancy = HHClient._from_rss_item(item)
        self.assertIsNotNone(vacancy)
        assert vacancy is not None
        self.assertEqual(vacancy.external_id, "136200894")
        self.assertEqual(vacancy.company, "ALLIO")
        self.assertEqual(vacancy.area, "Москва")
        self.assertEqual(vacancy.salary_from, 80000)
        self.assertIsNone(vacancy.salary_to)
        self.assertEqual(vacancy.salary_currency, "RUR")
        self.assertEqual(vacancy.application_url, "https://hh.ru/vacancy/136200894")

    def test_salary_range_is_parsed(self):
        self.assertEqual(
            HHClient._salary_from_text("70 000–100 000 ₽"),
            (70000, 100000, "RUR"),
        )

    def test_missing_salary_stays_unknown(self):
        self.assertEqual(
            HHClient._salary_from_text("не указан"),
            (None, None, None),
        )


if __name__ == "__main__":
    unittest.main()
