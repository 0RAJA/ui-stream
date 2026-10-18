"""Bilingual commands and PyPI links must preserve the same runnable contract."""

import unittest

try:
    from tools.check_docs import render_pypi, validate_commands
except ModuleNotFoundError:
    render_pypi = validate_commands = None


class DocumentationTest(unittest.TestCase):
    def test_pypi_links_are_absolute_and_images_use_raw_content(self):
        self.assertTrue(callable(render_pypi), "PyPI documentation converter must exist")
        result = render_pypi(
            "[API](docs/api.md)\n![diagram](docs/diagrams/architecture.png)\n[site](https://example.org)"
        )
        self.assertIn("https://github.com/0RAJA/ui-stream/blob/main/docs/api.md", result)
        self.assertIn("https://raw.githubusercontent.com/0RAJA/ui-stream/main/docs/diagrams/architecture.png", result)
        self.assertIn("[site](https://example.org)", result)

    def test_bilingual_commands_cannot_silently_diverge(self):
        self.assertTrue(callable(validate_commands), "bilingual command gate must exist")
        source = "```sh\npython -m examples.counter\n```"
        validate_commands(source, source)
        with self.assertRaises(ValueError):
            validate_commands(source, "```sh\npython -m demos.sqlite\n```")
