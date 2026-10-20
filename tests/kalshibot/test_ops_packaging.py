from dataclasses import fields
from pathlib import Path

import yaml

from kalshibot.config import Settings

ROOT = Path(__file__).resolve().parents[2]


def test_example_config_documents_every_setting_with_defaults():
    raw = yaml.safe_load((ROOT / "config.example.yaml").read_text())
    s, d = Settings.load(ROOT / "config.example.yaml"), Settings()
    for sect in ("strategy", "risk", "io"):
        names = {f.name for f in fields(getattr(d, sect))}
        assert set(raw[sect]) == names, sect
        for n in names:
            if (sect, n) == ("io", "data_dir"):
                assert getattr(s.io, n) == "/data"  # docker path
                continue
            assert getattr(getattr(s, sect), n) == getattr(getattr(d, sect), n), (sect, n)


def test_compose_binds_dashboard_to_localhost():
    c = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    bot = c["services"]["kalshibot"]
    assert bot["restart"] == "unless-stopped" and bot["env_file"] == ".env"
    assert all(p.startswith("127.0.0.1:") for p in bot["ports"])
    assert any(v.endswith(":/data") for v in bot["volumes"])
