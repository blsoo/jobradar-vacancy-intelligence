from __future__ import annotations

from email.header import Header
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

    def test_rejects_hh_resume_moderation_and_strips_css(self) -> None:
        subject = Header("Ваше резюме прошло модерацию", "utf-8").encode()
        html = """
        <html>
          <head>
            <style>
              #outlook a { padding: 0; }
              body { margin: 0; padding: 0; }
              .mj-column-per-100 { width: 100% !important; }
            </style>
          </head>
          <body>
            <h1>Ваше резюме прошло модерацию</h1>
            <p>Теперь оно доступно работодателям.</p>
          </body>
        </html>
        """
        raw = (
            "From: hh.ru <noreply@hh.ru>\r\n"
            f"Subject: {subject}\r\n"
            "Message-ID: <resume-moderation@example>\r\n"
            "Date: Wed, 26 Aug 2026 17:20:00 +0300\r\n"
            "Content-Type: text/html; charset=utf-8\r\n\r\n"
            + html
        ).encode("utf-8")

        msg = parse_email(43, raw)
        self.assertIn("Ваше резюме прошло модерацию", msg.text)
        self.assertNotIn("#outlook", msg.text)
        self.assertNotIn("mj-column", msg.text)
        self.assertNotIn("padding:", msg.text)
        self.assertFalse(is_hh_recruiting_message(msg))

    def test_accepts_neutral_hh_employer_message(self) -> None:
        subject = Header("Новое сообщение от работодателя", "utf-8").encode()
        raw = (
            "From: hh.ru <noreply@hh.ru>\r\n"
            f"Subject: {subject}\r\n"
            "Message-ID: <employer-message@example>\r\n"
            "Content-Type: text/plain; charset=utf-8\r\n\r\n"
            "Новое сообщение от работодателя: Здравствуйте, готовы обсудить ваш опыт."
        ).encode("utf-8")
        msg = parse_email(44, raw)
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
