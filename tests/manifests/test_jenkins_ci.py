"""homelab-infra's checks run on Jenkins: never for forks, without cluster access."""

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
JENKINSFILE = (ROOT / "Jenkinsfile").read_text(encoding="utf-8")


def job_script():
    jcasc = yaml.safe_load((ROOT / "apps/production/jenkins/jcasc.yaml").read_text(encoding="utf-8"))
    [job] = jcasc["jobs"]
    return job["script"]


def pod():
    [spec] = re.findall(r"yaml '''\n(.*?)'''", JENKINSFILE, re.S)
    return yaml.safe_load(spec)


def test_the_job_builds_this_repository_and_never_a_fork():
    script = job_script()
    assert "repository('homelab-infra')" in script
    assert "credentialsId('github-app')" in script
    assert "scriptPath('Jenkinsfile')" in script
    assert "gitHubPullRequestDiscovery" in script
    # The repository is public: a fork's Jenkinsfile would run in the cluster.
    assert "gitHubForkDiscovery" not in script and "ForkPullRequestDiscovery" not in script


def test_jenkins_has_the_job_dsl_plugin():
    release = yaml.safe_load((ROOT / "apps/production/jenkins/release.yaml").read_text(encoding="utf-8"))
    plugins = release["spec"]["values"]["controller"]["additionalPlugins"]
    assert any(plugin.startswith("job-dsl:") for plugin in plugins)


def test_the_pod_has_no_cluster_access():
    spec = pod()["spec"]
    assert spec["automountServiceAccountToken"] is False
    assert "serviceAccountName" not in spec
    assert spec["securityContext"]["runAsNonRoot"] is True