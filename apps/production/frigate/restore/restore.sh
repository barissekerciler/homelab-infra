#!/usr/bin/env bash
# Restore camera recordings from the Hetzner Storage Box (Linux, macOS, Git Bash).
#
# Needs only rclone, your Storage Box SSH key in an SSH agent (Bitwarden) or the main
# account's password, and the crypt password and salt from Bitwarden. Nothing secret is
# written to disk: the rclone remotes exist only in this process.
#
# STORAGE_BOX is the Storage Box main account (u123456, from the password manager).
#
#   STORAGE_BOX=u123456 ./restore.sh  # list the days
#   ./restore.sh 2026-10-16           # one day
#   ./restore.sh 2026-10-16 08        # one hour
#   BOX_PASSWORD=1 ./restore.sh ...   # log in with the main account's password instead
#   CHECK_ONLY=1 ./restore.sh         # only test the Storage Box login
#   DEST=/some/dir ./restore.sh ...   # default: ./camera-restore
#
# Days and hours are UTC. Recordings are 10 s MP4 segments under
# recordings/<day>/<hour>/living_room/; play them with VLC or ffplay. The person
# snapshots are in cam:clips (named by Unix time); see README.md for copying them.
set -euo pipefail

day=${1:-}
hour=${2:-}
dest=${DEST:-./camera-restore}
here=$(cd "$(dirname "$0")" && pwd)
command -v rclone >/dev/null || { echo "rclone not found: https://rclone.org/install/" >&2; exit 1; }
[[ -z $day || $day =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]] || { echo "day must look like 2026-10-16" >&2; exit 1; }
[[ -z $hour || $hour =~ ^[0-9]{2}$ ]] || { echo "hour must look like 08" >&2; exit 1; }

# Git Bash runs the Windows rclone, which wants Windows paths and NUL.
known_hosts=$here/known_hosts
no_config=/dev/null
if command -v cygpath >/dev/null; then
  known_hosts=$(cygpath -w "$known_hosts")
  no_config=NUL
fi
rc() { rclone --config "$no_config" "$@"; }

# The Storage Box main account; the host keys are pinned next to this script.
box=${STORAGE_BOX:?set STORAGE_BOX to the Storage Box main account, e.g. u123456}
[[ $box =~ ^u[0-9]+$ ]] || { echo "STORAGE_BOX must look like u123456" >&2; exit 1; }
export RCLONE_CONFIG_HZ_TYPE=sftp
export RCLONE_CONFIG_HZ_HOST=$box.your-storagebox.de
export RCLONE_CONFIG_HZ_USER=$box
export RCLONE_CONFIG_HZ_PORT=23
export RCLONE_CONFIG_HZ_KNOWN_HOSTS_FILE=$known_hosts
if [[ -n ${BOX_PASSWORD:-} ]]; then
  read -rsp "Storage Box main account password: " box_pass; echo
  RCLONE_CONFIG_HZ_PASS=$(rclone obscure "$box_pass"); export RCLONE_CONFIG_HZ_PASS
  unset box_pass
else
  export RCLONE_CONFIG_HZ_KEY_USE_AGENT=true
fi

if [[ -n ${CHECK_ONLY:-} ]]; then
  rc lsf hz:frigate --dirs-only
  echo "RESTORE-CHECK-OK: the Storage Box answers and frigate/ is there."
  exit 0
fi

echo "Paste the crypt password and the salt from Bitwarden. Nothing is shown while you paste."
read -rsp "Crypt password (CRYPT_PASSWORD): " crypt_pass; echo
read -rsp "Salt (CRYPT_PASSWORD2): " crypt_salt; echo
# The crypt layer over frigate/data; rclone wants both values obscured.
export RCLONE_CONFIG_CAM_TYPE=crypt
export RCLONE_CONFIG_CAM_REMOTE=hz:frigate/data
RCLONE_CONFIG_CAM_PASSWORD=$(rclone obscure "$crypt_pass"); export RCLONE_CONFIG_CAM_PASSWORD
RCLONE_CONFIG_CAM_PASSWORD2=$(rclone obscure "$crypt_salt"); export RCLONE_CONFIG_CAM_PASSWORD2
unset crypt_pass crypt_salt

days=$(rc lsf cam:recordings --dirs-only 2>/dev/null || true)
[[ -n $days ]] || { echo "No readable folders: the password or the salt is wrong." >&2; exit 1; }
if [[ -z $day ]]; then
  echo "Days on the Storage Box (UTC). Run again with: $0 <day> [<HH>]"
  sed 's#/$##; s#^#  #' <<<"$days"
  exit 0
fi

path=recordings/$day${hour:+/$hour}
echo "Downloading $path to $dest/$path ..."
rc copy "cam:$path" "$dest/$path" --stats-one-line --stats 5s
echo "RESTORE-DONE: $(find "$dest/$path" -type f | wc -l) recordings in $dest/$path"
