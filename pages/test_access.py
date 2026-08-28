import os
import subprocess
from datetime import datetime, timezone
from unittest.mock import patch

from django.contrib.auth.hashers import make_password
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from gamescal.access import ACCESS_SESSION_KEY, MAX_FAILED_ATTEMPTS_PER_MINUTE
from gamescal.context_processors import _build_info, build_info

TEST_PASSWORD = "shared-calendar-password"
TEST_PASSWORD_HASH = make_password(TEST_PASSWORD)


@override_settings(
    GAMESCAL_ACCESS_PASSWORD_HASH=TEST_PASSWORD_HASH,
    GAMESCAL_ACCESS_SESSION_AGE=31_536_000,
)
class SharedAccessTests(TestCase):
    def setUp(self):
        cache.clear()

    def unlock(self, *, next_url=None):
        data = {"password": TEST_PASSWORD}
        if next_url is not None:
            data["next"] = next_url
        return self.client.post(reverse("shared_access_login"), data)

    def test_protected_page_redirects_to_password_form(self):
        response = self.client.get(reverse("home"))

        self.assertRedirects(
            response,
            f'{reverse("shared_access_login")}?next=%2F',
            fetch_redirect_response=False,
        )
        self.assertEqual(response.headers["Cache-Control"], "private, no-store")
        self.assertEqual(response.headers["X-Robots-Tag"], "noindex, noarchive")

    def test_password_form_is_public_and_has_no_top_bar(self):
        response = self.client.get(reverse("shared_access_login"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "shared_access/login.html")
        self.assertContains(response, "Enter the shared password")
        self.assertNotContains(response, 'class="navbar')
        self.assertContains(response, 'name="password"')

    def test_correct_password_unlocks_the_application(self):
        response = self.unlock()

        self.assertRedirects(response, reverse("home"))
        self.assertIn(ACCESS_SESSION_KEY, self.client.session)
        self.assertEqual(
            int(response.cookies["sessionid"]["max-age"]),
            31_536_000,
        )

        home = self.client.get(reverse("home"))
        self.assertEqual(home.status_code, 200)
        self.assertNotContains(home, 'class="navbar')
        self.assertContains(home, "Lock this device")
        self.assertEqual(home.headers["Cache-Control"], "private, no-store")
        self.assertEqual(home.headers["X-Robots-Tag"], "noindex, noarchive")

    def test_session_expiry_rolls_forward_on_an_authorized_request(self):
        self.unlock()
        self.client.session.set_expiry(60)
        self.client.session.save()

        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            int(response.cookies["sessionid"]["max-age"]),
            31_536_000,
        )

    def test_wrong_password_does_not_unlock_the_application(self):
        response = self.client.post(
            reverse("shared_access_login"),
            {"password": "wrong"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "That password is not correct.")
        self.assertNotIn(ACCESS_SESSION_KEY, self.client.session)

    def test_failed_password_attempts_are_rate_limited(self):
        for _ in range(MAX_FAILED_ATTEMPTS_PER_MINUTE - 1):
            response = self.client.post(
                reverse("shared_access_login"),
                {"password": "wrong"},
            )
            self.assertEqual(response.status_code, 200)

        response = self.client.post(
            reverse("shared_access_login"),
            {"password": "wrong"},
        )

        self.assertEqual(response.status_code, 429)
        self.assertContains(
            response,
            "Too many attempts. Try again in a minute.",
            status_code=429,
        )

    def test_safe_next_url_is_preserved(self):
        destination = f'{reverse("home")}?view=all'

        response = self.unlock(next_url=destination)

        self.assertRedirects(
            response,
            destination,
            fetch_redirect_response=False,
        )

    def test_external_next_url_is_rejected(self):
        response = self.unlock(next_url="https://example.com/")

        self.assertRedirects(response, reverse("home"))

    def test_lock_clears_access_for_this_device(self):
        self.unlock()

        response = self.client.post(reverse("shared_access_lock"))

        self.assertRedirects(response, reverse("shared_access_login"))
        self.assertNotIn(ACCESS_SESSION_KEY, self.client.session)
        locked_home = self.client.get(reverse("home"))
        self.assertEqual(locked_home.status_code, 302)

    def test_changing_password_hash_invalidates_existing_sessions(self):
        self.unlock()

        with override_settings(
            GAMESCAL_ACCESS_PASSWORD_HASH=make_password("new-password")
        ):
            response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith(reverse("shared_access_login")))


class DisabledSharedAccessTests(TestCase):
    @override_settings(GAMESCAL_ACCESS_PASSWORD_HASH="")
    def test_gate_is_disabled_without_a_configured_hash_in_development(self):
        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)


class BuildInfoTests(TestCase):
    def tearDown(self):
        _build_info.cache_clear()

    @patch("gamescal.context_processors.subprocess.run")
    def test_build_info_reads_the_current_git_commit(self, run):
        run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=(
                "b4c63e8185d5b7355df48e7e0f6790dc173d40ce"
                "\x002026-08-26T18:53:35-07:00\n"
            ),
        )
        _build_info.cache_clear()

        with patch.dict(
            os.environ,
            {"GIT_COMMIT_SHA": "", "GIT_COMMIT_DATE": ""},
        ):
            context = build_info(None)

        self.assertEqual(context["build_info"]["sha"], "b4c63e8")
        self.assertEqual(
            context["build_info"]["committed_at"].isoformat(),
            "2026-08-26T18:53:35-07:00",
        )
        self.assertEqual(
            context["build_info"]["committed_date"].isoformat(), "2026-08-26"
        )
        self.assertEqual(
            context["build_info"]["commit_url"],
            "https://github.com/sri/gamescal/commit/"
            "b4c63e8185d5b7355df48e7e0f6790dc173d40ce",
        )

    @override_settings(GAMESCAL_ACCESS_PASSWORD_HASH="")
    @patch("gamescal.context_processors._build_info")
    def test_footer_links_the_commit_sha_and_date_to_github(self, info):
        committed_at = datetime(2026, 8, 26, tzinfo=timezone.utc)
        info.return_value = {
            "sha": "b4c63e8",
            "committed_at": committed_at,
            "committed_date": committed_at.date(),
            "commit_url": "https://github.com/sri/gamescal/commit/full-sha",
        }

        response = self.client.get(reverse("home"))

        self.assertContains(
            response,
            'href="https://github.com/sri/gamescal/commit/full-sha"',
        )
        self.assertContains(response, "<code>b4c63e8</code>:2026-08-26</a>(")
        self.assertContains(response, " ago)</small>")
