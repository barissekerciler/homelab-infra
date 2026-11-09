// Checks for every branch of this repository and every pull request opened
// from one (pull requests from forks are never built): kustomize build +
// kubeconform of every overlay, the repository's rules (tests/), and flate,
// which renders every Kustomization and HelmRelease the way Flux would, so a
// chart bump whose templates or values break fails here. The job is defined
// in apps/production/jenkins/jcasc.yaml. The pod gets no ServiceAccount
// token: it needs nothing from the cluster.
pipeline {
    agent {
        kubernetes {
            defaultContainer 'tools'
            yaml '''
apiVersion: v1
kind: Pod
spec:
  restartPolicy: Never
  automountServiceAccountToken: false
  securityContext:
    runAsNonRoot: true
    runAsUser: 1000
    runAsGroup: 1000
    fsGroup: 1000
  containers:
    - name: jnlp
      resources:
        requests:
          cpu: 5m
          memory: 256Mi
        limits:
          memory: 512Mi
    - name: tools
      image: ghcr.io/astral-sh/uv:0.13.7-python3.13-trixie
      command: [sleep, infinity]
      env:
        - name: HOME
          value: /tmp
        - name: KUSTOMIZE_VERSION
          value: v5.8.3
        - name: KUBECONFORM_VERSION
          value: v0.8.0
        - name: FLATE_VERSION
          value: v0.7.1
      resources:
        requests:
          cpu: 100m
          memory: 512Mi
        limits:
          memory: 3Gi
'''
        }
    }
    options {
        timeout(time: 30, unit: 'MINUTES')
        disableConcurrentBuilds(abortPrevious: true)
        buildDiscarder(logRotator(numToKeepStr: '30'))
        timestamps()
    }
    stages {
        stage('Tools') {
            steps {
                // Release binaries, each checked against its published SHA-256.
                sh '''#!/bin/bash
set -euo pipefail
mkdir -p /tmp/bin /tmp/dl && cd /tmp/dl
fetch() { # release URL, archive, expected sha256, binary in the archive
    curl -fsSL --retry 3 -o "$2" "$1/$2"
    echo "$3  $2" | sha256sum -c -
    tar -xzf "$2" -C /tmp/bin "$4"
}
gh=https://github.com
k=$gh/kubernetes-sigs/kustomize/releases/download/kustomize%2F$KUSTOMIZE_VERSION
f=kustomize_${KUSTOMIZE_VERSION}_linux_amd64.tar.gz
fetch "$k" "$f" "$(curl -fsSL "$k/checksums.txt" | awk -v f="$f" '$2 == f { print $1 }')" kustomize
c=$gh/yannh/kubeconform/releases/download/$KUBECONFORM_VERSION
f=kubeconform-linux-amd64.tar.gz
fetch "$c" "$f" "$(curl -fsSL "$c/CHECKSUMS" | awk -v f="$f" '$2 == f { print $1 }')" kubeconform
l=$gh/home-operations/flate/releases/download/$FLATE_VERSION
f=flate_${FLATE_VERSION#v}_linux_amd64.tar.gz
fetch "$l" "$f" "$(curl -fsSL "$l/$f.sha256" | awk '{ print $1 }')" flate
/tmp/bin/kustomize version && /tmp/bin/kubeconform -v && /tmp/bin/flate --version
'''
            }
        }
        stage('Validate') {
            steps {
                sh 'PATH=/tmp/bin:$PATH bash scripts/validate.sh'
            }
        }
        stage('Tests') {
            steps {
                // Tests that need private values from .env are skipped here.
                sh 'PATH=/tmp/bin:$PATH uv run --no-project --with pytest --with pyyaml python -m pytest tests -p no:cacheprovider'
            }
        }
        stage('Render') {
            steps {
                // Secrets come from OpenBao and the tenant's repository is
                // private, so flate skips what needs them. Helm renders for
                // the Kubernetes version tuppr keeps the nodes on.
                sh '''#!/bin/bash
set -euo pipefail
kube=$(sed -n 's/^ *version: \\(v[0-9.]*\\).*/\\1/p' infrastructure/tuppr/upgrades/kubernetes.yaml)
/tmp/bin/flate test --allow-missing-kinds Secret --kube-version "$kube" --no-progress
'''
            }
        }
        stage('Diff') {
            when { changeRequest() }
            steps {
                // What the pull request changes in the rendered cluster, for
                // review in this log; it never fails the build.
                catchError(buildResult: 'SUCCESS', stageResult: 'UNSTABLE') {
                    sh '''#!/bin/bash
set -euo pipefail
kube=$(sed -n 's/^ *version: \\(v[0-9.]*\\).*/\\1/p' infrastructure/tuppr/upgrades/kubernetes.yaml)
/tmp/bin/flate diff --base "origin/$CHANGE_TARGET" --allow-missing-kinds Secret --kube-version "$kube" --no-progress
'''
                }
            }
        }
    }
}
