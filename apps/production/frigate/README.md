# Frigate: living room camera

Frigate records the living room camera (Frigate name `living_room`) on any node (it prefers `homelab`). Every recording leaves the house within
seconds, encrypted, to a Hetzner Storage Box, and a plain copy goes to the NAS.

## What runs where

| Piece | Where | What it does |
|---|---|---|
| Camera SP1 (eufy T8410) | Cudy IoT network, 10.1.0.174, powered by the Meross plug "Cam Socket" | RTSP, 1080p at 15 fps, no internet by default |
| `frigate` container | Deployment `frigate`, any node | Reads the camera through the Cudy DNAT `10.0.0.224:8554`, detects people on the node's iGPU with YOLOv9-c (built at every new pod from the official weights by the `export-model` init container, a few minutes; the bundled SSDLite if that fails), keeps motion recordings and snapshots for 30 days in an emptyDir on the node's own disk. A new pod starts empty; the copies below keep the history. UI at `https://cam.home.bariss.dev` behind the GitHub sign-in. |
| `uploader` sidecar | same pod | rclone: every 5 s the new files go through `crypt` to the Storage Box `frigate/data`; after each good run it writes `frigate/status/heartbeat` |
| `nas-push` sidecar | same pod | rclone: every 5 s the new files go, as they are, to the NAS mirror over WebDAV |
| `frigate-nas-mirror` | its own Deployment, any node | `rclone serve webdav` onto the NAS share over NFS; only the Frigate pod may connect; deletes files older than 30 days |
| `frigate-cleanup` | CronJob, 03:30 Europe/Berlin | Deletes off-site files older than 30 days |
| Mosquitto | Pi 10.0.0.87:1883 | MQTT between Frigate and Home Assistant; user `frigate` may use only `frigate/#` |
| Home Assistant | Pi (`packages/camera.yaml` in its config) | Arms (plug on, detection on) and disarms (plug off) through the UI presence automations and Good Night, sends the person alert with a photo, warns about stale uploads and a dark camera, and has the internet toggles. The "Camera" page shows the live view (`camera.living_room_camera`, with sound in the recordings, not in the live view); `require_admin` only hides it from the sidebar, any HA user can still open the camera. |
| restream | host port `8554` behind `cam.home.bariss.dev` (every node; those without Frigate refuse) | go2rtc's RTSP restream for Home Assistant's live view. go2rtc asks for the restream password and the NetworkPolicy lets only the Pi in. |

Secrets: `frigate-secrets` (camera RTSP, MQTT and the restream login) is for Frigate. `frigate-uploader-secrets` (the
Storage Box key and the obscured crypt password and salt) is for the uploader and the cleanup
job only.

The Storage Box (`<box>`, its account name, is in the password manager and in
OpenBao `secret/frigate/frigate-uploader-secrets`):

- `frigate/` is sub-account `<box>-sub1`, read-write, used by the uploader key.
  - `data/` holds the rclone crypt files (contents and names encrypted).
  - `status/heartbeat` holds the epoch of the last good upload run, in plain text.
- `frigate/status/` is sub-account `<box>-sub2`, read-only, used by Home Assistant's key.

## Restore from Hetzner after the house is lost

You need a computer with rclone and the escrowed crypt password and salt in plain text.
Nothing from the house is needed.

**Scripts:** [`restore/restore.ps1`](restore/restore.ps1) (Windows) and
[`restore/restore.sh`](restore/restore.sh) (Linux, macOS, Git Bash) do steps 2 to 4. They log in
with your SSH key from an SSH agent (Bitwarden) or, with `-BoxPassword` / `BOX_PASSWORD=1`, with
the main account's password, ask for the crypt password and salt without echoing them, and keep
the rclone remotes in the process environment only. The Storage Box host keys are pinned in
[`restore/known_hosts`](restore/known_hosts). Without a day they list the days; with a day (and
an hour) they download it:

```powershell
winget install Rclone.Rclone
powershell -ExecutionPolicy Bypass -File restore\restore.ps1 -StorageBox <box> -Day 2026-10-16 -Hour 08
```

The manual steps:

1. **Get into the main account.** Sign in to the Hetzner Console
   (`https://console.hetzner.com`, project `homelab-backup`, `storage-box-1`). The main
   account has no password; it logs in with your own SSH key ("hetzner homelab backup" in
   the password manager). Without that key, reset the main account's password in the
   Console.
2. **SFTP remote** to `<box>@<box>.your-storagebox.de:23`:

   ```sh
   # With the SSH key loaded in ssh-agent:
   rclone config create hetzner sftp host=<box>.your-storagebox.de user=<box> port=23 key_use_agent=true
   # Or with the reset password:
   read -rs BOX_PASSWORD
   rclone config create hetzner sftp host=<box>.your-storagebox.de user=<box> port=23 \
     pass="$(printf '%s' "$BOX_PASSWORD" | rclone obscure -)" --no-obscure
   unset BOX_PASSWORD
   rclone lsd hetzner:frigate        # data, status
   ```

