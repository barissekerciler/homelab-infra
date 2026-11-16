"""sonarr-stalled-requeue picks Sonarr's queue items that warn of a stalled download or an executable."""

import datetime as dt
from pathlib import Path

import yaml

CONFIGMAP = Path(__file__).resolve().parents[2] / "apps/production/media/sonarr/stalled-requeue-configmap.yaml"


def script():
    namespace = {"__name__": "requeue"}
    exec(yaml.safe_load(CONFIGMAP.read_text())["data"]["requeue.py"], namespace)
    return namespace


def test_picks_old_stalled_queue_warnings_only():
    s = script()
    old = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=1)).isoformat()
    new = dt.datetime.now(dt.timezone.utc).isoformat()
    stalled = [{"title": "The download is stalled with no connections", "messages": []}]
    records = [
        {"id": 1, "status": "warning", "statusMessages": stalled, "added": old},
        {"id": 2, "status": "warning", "errorMessage": "Download client is reporting an error", "added": old},
        {"id": 3, "status": "warning", "statusMessages": stalled, "added": new},
        {"id": 4, "status": "downloading", "statusMessages": stalled, "added": old},
        {"id": 5, "status": "warning", "errorMessage": "Not enough free space", "added": old},
    ]
    assert [r["id"] for r in s["stalled_queue_items"](records)] == [1, 2]


def test_picks_programs_and_empty_downloads_at_once():
    s = script()
    new = dt.datetime.now(dt.timezone.utc).isoformat()
    exe = [{"title": "Show S01E01", "messages": ["Caution: Found executable file with extension: '.exe'"]}]
    other = [{"title": "Show S01E02", "messages": ["No files found are eligible for import"]}]
    path = "/downloads/tv-sonarr/Show S01E03 1080p WEB H264-GRP"
    records = [
        {"id": 1, "status": "completed", "trackedDownloadStatus": "warning", "statusMessages": exe,
         "outputPath": path, "size": 5, "added": new},
        {"id": 2, "status": "completed", "trackedDownloadStatus": "warning", "statusMessages": other,
         "outputPath": path, "size": 5, "added": new},
        {"id": 3, "status": "completed", "trackedDownloadStatus": "ok", "outputPath": f"{path}.mkv",
         "size": 5, "added": new},
        {"id": 4, "status": "downloading", "outputPath": f"{path}.EXE", "size": 5, "added": new},
        {"id": 5, "status": "completed", "outputPath": f"{path}.bin", "size": 0, "added": new},
        {"id": 6, "status": "completed", "outputPath": path, "size": 0, "added": new},
        {"id": 7, "status": "queued", "outputPath": path, "size": 0, "added": new},
    ]
    assert [r["id"] for r in s["executable_queue_items"](records)] == [1, 4, 5, 6]


def test_talks_only_to_sonarr():
    text = yaml.safe_load(CONFIGMAP.read_text())["data"]["requeue.py"]
    assert "sqlite3" not in text and "config.xml" not in text
    assert "SONARR_URL" in text and "_URL" not in text.replace("SONARR_URL", "")
