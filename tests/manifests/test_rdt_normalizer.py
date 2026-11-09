import re
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml


CONFIGMAP = Path(__file__).resolve().parents[2] / "apps/production/media/rdt/config-normalizer.yaml"
DEFAULTS = ("Gui", "Integrations", "Provider", "Watch")


def run_normalizer(db_path):
    script = yaml.safe_load(CONFIGMAP.read_text())["data"]["normalize-rdt.py"]
    script = script.replace("/data/db/rdtclient.db", db_path.as_posix())
    subprocess.run([sys.executable, "-I", "-c", script], check=True, capture_output=True)


class RdtNormalizerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "rdtclient.db"
        con = sqlite3.connect(self.db)
        con.executescript(
            """
            create table Settings (SettingId text primary key, Value text);
            create table Torrents (TorrentId text primary key, Error text);
            create table Downloads (DownloadId text primary key, TorrentId text);
            insert into Settings values ('Integrations:Default:ExcludeRegex', null);
            """
        )
        con.commit()
        con.close()
        run_normalizer(self.db)

    def tearDown(self):
        self.tmp.cleanup()

    def settings(self):
        con = sqlite3.connect(self.db)
        try:
            return dict(con.execute("select SettingId, Value from Settings"))
        finally:
            con.close()

    def test_every_default_excludes_junk_files(self):
        settings = self.settings()
        for default in DEFAULTS:
            self.assertTrue(settings[f"{default}:Default:ExcludeRegex"], default)
        self.assertEqual(len({settings[f"{d}:Default:ExcludeRegex"] for d in DEFAULTS}), 1)

    def test_regex_skips_release_junk_and_keeps_media(self):
        # rdt matches the regex against each file's path inside the torrent.
        regex = re.compile(self.settings()["Integrations:Default:ExcludeRegex"])
        season = "Rick and Morty - S09 Complete/Rick and Morty - S09E08 - Rickuiem Mort a Dream"
        for junk in (
            f"{season}/Downloaded From The Pirate Bay.txt",
            f"{season}/Downloaded From Extto.com.TXT",
            "Show.S01E01/Show.S01E01.nfo",
            "Show.S01E01/Visit Site.url",
            "Show.S01E01/poster.jpg",
        ):
            self.assertTrue(regex.search(junk), junk)
        for media in (
            f"{season}/Rick and Morty - S09E08 - Rickuiem Mort a Dream - 2160p HDR Ai Upscale -Mesc.mkv",
            "Show.S01E01/Show.S01E01.en.srt",
            "Show.S01E01/Subs/English.ass",
            "Movie.2020/Movie.2020.mp4",
            "Show.S01E01.txt.mkv",
        ):
            self.assertFalse(regex.search(media), media)

    def test_delete_on_error_stays_enabled(self):
        settings = self.settings()
        for default in DEFAULTS:
            self.assertEqual(settings[f"{default}:Default:DeleteOnError"], "1")


if __name__ == "__main__":
    unittest.main()
