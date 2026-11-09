# Restore camera recordings from the Hetzner Storage Box on Windows.
#
# Needs only rclone (winget install Rclone.Rclone), your Storage Box SSH key in an SSH agent
# (Bitwarden) or the main account's password, and the crypt password and salt from Bitwarden.
# Nothing secret is written to disk: the rclone remotes exist only in this process.
#
# -StorageBox is the Storage Box main account (u123456, from the password manager).
#
#   powershell -ExecutionPolicy Bypass -File restore.ps1 -StorageBox u123456  # list the days
#   powershell -ExecutionPolicy Bypass -File restore.ps1 -Day 2026-10-16     # one day
#   powershell -ExecutionPolicy Bypass -File restore.ps1 -Day 2026-10-16 -Hour 08
#   ... -BoxPassword      # log in with the main account's password instead of the SSH key
#   ... -CheckOnly        # only test the Storage Box login (no crypt password needed)
#
# Days and hours are UTC (Germany is UTC+1 in winter, UTC+2 in summer). Recordings are 10 s
# MP4 segments under recordings/<day>/<hour>/living_room/; play them with VLC.
# The person snapshots are in cam:clips (named by Unix time); see README.md for
# copying them.
param(
    [Parameter(Mandatory)][ValidatePattern('^u\d+$')][string]$StorageBox,
    [string]$Day,
    [string]$Hour,
    [string]$Dest = (Join-Path $env:USERPROFILE 'Downloads\camera-restore'),
    [switch]$BoxPassword,
    [switch]$CheckOnly
)
$ErrorActionPreference = 'Stop'
$names = 'HZ_TYPE', 'HZ_HOST', 'HZ_USER', 'HZ_PORT', 'HZ_KEY_USE_AGENT', 'HZ_PASS', 'HZ_KNOWN_HOSTS_FILE',
         'CAM_TYPE', 'CAM_REMOTE', 'CAM_PASSWORD', 'CAM_PASSWORD2'

function Plain([Security.SecureString]$s) {
    $b = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($s)
    try { [Runtime.InteropServices.Marshal]::PtrToStringBSTR($b) } finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($b) }
}

function Find-Rclone {
    $cmd = Get-Command rclone -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $winget = Get-ChildItem (Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Packages') -Filter rclone.exe -Recurse -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($winget) { return $winget.FullName }
    throw 'rclone not found. Install it with: winget install Rclone.Rclone'
}

try {
    $rc = Find-Rclone
    if ($Day -and $Day -notmatch '^\d{4}-\d{2}-\d{2}$') { throw "-Day must look like 2026-10-16, got '$Day'" }
    if ($Hour -and $Hour -notmatch '^\d{2}$') { throw "-Hour must look like 08, got '$Hour'" }
    if ($Hour -and -not $Day) { throw '-Hour needs -Day' }

    # The Storage Box main account; the host keys are pinned next to this script.
    $env:RCLONE_CONFIG_HZ_TYPE = 'sftp'
    $env:RCLONE_CONFIG_HZ_HOST = "$StorageBox.your-storagebox.de"
    $env:RCLONE_CONFIG_HZ_USER = $StorageBox
    $env:RCLONE_CONFIG_HZ_PORT = '23'
    $env:RCLONE_CONFIG_HZ_KNOWN_HOSTS_FILE = Join-Path $PSScriptRoot 'known_hosts'
    if ($BoxPassword) {
        $bp = Read-Host 'Storage Box main account password' -AsSecureString
        $env:RCLONE_CONFIG_HZ_PASS = (& $rc obscure (Plain $bp)).Trim()
        Remove-Variable bp
    } else {
        $env:RCLONE_CONFIG_HZ_KEY_USE_AGENT = 'true'
    }

    if ($CheckOnly) {
        & $rc --config NUL lsf hz:frigate --dirs-only
        if ($LASTEXITCODE -ne 0) { throw 'Storage Box login failed.' }
        Write-Host 'RESTORE-CHECK-OK: the Storage Box answers and frigate/ is there.' -ForegroundColor Green
        return
    }

    Write-Host 'Paste the crypt password and the salt from Bitwarden. Nothing is shown while you paste.'
    $p = Read-Host 'Crypt password (CRYPT_PASSWORD)' -AsSecureString
    $s = Read-Host 'Salt (CRYPT_PASSWORD2)' -AsSecureString
    # The crypt layer over frigate/data; rclone wants both values obscured.
    $env:RCLONE_CONFIG_CAM_TYPE = 'crypt'
    $env:RCLONE_CONFIG_CAM_REMOTE = 'hz:frigate/data'
    $env:RCLONE_CONFIG_CAM_PASSWORD = (& $rc obscure (Plain $p)).Trim()
    $env:RCLONE_CONFIG_CAM_PASSWORD2 = (& $rc obscure (Plain $s)).Trim()
    Remove-Variable p, s

    $days = & $rc --config NUL lsf cam:recordings --dirs-only 2>$null
    if (-not $days) { throw 'No readable folders: the password or the salt is wrong.' }
    if (-not $Day) {
        Write-Host 'Days on the Storage Box (UTC). Run again with -Day <day> [-Hour <HH>]:' -ForegroundColor Cyan
        $days | ForEach-Object { Write-Host "  $($_.TrimEnd('/'))" }
        return
    }

    $path = "recordings/$Day" + $(if ($Hour) { "/$Hour" } else { '' })
    $target = Join-Path $Dest ($path -replace '/', '\')
    Write-Host "Downloading $path to $target ..." -ForegroundColor Cyan
    & $rc --config NUL copy "cam:$path" $target --stats-one-line --stats 5s
    if ($LASTEXITCODE -ne 0) { throw "rclone copy failed ($LASTEXITCODE)" }
    $count = (Get-ChildItem -Recurse -File $target -ErrorAction SilentlyContinue).Count
    Write-Host "RESTORE-DONE: $count recordings in $target" -ForegroundColor Green
}
catch {
    Write-Host "RESTORE-FAILED: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
finally {
    foreach ($n in $names) { Remove-Item "env:RCLONE_CONFIG_$n" -ErrorAction SilentlyContinue }
}
