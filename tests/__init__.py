"""Tests run against the made-up account in tests/fixtures (rensim.toml there), never real game files.
Set before any test imports renaissance, since settings are read once at import."""
import os
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent / "fixtures"
os.environ["RENSIM_CONFIG"] = str(FIXTURES / "rensim.toml")