3. **Crypt remote** over `frigate/data`. The uploader uses rclone's defaults (standard
   file name encryption, encrypted directory names, base32), so only the password and the
   salt are needed. `rclone obscure` turns the escrowed plain text into rclone's format:

   ```sh
   read -rs CRYPT_PASSWORD           # the escrowed crypt password
   read -rs CRYPT_SALT               # the escrowed salt (password2)
   rclone config create cam crypt remote=hetzner:frigate/data \
     password="$(printf '%s' "$CRYPT_PASSWORD" | rclone obscure -)" \
     password2="$(printf '%s' "$CRYPT_SALT" | rclone obscure -)" --no-obscure
   unset CRYPT_PASSWORD CRYPT_SALT
   rclone lsd cam:                   # clips and recordings; garbled names mean a wrong password or salt
   ```

4. **Copy the day you want and play it.** Recordings are 10 s segments under
   `recordings/YYYY-MM-DD/HH/living_room/MM.SS.mp4`, in UTC. `clips/` holds the snapshots.

   ```sh
   rclone lsf cam:recordings/
   rclone copy cam:recordings/2026-10-15 ./restore/recordings/2026-10-15 --progress
   rclone copy cam:clips ./restore/clips --max-age 72h
   ```

   Play the files with VLC or `ffplay`.

If files are missing, look in a snapshot (next section).

## Storage Box snapshots

The box takes a daily automatic snapshot and keeps 10. They are in the Hetzner Console under
Storage Box → Snapshots. To read files from one without rolling the whole box back, turn on
**Display snapshot directory** ("Snapshot-Verzeichnis anzeigen") on that page. The main
account then sees each snapshot read-only under `.zfs/snapshot/<snapshot>/` (on port 23:
`/home/.zfs/snapshot/`). Repeat step 3 as `cam-snap` with
`remote=hetzner:.zfs/snapshot/<snapshot>/frigate/data`. A rollback in the Console deletes
everything newer than the snapshot, so copy files out instead.

Open decision: files uploaded since the last snapshot can be deleted by anyone holding the
uploader key from a stolen node.

## Pausing uploads

```sh
kubectl -n frigate exec deploy/frigate -c uploader -- touch /state/pause
kubectl -n frigate exec deploy/frigate -c uploader -- rm /state/pause
```

While paused the uploader skips its runs and the liveness probe leaves it alone. The pause
lasts until the `rm` or a new pod. Home Assistant's "upload stale" warning fires about
40 minutes in, if the camera is armed.

## The NAS copy

Plain files, not encrypted, in the same layout as `/media/frigate` (`recordings/`, `clips/`),
on TrueNAS 10.0.0.203 under `/mnt/pool1/k8s/static-disks/cam-records`. The NFS export
admits only 10.0.0.150, .151 and .87. The Frigate pod's `nas-push` sidecar sends the files
over WebDAV to the `frigate-nas-mirror` pod, which mounts the share and writes as uid and
gid 568 (TrueNAS `apps`), so the dataset must let 568 write. Files older than 30 days are
removed daily. When the NAS is down only the mirror pod waits; Frigate keeps recording and
uploading, and `nas-push` catches up on the last 72 h when the mirror is back.

## Prerequisites outside Git

- **Home Assistant `.storage`** (edit only while HA is stopped):
  - the Meross plug's entity renamed to `switch.cam_socket` ("Cam Socket")
  - the MQTT entry: broker 10.0.0.87:1883, user `homeassistant` with its password from
    Mosquitto's password file
  - the main dashboard's plug row (`lovelace.dashboard_dashboard`) uses `switch.cam_socket`
  - the Generic Camera entry "Living room camera" (`camera.living_room_camera`): stream source
    `rtsp://cam.home.bariss.dev:8554/living_room`, RTSP over TCP, no still image URL, and the username and password
    from `FRIGATE_RESTREAM_USER` and `FRIGATE_RESTREAM_PASSWORD` in `frigate-secrets` (HA adds
    them to the URL). Settings → Devices & services → Add integration → Generic Camera
    recreates it.
- **Home Assistant clips:** `/config/media/camera` must exist (root, 0755), or HA rejects
  `media_dirs` and starts in recovery mode. `.storage/camera` has `preload_stream: true` for
  `camera.living_room_camera`, which the 5 s lookback of the person-alert clip needs.
- **Home Assistant UI automations** (`automations.yaml`): "when going out" starts with
  `script.camera_arm`; "when going in" starts with `script.camera_arrival` and
  `left_at: "{{ trigger.from_state.last_changed if trigger.from_state else '' }}"`.
- **Home Assistant SSH keys** in `/config/.ssh/` (root, 0600):
  - `/config/.ssh/hetzner_status`, the read-only sub2 key; its public key is in
    `frigate/status/.ssh/authorized_keys` on the box, and the host key is pinned in
    `known_hosts_hetzner`
  - `cudy_toggle`, for the internet toggles on the Cudy router
- **Storage Box:** the uploader's public key in `frigate/.ssh/authorized_keys` (sub1), the
  sub-accounts above, daily snapshots and delete protection
- **Mosquitto password file** on the Pi
- **Router:** the DNAT, the camera's lease and the nft include on the Cudy
- **NAS:** the `cam-records` dataset and its NFS export
- **Escrow outside the house:** the crypt password and salt, and the SOPS age key

## Known limit: node-local access

The NetworkPolicy admits only Traefik, on 8971, and the Pi, on 8554 (the restream, with a
password). Cilium (`talos/README.md`) lets all traffic from the node itself
through, so any process on the node that runs Frigate, a hostNetwork pod too,
can reach the pod's port 5000 (Frigate's API without sign-in) and 1984
(go2rtc's API) directly.
