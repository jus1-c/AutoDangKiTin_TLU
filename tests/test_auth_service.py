import unittest

from src.models.user import User
from src.services.auth_service import AuthService


class RetryAuthService(AuthService):
    def __init__(self, failures):
        self.failures = failures
        self.calls = 0

    async def login(self, username, password, save=True):
        self.calls += 1
        if self.failures:
            raise self.failures.pop(0)
        return User(username=username, password=password, student_id="student")


class AuthServiceTest(unittest.IsolatedAsyncioTestCase):
    async def test_retry_invalid_retries_until_login_returns_user(self):
        auth = RetryAuthService([ValueError("invalid response")])

        user = await auth.login_until_success(
            "student", "password", retry_invalid=True,
        )

        self.assertEqual(user.student_id, "student")
        self.assertEqual(auth.calls, 2)

    async def test_default_does_not_retry_invalid_response(self):
        auth = RetryAuthService([ValueError("invalid response")])

        with self.assertRaises(ValueError):
            await auth.login_until_success("student", "password")

        self.assertEqual(auth.calls, 1)


if __name__ == "__main__":
    unittest.main()
