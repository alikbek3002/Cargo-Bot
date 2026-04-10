import unittest

from cargo_bots.core.access import has_admin_access


class AdminAccessTest(unittest.TestCase):
    def test_allows_any_user_when_admin_ids_are_empty(self) -> None:
        self.assertTrue(has_admin_access(123456789, []))

    def test_rejects_missing_user(self) -> None:
        self.assertFalse(has_admin_access(None, []))

    def test_checks_whitelist_when_admin_ids_are_configured(self) -> None:
        self.assertTrue(has_admin_access(10, [10, 20]))
        self.assertFalse(has_admin_access(30, [10, 20]))


if __name__ == "__main__":
    unittest.main()
