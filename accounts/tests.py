from django.contrib.auth import get_user_model
from django.test import TestCase


class CustomUserTests(TestCase):
    def test_create_user(self):
        user = get_user_model().objects.create_user(
            username="player",
            email="player@example.com",
            password="test-password",
        )

        self.assertEqual(str(user), "player@example.com")
        self.assertTrue(user.check_password("test-password"))
