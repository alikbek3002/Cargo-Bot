from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from cargo_bots.services.address_book import AddressTemplateService


class AddressTemplateServiceTest(unittest.TestCase):
    def test_render_replaces_known_placeholders(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            template_path = Path(tmp_dir) / "address.txt"
            template_path.write_text("Получатель JJ-XXXX\nСклад J-XXXX", encoding="utf-8")

            service = AddressTemplateService(str(template_path))
            rendered = service.render("J-4321")

            self.assertIn("J-4321", rendered)
            self.assertNotIn("JJ-XXXX", rendered)
            self.assertNotIn("J-XXXX", rendered)


if __name__ == "__main__":
    unittest.main()

