import unittest

from cargo_bots.services.normalization import (
    extract_client_code_candidates,
    extract_track_code_candidates,
    normalize_client_code,
    normalize_name,
)


class NormalizationTest(unittest.TestCase):
    def test_normalize_client_code_adds_separator(self) -> None:
        self.assertEqual(normalize_client_code("j1234"), "J-1234")

    def test_normalize_name_collapses_whitespace(self) -> None:
        self.assertEqual(normalize_name("  Ivan   Ivanov "), "IVAN IVANOV")

    def test_extract_client_code_candidates_deduplicates(self) -> None:
        self.assertEqual(
            extract_client_code_candidates("J-1111, J-1111, J-2222"),
            ["J-1111", "J-2222"],
        )

    def test_extract_track_code_candidates_supports_letters_and_digits(self) -> None:
        self.assertEqual(
            extract_track_code_candidates("JT5473061949539 / 777396977982272"),
            ["JT5473061949539", "777396977982272"],
        )


if __name__ == "__main__":
    unittest.main()
