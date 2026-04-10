import unittest

from cargo_bots.services.excel_parser import SupplierWorkbookParser


class SupplierWorkbookParserTest(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = SupplierWorkbookParser()

    def test_parse_row_extracts_single_client_and_track(self) -> None:
        parsed, failed = self.parser._parse_row(
            2,
            {
                "Код клиента": "J-1234",
                "Трек код": "JT5473061949539",
            },
        )

        self.assertIsNone(failed)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.client_code, "J-1234")
        self.assertEqual(parsed.track_code, "JT5473061949539")

    def test_parse_row_marks_multiple_client_codes_as_failed(self) -> None:
        parsed, failed = self.parser._parse_row(
            3,
            {
                "Комментарий": "J-1234 J-5678",
                "Трек": "777396977982272",
            },
        )

        self.assertIsNone(parsed)
        self.assertIsNotNone(failed)
        self.assertIn("Expected exactly one client code", failed.reason)


if __name__ == "__main__":
    unittest.main()

