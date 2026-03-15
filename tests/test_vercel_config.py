"""Tests for vercel.json configuration (INFRA-04)."""
import json
from pathlib import Path


def test_max_duration():
    """INFRA-04: vercel.json has maxDuration: 60 for api/index.py."""
    vercel_path = Path(__file__).parent.parent / "vercel.json"
    config = json.loads(vercel_path.read_text())
    assert config["functions"]["api/index.py"]["maxDuration"] == 60
