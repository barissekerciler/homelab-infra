import os
import shutil
import subprocess
from pathlib import Path

import pytest


SCRIPT = Path(__file__).parents[2] / "scripts" / "validate.sh"


def _usable_bash() -> str:
    candidates = [shutil.which("bash")]
    if os.name == "nt":
        candidates.extend(
            [
                r"C:\Program Files\Git\bin\bash.exe",
                r"C:\Program Files\Git\usr\bin\bash.exe",
            ]
        )
    for candidate in candidates:
        if not candidate:
            continue
        probe = subprocess.run(
            [candidate, "-c", "exit 0"], capture_output=True, check=False
        )
        if probe.returncode == 0:
            return candidate
    pytest.skip("a usable bash is required to run validate.sh")


def _write_stub(path: Path, body: str) -> None:
    path.write_text(f"#!/usr/bin/env bash\nset -u\n{body}\n", encoding="utf-8")
    path.chmod(0o755)


def _run_validate(
    tmp_path: Path,
    args: list[str],
    *,
    kustomize_status: int = 0,
    kubeconform_status: int = 0,
) -> subprocess.CompletedProcess[str]:
    bash = _usable_bash()

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_stub(
        bin_dir / "kustomize",
        """
if [ "${KUSTOMIZE_STATUS:-0}" -ne 0 ]; then
  exit "$KUSTOMIZE_STATUS"
fi
cat <<'YAML'
apiVersion: v1
kind: ConfigMap
metadata:
  name: fixture
YAML
""",
    )
    _write_stub(
        bin_dir / "kubeconform",
        """
cat >/dev/null
exit "${KUBECONFORM_STATUS:-0}"
""",
    )

    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    env["KUSTOMIZE_STATUS"] = str(kustomize_status)
    env["KUBECONFORM_STATUS"] = str(kubeconform_status)
    return subprocess.run(
        [bash, str(SCRIPT), *args],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


@pytest.mark.parametrize(
    ("kustomize_status", "kubeconform_status"),
    [(1, 0), (0, 1)],
)
def test_kustomize_pipeline_propagates_either_stage_failure(
    tmp_path: Path, kustomize_status: int, kubeconform_status: int
) -> None:
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "kustomization.yaml").write_text(
        "resources: []\n", encoding="utf-8"
    )

    result = _run_validate(
        tmp_path,
        ["--kustomize"],
        kustomize_status=kustomize_status,
        kubeconform_status=kubeconform_status,
    )

    assert result.returncode != 0


def test_unknown_option_fails(tmp_path: Path) -> None:
    result = _run_validate(tmp_path, ["--unknown"])
    assert result.returncode != 0
    assert "Unknown option" in result.stderr
