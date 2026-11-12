"""What AdGuard answers for an A query, worked out from the user rules in Git.

Covers the rule forms the config uses: `||domain^` and `host^` patterns with
$client, $dnstype, $denyallow and $dnsrewrite=NOERROR;A;<ip>. Like AdGuard,
the A records of every matching rule add up.
"""

from pathlib import Path

import yaml
from private_values import need

CONFIG = Path(__file__).resolve().parents[2] / "infrastructure/adguardhome/adguardhome-config.yaml"
# The config is a template: the ExternalSecret adguardhome-config fills in the
# Tailscale addresses from OpenBao. The tests take them from .env.
TAILSCALE = {"tailscale_homelab": "TAILSCALE_HOMELAB_IP", "tailscale_homelab2": "TAILSCALE_HOMELAB2_IP",
             "tailscale_pi": "TAILSCALE_PI_IP"}


class _Answers:
    """The A records a client group gets; every address comes from .env."""

    def __init__(self, lan_keys, tailscale_keys):
        self.keys = {"local": lan_keys, "tailscale": tailscale_keys}

    def __getitem__(self, client):
        return {need(key) for key in self.keys[client]}


# Traefik on every node; homelab3 has no Tailscale address yet.
CLUSTER = _Answers(("HOMELAB_IP", "HOMELAB2_IP", "HOMELAB3_IP"), ("TAILSCALE_HOMELAB_IP", "TAILSCALE_HOMELAB2_IP"))
# Caddy on the Pi.
PI = _Answers(("PI_IP",), ("TAILSCALE_PI_IP",))


def rendered():
    text = CONFIG.read_text(encoding="utf-8")
    for name, key in TAILSCALE.items():
        text = text.replace("{{ ." + name + " }}", need(key))
    return yaml.safe_load(text)


def rules():
    return rendered()["user_rules"]


def _covers(domain, name):
    return name == domain or name.endswith("." + domain)


def resolve(name, client):
    answers = set()
    for rule in rules():
        if rule.startswith(("!", "@@")) or "$" not in rule:
            continue
        pattern, _, modifiers = rule.partition("$")
        mods = dict((item.split("=", 1) + [""])[:2] for item in modifiers.split(","))
        domain = pattern.removeprefix("||").removesuffix("^")
        if not (_covers(domain, name) if pattern.startswith("||") else name == domain):
            continue
        if mods.get("client", "").strip("'\"") not in ("", client):
            continue
        if mods.get("dnstype", "A") != "A":
            continue
        if any(_covers(excluded, name) for excluded in mods.get("denyallow", "").split("|") if excluded):
            continue
        rcode, _, rest = mods.get("dnsrewrite", "").partition(";")
        record, _, value = rest.partition(";")
        if rcode == "NOERROR" and record == "A":
            answers.add(value)
    return answers
