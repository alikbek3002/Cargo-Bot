import unittest

from cargo_bots.run import normalize_app_role


class RunRoleTest(unittest.TestCase):
    def test_accepts_known_roles(self) -> None:
        self.assertEqual(normalize_app_role("admin_web"), "admin_web")
        self.assertEqual(normalize_app_role("CLIENT_WEB"), "client_web")
        self.assertEqual(normalize_app_role("worker"), "worker")

    def test_rejects_missing_role(self) -> None:
        with self.assertRaises(RuntimeError):
            normalize_app_role(None)

    def test_rejects_unknown_role(self) -> None:
        with self.assertRaises(RuntimeError):
            normalize_app_role("bot")


if __name__ == "__main__":
    unittest.main()
