#!/usr/bin/env bash
set -Eeuo pipefail

COUNT=$#

while test $# -gt 0; do
  case "$1" in
    -h|--help)
      echo "options:"
      echo "-h, --help                show brief help"
      echo "--cluster                 validate Flux cluster definitions"
      echo "--kustomize               validate kustomize overlays"
      exit 0
      ;;
    --cluster)
      CLUSTER=true
      shift
      ;;
    --kustomize)
      KUSTOMIZE=true
      shift
      ;;
    *)
      echo "Unknown option: $1" >&2
      exit 1
      ;;
  esac
done

if [ $COUNT -eq 0 ]; then
    CLUSTER=true
    KUSTOMIZE=true
fi

function error() {
  RED='\033[0;31m'
  NC='\033[0m' # No Color
  echo -e "${RED}ERROR${NC} - ${1}"
  exit 1
}

if [[ "${CLUSTER:-}" ]]; then
  echo "=== INFO - Validating clusters ==="
  if ! kubeconform -ignore-filename-pattern '.*[/\\]flux-system[/\\].*' -ignore-filename-pattern kustomization.yaml -schema-location default -schema-location 'https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json' -summary ./clusters; then
    error "unable to validate clusters"
  fi
fi

# The schema catalogs publish no CustomResourceDefinition schema; the CRDs
# come from upstream charts and Flux, so kubeconform skips them. The catalog's
# tuppr schemas predate the maintenance window field the chart supports.
SKIP_KINDS="CustomResourceDefinition,TalosUpgrade,KubernetesUpgrade"

# mirror kustomize-controller build options
kustomize_config="kustomization.yaml"

if [[ "${KUSTOMIZE:-}" ]]; then
  echo "=== INFO - Validating kustomize overlays ==="
  find . \( -path './.worktrees' -o -path './.claude' -o -path './.git' -o -path './tmp' \) -prune -o -type f -name "$kustomize_config" -print0 | while IFS= read -r -d $'\0' file;
    do
      echo "INFO - Validating kustomization ${file/%$kustomize_config}"
      if ! kustomize build "${file/%$kustomize_config}" | \
        kubeconform -skip "$SKIP_KINDS" -schema-location default -schema-location 'https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json' -summary; then
        error "unable to validate ${file}"
      fi
  done
fi
