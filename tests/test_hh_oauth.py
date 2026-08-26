import os
import tempfile
import time
import unittest
from urllib.parse import parse_qs, urlparse

from jobradar.hh_oauth import HHOAuthManager
from jobradar.storage import VacancyStore


class FakeOAuth(HHOAuthManager):
    def _post_token(self, values):
        self.last_values = dict(values)
        return self._save(
            {
                "access_token": "access-2",
                "refresh_token": "refresh-2",
                "expires_in": 1209600,
            }
        )


class HHOAuthTests(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(prefix="jobradar-oauth-", suffix=".db")
        os.close(fd)
        self.store = VacancyStore(self.path)
        self.oauth = FakeOAuth(
            self.store,
            client_id="client-id",
            client_secret="client-secret",
            redirect_uri="https://github.com/blsoo/jobradar-vacancy-intelligence",
        )

    def tearDown(self):
        self.store.close()
        os.unlink(self.path)

    def test_authorization_url_has_state_and_redirect(self):
        url = self.oauth.authorization_url()
        query = parse_qs(urlparse(url).query)
        self.assertEqual(query["client_id"], ["client-id"])
        self.assertEqual(query["response_type"], ["code"])
        self.assertEqual(query["redirect_uri"], ["https://github.com/blsoo/jobradar-vacancy-intelligence"])
        self.assertEqual(query["state"], [self.store.get_setting("hh_oauth_state")])

    def test_exchange_requires_matching_state_and_persists_tokens(self):
        url = self.oauth.authorization_url()
        state = parse_qs(urlparse(url).query)["state"][0]
        token = self.oauth.exchange_redirect(
            "https://github.com/blsoo/jobradar-vacancy-intelligence?code=abc123&state=" + state
        )
        self.assertEqual(token.access_token, "access-2")
        self.assertEqual(self.store.get_setting("hh_access_token"), "access-2")
        self.assertEqual(self.store.get_setting("hh_refresh_token"), "refresh-2")
        self.assertEqual(self.oauth.last_values["grant_type"], "authorization_code")
        self.assertEqual(self.store.get_setting("hh_oauth_state"), "")

    def test_exchange_rejects_wrong_state(self):
        self.oauth.authorization_url()
        with self.assertRaises(ValueError):
            self.oauth.exchange_redirect(
                "https://github.com/blsoo/jobradar-vacancy-intelligence?code=abc123&state=wrong"
            )

    def test_expired_access_uses_refresh_token(self):
        self.store.set_setting("hh_access_token", "old-access")
        self.store.set_setting("hh_refresh_token", "old-refresh")
        self.store.set_setting("hh_access_expires_at", str(int(time.time()) - 1))
        access = self.oauth.access_token()
        self.assertEqual(access, "access-2")
        self.assertEqual(self.oauth.last_values["grant_type"], "refresh_token")
        self.assertEqual(self.oauth.last_values["refresh_token"], "old-refresh")


if __name__ == "__main__":
    unittest.main()
