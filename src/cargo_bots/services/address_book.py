from __future__ import annotations

from functools import cached_property
from pathlib import Path


class AddressTemplateService:
    def __init__(self, template_path: str) -> None:
        self.template_path = Path(template_path)

    @cached_property
    def template(self) -> str:
        return self.template_path.read_text(encoding="utf-8")

    def render(self, client_code: str) -> str:
        rendered = self.template
        for placeholder in ("JJ-XXXX", "J-XXXX", "{{CLIENT_CODE}}"):
            rendered = rendered.replace(placeholder, client_code)
        return rendered

