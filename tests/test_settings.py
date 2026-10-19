"""Settings: rensim.toml is found from the current folder up, and RENSIM_* environment variables win."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from renaissance import settings

from . import FIXTURES


def env(**overrides):
    """The environment without any RENSIM_* variables, plus these."""
    clean = {k: v for k, v in os.environ.items() if not k.startswith("RENSIM_")}
    return mock.patch.dict(os.environ, {**clean, **overrides}, clear=True)


class Settings(unittest.TestCase):
    def test_tests_use_the_fixture_account(self):
        self.assertEqual((settings.MY_ACCOUNT, settings.MY_FAMILY, settings.HOME), ("TST", "STARK", "Carlisle"))
        self.assertEqual(settings.DATA, FIXTURES / "data")
        self.assertEqual(settings.ORDERS_DIR, FIXTURES / "orders")

    def test_file_then_environment(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rensim.toml"
            path.write_text('account = "ABC"\nfamily = "SMITH"\nhome = "Kelso"\n', encoding="utf-8")
            with env(RENSIM_FAMILY="JONES"):
                values = settings.load(path)
        self.assertEqual((values["account"], values["family"], values["home"]), ("ABC", "JONES", "Kelso"))
        self.assertEqual(values["data_dir"], "data")

    def test_missing_file_uses_defaults(self):
        with env():
            self.assertEqual(settings.load(None), settings.DEFAULTS)

    def test_config_is_found_in_a_parent_folder(self):
        with tempfile.TemporaryDirectory() as tmp, env():
            root = Path(tmp).resolve()
            (root / "rensim.toml").write_text('account = "ABC"\n', encoding="utf-8")
            (root / "orders" / "old").mkdir(parents=True)
            self.assertEqual(settings.find_config(root / "orders" / "old"), root / "rensim.toml")

    def test_rensim_config_wins(self):
        with env(RENSIM_CONFIG="/somewhere/else/rensim.toml"):
            self.assertEqual(settings.find_config(FIXTURES), Path("/somewhere/else/rensim.toml").resolve())


if __name__ == "__main__":
    unittest.main()
