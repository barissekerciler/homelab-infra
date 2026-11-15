"""homelab-infra's and the tenant's checks run on Jenkins: never for forks, without cluster access."""

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
JENKINSFILE = (ROOT / "Jenkinsfile").read_text(encoding="utf-8")


def job_scripts():
    jcasc = yaml.safe_load((ROOT / "apps/production/jenkins/jcasc.yaml").read_text(encoding="utf-8"))
    return {re.search(r"multibranchPipelineJob\('([^']+)'\)", job["script"]).group(1): job["script"]
            for job in jcasc["jobs"]}


def pod():
    [spec] = re.findall(r"yaml '''\n(.*?)'''", JENKINSFILE, re.S)
    return yaml.safe_load(spec)


def test_each_job_builds_its_own_repository_and_never_a_fork():
    scripts = job_scripts()
    assert set(scripts) == {"homelab-infra", "homelab-private"}
    for name, script in scripts.items():
        assert f"repository('{name}')" in script
        assert "credentialsId('github-app')" in script
        assert "scriptPath('Jenkinsfile')" in script
        assert "gitHubPullRequestDiscovery" in script
        # A fork's Jenkinsfile would run in the cluster.
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