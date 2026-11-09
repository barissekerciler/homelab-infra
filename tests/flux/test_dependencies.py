import re
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
ROOT = REPO_ROOT / "clusters" / "homelab"


def manifest_document(filename, resource_name):
    manifest = (ROOT / filename).read_text(encoding="utf-8")
    for document in re.split(r"^---\s*$", manifest, flags=re.MULTILINE):
        if re.search(
            rf"^  name: {re.escape(resource_name)}$",
            document,
            flags=re.MULTILINE,
        ):
            return document
    raise AssertionError(f"{resource_name} not found in {filename}")


def dependency_names(filename, resource_name):
    manifest = manifest_document(filename, resource_name)
    block = re.search(r"^  dependsOn:\n((?:    - name: .+\n)+)", manifest, re.MULTILINE)
    if not block:
        return []
    return re.findall(r"^    - name: (.+)$", block.group(1), re.MULTILINE)


def field_value(filename, resource_name, field):
    text = manifest_document(filename, resource_name)
    match = re.search(rf"^  {re.escape(field)}: (.+)$", text, re.MULTILINE)
    if not match:
        raise AssertionError(f"{field} not found for {resource_name} in {filename}")
    return match.group(1)


class FluxDependencyGraphTest(unittest.TestCase):
    def test_prod_depends_on_infrastructure_and_the_shared_postgresql(self):
        self.assertEqual(
            dependency_names("apps.yaml", "prod"),
            ["infrastructure", "postgresql"],
        )

    def test_shared_postgresql_is_applied_once_before_both_clusters(self):
        # apps/base/postgresql: the namespace, password store and superuser
        # both clusters use. Only its own Kustomization applies it; the
        # overlays take nothing but the Cluster template from the base.
        self.assertEqual(dependency_names("apps.yaml", "postgresql"), ["infrastructure"])
        self.assertEqual(field_value("apps.yaml", "postgresql", "path"), "./apps/base/postgresql")
        for overlay in ("production", "dev"):
            text = (REPO_ROOT / "apps" / overlay / "postgresql" / "kustomization.yaml").read_text(
                encoding="utf-8"
            )
            self.assertIn("  - ../../base/postgresql/cluster\n", text)
            self.assertNotRegex(text, r"(?m)^  - \.\./\.\./base/postgresql$")

    def test_infrastructure_waits_for_sources_and_cert_issuers(self):
        self.assertEqual(
            dependency_names("infrastructure.yaml", "infrastructure"),
            ["sources", "cert-issuers"],
        )

    def test_cert_issuers_wait_for_cert_manager_and_external_secrets(self):
        self.assertEqual(
            dependency_names("certs.yaml", "cert-issuers"),
            ["cert-manager", "external-secrets"],
        )

    def test_external_secrets_waits_only_for_sources(self):
        self.assertEqual(
            dependency_names("external-secrets.yaml", "external-secrets"),
            ["sources"],
        )

    def test_infrastructure_configs_wait_for_infrastructure(self):
        self.assertEqual(
            dependency_names("infrastructure.yaml", "infrastructure-configs"),
            ["infrastructure"],
        )

    def test_dev_depends_on_infrastructure_and_the_shared_postgresql(self):
        self.assertEqual(
            dependency_names("apps.yaml", "dev"),
            ["infrastructure", "postgresql"],
        )

    def test_democratic_csi_depends_on_general_infrastructure(self):
        self.assertEqual(
            dependency_names("democratic-csi.yaml", "democratic-csi"),
            ["infrastructure"],
        )

    def test_prod_applies_without_waiting_for_all_workloads(self):
        text = manifest_document("apps.yaml", "prod")
        self.assertNotRegex(text, r"(?m)^  wait: true$")

    def test_infrastructure_has_single_owners_for_sources_and_cert_manager(self):
        text = (REPO_ROOT / "infrastructure" / "kustomization.yaml").read_text(
            encoding="utf-8"
        )
        self.assertNotRegex(text, r"(?m)^\s+- sources$")
        self.assertNotRegex(text, r"(?m)^\s+- cert-manager$")

    def test_health_waits_stay_on_stateful_boundaries(self):
        self.assertEqual(field_value("democratic-csi.yaml", "democratic-csi", "wait"), "true")

    def test_media_apps_are_part_of_prod(self):
        self.assertFalse((REPO_ROOT / "clusters" / "homelab" / "media-truenas.yaml").exists())
        self.assertFalse((REPO_ROOT / "apps" / "production" / "media-truenas").exists())


if __name__ == "__main__":
    unittest.main()
