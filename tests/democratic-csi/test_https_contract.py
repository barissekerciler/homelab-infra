from pathlib import Path
import json
import unittest

import yaml
from private_values import home, need


ROOT = Path(__file__).resolve().parents[2] / "infrastructure" / "democratic-csi"


class DemocraticCsiHttpsContractTests(unittest.TestCase):
    def load_values(self, driver):
        document = yaml.safe_load(
            (ROOT / driver / "release.yaml").read_text(encoding="utf-8")
        )
        return document["spec"]["values"]

    def test_nfs_and_iscsi_use_sops_driver_configuration(self):
        for driver in ("nfs", "iscsi"):
            with self.subTest(driver=driver):
                values = self.load_values(driver)
                secret = yaml.safe_load(
                    (ROOT / driver / "driver-config-secret.yaml").read_text(encoding="utf-8")
                )
                self.assertEqual("Secret", secret["kind"])
                self.assertIn("sops", secret)
                self.assertEqual(
                    secret["metadata"]["name"], values["driver"]["existingConfigSecret"]
                )
                self.assertEqual("keep", secret["metadata"]["annotations"]["helm.sh/resource-policy"])
                self.assertTrue(secret["data"]["driver-config-file.yaml"].startswith("ENC[AES256_GCM,"))
                self.assertEqual(
                    {"driver": "freenas-api-" + driver}, values["driver"]["config"]
                )

    def test_nfs_and_iscsi_resolve_certificate_name_to_truenas_ip(self):
        expected_alias = {
            "ip": need("NAS_IP"),
            "hostnames": [home("nas")],
        }

        for driver in ("nfs", "iscsi"):
            with self.subTest(driver=driver):
                values = self.load_values(driver)
                for pod_kind in ("controller", "node"):
                    raw_aliases = values[pod_kind]["hostAliases"]
                    self.assertIsInstance(raw_aliases, str)
                    self.assertIn(expected_alias, json.loads(raw_aliases))
                    rendered = yaml.safe_load(f"hostAliases: {raw_aliases}\n")
                    self.assertIn(expected_alias, rendered["hostAliases"])

    def test_no_plaintext_sops_work_files_remain(self):
        self.assertEqual([], list(ROOT.rglob(".decrypted*")))


if __name__ == "__main__":
    unittest.main()
