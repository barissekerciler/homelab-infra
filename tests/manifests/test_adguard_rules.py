"""Every home-domain name reaches the cluster through one wildcard; only the Pi's names are listed."""

import pytest

from adguard_rules import CLUSTER, CONFIG, PI, resolve
from private_values import home

PI_NAMES = ["dns2", "nas", "router", "raspi", "ha", "vault", "cudy"]
CLIENTS = ["local", "tailscale"]


@pytest.mark.parametrize("client", CLIENTS)
@pytest.mark.parametrize("name", PI_NAMES)
def test_pi_names_answer_only_the_pi(name, client):
    assert resolve(home(name), client) == PI[client]


@pytest.mark.parametrize("client", CLIENTS)
@pytest.mark.parametrize("name", ["grafana", "openbao", "tv", "any-new-ingress", "xraspi", "alpha"])
def test_every_other_name_answers_both_nodes(name, client):
    assert resolve(home(name), client) == CLUSTER[client]


def test_no_cluster_host_name_is_listed():
    # The wildcard answers every cluster host; listing one here is a mistake.
    text = CONFIG.read_text(encoding="utf-8")
    for word in ("grafana", "openbao", "flux.home"):
        assert word not in text, word
