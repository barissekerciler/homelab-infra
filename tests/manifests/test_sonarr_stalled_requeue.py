"""sonarr-stalled-requeue picks rdt's stalled and expired tv-sonarr torrents from rdt's API."""

import datetime as dt
from pathlib import Path

import yaml

CONFIGMAP = Path(__file__).resolve().parents[2] / "apps/production/media/sonarr/stalled-requeue-configmap.yaml"


def script():
    namespace = {"__name__": "requeue"}
    exec(yaml.safe_load(CONFIGMAP.read_text())["data"]["requeue.py"], namespace)
    return namespace


def test_picks_old_stalled_and_expired_tv_torrents_only():
    s = script()
    old = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=1)).isoformat()
    new = dt.datetime.now(dt.timezone.utc).isoformat()
    torrents = [
        {"Hash": "a", "Category": "tv-sonarr", "RdStatusRaw": "stalled", "Error": None, "Completed": None, "Added": old},
        {"Hash": "b", "Category": "tv-sonarr", "RdStatusRaw": "downloaded",
         "Error": "Torrent lifetime of 360 minutes reached", "Completed": None, "Added": old},
        {"Hash": "c", "Category": "tv-sonarr", "RdStatusRaw": "stalled", "Error": None, "Completed": None, "Added": new},
        {"Hash": "d", "Category": "radarr", "RdStatusRaw": "stalled", "Error": None, "Completed": None, "Added": old},
        {"Hash": "e", "Category": "tv-sonarr", "RdStatusRaw": "downloading", "Error": None, "Completed": None, "Added": old},
        {"Hash": "f", "Category": "tv-sonarr", "RdStatusRaw": "stalled", "Error": None, "Completed": old, "Added": old},
    ]
    assert [t["Hash"] for t in s["stalled_torrents"](torrents)] == ["a", "b"]


def test_reads_no_files():
    text = yaml.safe_load(CONFIGMAP.read_text())["data"]["requeue.py"]
    assert "sqlite3" not in text and "config.xml" not in text
    assert "/Api/Authentication/Login" in text and "/Api/Torrents" in text
