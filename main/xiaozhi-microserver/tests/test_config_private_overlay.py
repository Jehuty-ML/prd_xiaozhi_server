"""Tests for template + private config and broadcast redaction."""

from __future__ import annotations

import sys
from pathlib import Path

MICRO_ROOT = Path(__file__).resolve().parents[1]
COMMON = MICRO_ROOT / "common"
sys.path.insert(0, str(COMMON.resolve()))


def test_load_service_config_merges_private(tmp_path: Path):
    from xiaozhi_common.config_files import load_service_config

    (tmp_path / "config.yaml").write_text(
        "server:\n  port: 8000\nmanager-api:\n  enabled: false\n  secret: ''\n",
        encoding="utf-8",
    )
    data = tmp_path / "data"
    data.mkdir()
    (data / ".config.yaml").write_text(
        "manager-api:\n  url: http://x\n  secret: real-secret\n  enabled: true\n",
        encoding="utf-8",
    )
    merged, _, _, private_loaded = load_service_config(tmp_path)
    assert private_loaded
    assert merged["server"]["port"] == 8000
    assert merged["manager-api"]["secret"] == "real-secret"
    assert merged["manager-api"]["enabled"] is True


def test_snapshot_for_peer_redacts_except_access():
    from xiaozhi_common.config_files import snapshot_for_peer
    from xiaozhi_common.constants import ACCESS_SERVICE, AGENT_SERVICE

    cfg = {
        "manager-api": {"url": "http://x", "secret": "s3cret", "enabled": True},
        "server": {"auth_key": "ak", "admin": {"token": "tok"}},
    }
    access = snapshot_for_peer(cfg, ACCESS_SERVICE, access_service=ACCESS_SERVICE)
    assert access["manager-api"]["secret"] == "s3cret"
    agent = snapshot_for_peer(cfg, AGENT_SERVICE, access_service=ACCESS_SERVICE)
    assert agent["manager-api"]["secret"] == ""
    assert agent["server"]["auth_key"] == ""
    assert agent["server"]["admin"]["token"] == ""
