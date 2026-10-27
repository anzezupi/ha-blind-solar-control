"""Make the pure modules importable without Home Assistant installed."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parents[1] / "custom_components" / "blind_control"))
