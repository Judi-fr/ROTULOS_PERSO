from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from django.core.management import call_command
from django.test import SimpleTestCase


class ErdMermaidTests(SimpleTestCase):
    def run_command(self, *args):
        out = StringIO()
        call_command("erd_mermaid", *args, stdout=out)
        return out.getvalue()

    def test_includes_own_models_with_keys(self):
        diagram = self.run_command()
        self.assertTrue(diagram.startswith("erDiagram\n"))
        self.assertIn("    Order {", diagram)
        self.assertIn("bigauto id PK", diagram)
        self.assertIn("store_connection_id FK", diagram)

    def test_relations_follow_nullability(self):
        diagram = self.run_command()
        self.assertIn('    Order ||--o{ OrderStatusEvent : "order"', diagram)
        self.assertIn('    Order |o--o{ Label : "order"', diagram)

    def test_django_models_appear_without_columns(self):
        diagram = self.run_command()
        self.assertIn("    User {\n        auto id PK\n    }", diagram)
        self.assertNotIn("Session {", diagram)

    def test_output_writes_mermaid_block(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "docs" / "erd.md"
            self.run_command("--output", str(path))
            content = path.read_text(encoding="utf-8")
        self.assertIn("```mermaid\nerDiagram\n", content)
        self.assertTrue(content.endswith("```\n"))
