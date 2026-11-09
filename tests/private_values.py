"""Private values for the tests: LAN addresses, host names, NAS paths and
account names stay out of Git.

They are read from `.env` at the repository root (not committed; `.env.example`
lists the keys), and the environment overrides them. A test that needs a value
that is not set is skipped, so the suite runs without one.
"""

import os
from functools import lru_cache
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
KEYS = ("DOMAIN", "HOMELAB_IP", "HOMELAB2_IP", "PI_IP", "NAS_IP", "CAMERA_IP", "ROUTER_DNAT_IP",
        "IOT_SUBNET", "TAILSCALE_HOMELAB_IP", "TAILSCALE_HOMELAB2_IP", "TAILSCALE_PI_IP",
        "NAS_POOL", "GITHUB_USER", "JENKINS_HOME_PV")


@lru_cache
def values():
    found = {}
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                # An empty value followed by a comment, or a trailing " # comment".
                value = "" if value.lstrip().startswith("#") else value.split(" #", 1)[0]
                found[key.strip()] = value.strip().strip("\"'")
    found.update({key: os.environ[key] for key in KEYS if os.environ.get(key)})
    return found


def need(key):
    """The value of `key`, or skip the calling test when it is not set."""
    if key not in KEYS:
        raise KeyError(f"{key} is not a known private value; add it to KEYS and .env.example")
    value = values().get(key)
    if not value:
        pytest.skip(f"{key} is not set (.env)")
    return value


def home(name=None):
    """A host name under the home domain: home('grafana') -> grafana.home.<DOMAIN>."""
    domain = f"home.{need('DOMAIN')}"
    return f"{name}.{domain}" if name else domain


def oauth2_proxy_namespace():
    return "oauth2-proxy-" + home().replace(".", "-")


def oauth2_proxy_url():
    """The forwardAuth address of the home domain's oauth2-proxy."""
    return f"http://oauth2-proxy.{oauth2_proxy_namespace()}.svc.cluster.local/"
