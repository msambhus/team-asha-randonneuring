"""Tests for vercel.json configuration (INFRA-04)."""
import json
from pathlib import Path


def test_vercel_config_valid():
    """INFRA-04: vercel.json has valid configuration with Python build."""
    vercel_path = Path(__file__).parent.parent / "vercel.json"
    config = json.loads(vercel_path.read_text())
    assert config["version"] == 2
    # Verify Python build target exists
    build_srcs = [b["src"] for b in config["builds"]]
    assert "api/index.py" in build_srcs
