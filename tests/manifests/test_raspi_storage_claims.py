import re
import subprocess
import unittest
from pathlib import Path
from private_values import need


REPO_ROOT = Path(__file__).resolve().parents[2]
ROOT = REPO_ROOT / "apps" / "production"


def rendered_document(overlay, kind, name):
    result = subprocess.run(
        ["kustomize", "build", str(ROOT / overlay)],
        check=True,
        capture_output=True,
        text=True,
    )
    for document in re.split(r"^---\s*$", result.stdout, flags=re.MULTILINE):
        if re.search(rf"^kind: {re.escape(kind)}$", document, re.MULTILINE) and re.search(
            rf"^  name: {re.escape(name)}$", document, re.MULTILINE
        ):
            return document
    raise AssertionError(f"{kind}/{name} not found in {overlay}")


class LocalStorageClaimsTest(unittest.TestCase):
    def test_jenkins_uses_the_iscsi_home_on_any_node(self):
        text = (ROOT / "jenkins" / "release.yaml").read_text(encoding="utf-8")

        self.assertIn("existingClaim: jenkins-home", text)
        self.assertNotIn("kubernetes.io/hostname:", text)

    def test_jenkins_home_claim_prebinds_retained_iscsi_pv(self):
        claim = rendered_document("jenkins", "PersistentVolumeClaim", "jenkins-home")

        self.assertRegex(claim, r"(?m)^  storageClassName: freenas-iscsi-csi$")
        self.assertRegex(
            claim,
            rf"(?m)^  volumeName: {re.escape(need('JENKINS_HOME_PV'))}$",
        )

if __name__ == "__main__":
    unittest.main()
