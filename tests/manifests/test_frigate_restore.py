"""The restore scripts decrypt the camera's off-site copy with nothing from the house."""

from pathlib import Path
import re
import shutil
import subprocess

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
FRIGATE = REPO_ROOT / "apps" / "production" / "frigate"
RESTORE = FRIGATE / "restore"
MAIN_HOST = "[*.your-storagebox.de]:23"


def _keys(text):
    """(type, key) pairs of a known_hosts text, without host names."""
    return sorted(tuple(line.split()[1:3]) for line in text.splitlines() if line and not line.startswith("#"))


def test_known_hosts_pin_the_main_host_with_the_uploaders_keys():
    pinned = (RESTORE / "known_hosts").read_text(encoding="utf-8")
    assert {line.split()[0] for line in pinned.splitlines() if line} == {MAIN_HOST}
    configmap = next(d for d in yaml.safe_load_all((FRIGATE / "uploader-configmap.yaml").read_text(encoding="utf-8")) if d)
    # Same server as the uploader's: the host keys it pins, checked against a live scan.
    assert _keys(pinned) == _keys(configmap["data"]["known_hosts"])


@pytest.mark.parametrize("name", ["restore.ps1", "restore.sh"])
def test_scripts_keep_secrets_off_disk(name):
    script = (RESTORE / name).read_text(encoding="utf-8")
    # Remotes only in the environment; never an rclone.conf with the obscured password.
    assert "config create" not in script
    assert "RCLONE_CONFIG_CAM_REMOTE" in script and "hz:frigate/data" in script
    assert "RCLONE_CONFIG_HZ_KNOWN_HOSTS_FILE" in script
    # The box name comes from the caller, never from the script.
    assert ".your-storagebox.de" in script
    assert set(re.findall(r"\bu\d{6}\b", script)) == {"u123456"}  # only the usage example
    assert "obscure" in script
    # Hidden prompts for the crypt password and salt.
    hidden = "-AsSecureString" if name.endswith(".ps1") else "read -rs"
    assert script.count(hidden) >= 2
    assert "ENC[" not in script and "PRIVATE KEY" not in script


def test_bash_script_parses():
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("bash not installed")
    subprocess.run([bash, "-n", str(RESTORE / "restore.sh")], check=True)


def test_powershell_script_parses():
    shell = shutil.which("pwsh") or shutil.which("powershell")
    if not shell:
        pytest.skip("PowerShell not installed")
    check = (
        "$e = $null; [void][System.Management.Automation.Language.Parser]::ParseFile("
        f"'{RESTORE / 'restore.ps1'}', [ref]$null, [ref]$e); if ($e) {{ $e | ForEach-Object Message; exit 1 }}"
    )
    subprocess.run([shell, "-NoProfile", "-Command", check], check=True)
