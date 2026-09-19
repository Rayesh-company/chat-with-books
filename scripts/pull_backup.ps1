# The nightly local pull (T14, GitLab #16): fetch the newest backup archive
# from the VPS into the operator's machine and keep RETENTION of them.
# The VPS-side archive is built by scripts/backup.sh under cron; this is the
# second location — an archive only on the VPS dies with the VPS.
#
# One-time setup (see the README's "The nightly backup"):
#   1. SSH key:  ssh-keygen -t ed25519  then install the public key on the
#      VPS (ssh-copy-id from Git Bash, or append to ~/.ssh/authorized_keys).
#   2. Scheduled task (run once, elevated not required):
#      schtasks /Create /TN "chat-with-books backup pull" /SC DAILY /ST 04:00 `
#        /TR "powershell -ExecutionPolicy Bypass -File <repo>\scripts\pull_backup.ps1"

param(
    [string]$VpsHost = "CHANGE-ME",                  # e.g. vps.example.ir
    [string]$VpsUser = "root",
    [string]$VpsArchiveDir = "/var/backups/chat-with-books",
    [string]$LocalDir = "D:\code\CHATBOT\backups",
    [int]$Retention = 30
)

$ErrorActionPreference = "Stop"

if ($VpsHost -eq "CHANGE-ME") {
    Write-Error "pull_backup: set -VpsHost (or edit the param default) before scheduling this."
}

New-Item -ItemType Directory -Force -Path $LocalDir | Out-Null

$remote = ssh "$VpsUser@$VpsHost" "ls -1t $VpsArchiveDir/chat-with-books-backup-*.tar.gz 2>/dev/null | head -1"
if (-not $remote) {
    Write-Error "pull_backup: no archive found in $VpsArchiveDir on $VpsHost — is the VPS cron running?"
}
$remote = $remote.Trim()
$name = Split-Path $remote -Leaf
$local = Join-Path $LocalDir $name

if (Test-Path $local) {
    Write-Output "pull_backup: $name already pulled"
} else {
    Write-Output "pull_backup: fetching $name"
    scp -q "$VpsUser@${VpsHost}:$remote" $local
    Write-Output ("pull_backup: {0} ({1:N0} MB)" -f $local, ((Get-Item $local).Length / 1MB))
}

$stale = Get-ChildItem $LocalDir -Filter "chat-with-books-backup-*.tar.gz" |
    Sort-Object LastWriteTime -Descending | Select-Object -Skip $Retention
foreach ($old in $stale) {
    Write-Output "pull_backup: prune $($old.Name)"
    Remove-Item $old.FullName
}

Write-Output ("pull_backup: done — {0} archive(s) kept locally" -f
    (Get-ChildItem $LocalDir -Filter "chat-with-books-backup-*.tar.gz").Count)
