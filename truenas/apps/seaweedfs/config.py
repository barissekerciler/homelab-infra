"""SeaweedFS's two secret files on the NAS, written from OpenBao.

security.toml holds the JWT keys, s3.json the S3 identities: an admin key and
one key per bucket that may only touch its own bucket. Both go into
/mnt/pool1/apps/seaweedfs/config (owner 1000:1000, mode 600), which the app
mounts read-only at /etc/seaweedfs (README.md).

    python truenas/apps/seaweedfs/config.py

Needs the OpenBao CLI signed in (BAO_ADDR, BAO_TOKEN; AGENTS.md) and
`ssh truenas` as a TrueNAS admin. The values go over SSH on stdin and are never
printed. Restart the app afterwards (Apps > seaweedfs > Restart).
"""

import base64
import json
import subprocess
import sys

# A bucket and its key in OpenBao (KV v2), shared with the bucket's one
# consumer in the cluster.
BUCKETS = {
    "cnpg-backups": "shared/cnpg-backups-s3",
    "openbao-snapshots": "shared/openbao-snapshots-s3",
}
ADMIN_KEY = "seaweedfs/seaweedfs-s3-config"
JWT_KEYS = "seaweedfs/seaweedfs-security"
DATASET = "/mnt/pool1/apps/seaweedfs"
OWNER = 1000

# Runs on the NAS (python3 -, DATA filled in): hand the folders to the SSH
# user, write both files, give them back to SeaweedFS's user.
REMOTE = """
import base64, json, os, subprocess
files = json.loads(base64.b64decode(DATA))
root, config = ROOT, ROOT + "/config"
def chown(path, uid, gid, recursive):
    job = {"path": path, "uid": uid, "gid": gid, "options": {"recursive": recursive}}
    subprocess.run(["midclt", "call", "-j", "filesystem.chown", json.dumps(job)],
                   check=True, stdout=subprocess.DEVNULL)
chown(root, os.getuid(), os.getgid(), False)
chown(config, os.getuid(), os.getgid(), True)
try:
    os.umask(0o077)
    for name, text in files.items():
        with open(f"{config}/.{name}.new", "w") as f:
            f.write(text)
        os.replace(f"{config}/.{name}.new", f"{config}/{name}")
finally:
    chown(config, OWNER, OWNER, True)
    chown(root, OWNER, OWNER, False)
print("wrote", ", ".join(sorted(files)))
"""


def bao(path):
    out = subprocess.run(["bao", "kv", "get", "-format=json", "-mount=secret", path],
                         check=True, capture_output=True).stdout
    return json.loads(out)["data"]["data"]


def security_toml(keys):
    def section(name, key, seconds):
        value = keys[key]
        if not value or '"' in value or "\\" in value:
            raise ValueError(f"unusable value for {key}")
        return f'[{name}]\nkey = "{value}"\nexpires_after_seconds = {seconds}\n'
    return "\n".join([
        "# Values: OpenBao secret/" + JWT_KEYS + " (truenas/apps/seaweedfs/config.py).",
        "# Volume writes and reads: the master signs, the volume server checks.",
        section("jwt.signing", "jwt_signing_key", 10),
        section("jwt.signing.read", "jwt_signing_read_key", 60),
        "# The filer's HTTP writes and reads.",
        section("jwt.filer_signing", "jwt_filer_signing_key", 10),
        section("jwt.filer_signing.read", "jwt_filer_signing_read_key", 60),
    ])


def s3_json(admin, buckets):
    identities = [{
        "name": "admin",
        "credentials": [{"accessKey": admin["admin_access_key_id"],
                         "secretKey": admin["admin_secret_access_key"]}],
        "actions": ["Admin", "Read", "List", "Tagging", "Write"],
    }]
    for bucket, key in sorted(buckets.items()):
        identities.append({
            "name": bucket,
            "credentials": [{"accessKey": key["AWS_ACCESS_KEY_ID"],
                             "secretKey": key["AWS_SECRET_ACCESS_KEY"]}],
            "actions": [f"{action}:{bucket}" for action in ("Read", "List", "Write", "Tagging")],
        })
    for identity in identities:
        if not all(identity["credentials"][0].values()):
            raise ValueError(f"empty key for {identity['name']}")
    return json.dumps({"identities": identities}, indent=2) + "\n"


def main():
    files = {
        "security.toml": security_toml(bao(JWT_KEYS)),
        "s3.json": s3_json(bao(ADMIN_KEY), {bucket: bao(path) for bucket, path in BUCKETS.items()}),
    }
    data = base64.b64encode(json.dumps(files).encode()).decode()
    script = f"DATA = {data!r}\nROOT = {DATASET!r}\nOWNER = {OWNER}\n" + REMOTE
    subprocess.run(["ssh", "truenas", "python3", "-"], input=script.encode(), check=True)


if __name__ == "__main__":
    sys.exit(main())
