from __future__ import annotations

import unittest

from jobradar.email_inbox import is_hh_recruiting_message, parse_email


class EmailInboxTests(unittest.TestCase):
    def test_parses_hh_invitation_and_vacancy_id(self) -> None:
        raw = (
            b"From: HeadHunter <noreply@hh.ru>\r\n"
            b"To: applicant@example.com\r\n"
            b"Subject: =?utf-8?b?0J/RgNC40LPQu9Cw0YjQtdC90LjQtSDQvdCwINGB0L7QsdC10YHQtdC00L7QstCw0L3QuNC1?=\r\n"
            b"Message-ID: <hh-123@example>\r\n"
            b"Date: Wed, 26 Aug 2026 09:00:00 +0300\r\n"
            b"Content-Type: text/plain; charset=utf-8\r\n\r\n"
            + "Работодатель приглашает вас на собеседование 29.08 в 15:30. https://hh.ru/vacancy/123456".encode("utf-8")
        )
        msg = parse_email(42, raw)
        self.assertEqual(msg.vacancy_id, "123456")
        self.assertIn("собеседование", msg.text)
        self.assertTrue(is_hh_recruiting_message(msg))

    def test_rejects_unrelated_mail(self) -> None:
        raw = (
            b"From: shop@example.com\r\n"
            b"Subject: Sale\r\n"
            b"Message-ID: <sale@example>\r\n"
            b"Content-Type: text/plain; charset=utf-8\r\n\r\n"
            b"Discount today"
        )
        msg = parse_email(7, raw)
        self.assertFalse(is_hh_recruiting_message(msg))


if __name__ == "__main__":
    unittest.main()
