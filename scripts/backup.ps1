# Mnema backup script (Windows PowerShell).
# Mirrors scripts/backup.sh — see that file for usage notes.
#
# Examples:
#   .\scripts\backup.ps1
#   $env:BACKUP_DIR = "D:\mnema-backups"; .\scripts\backup.ps1
#
# Schedule with Task Scheduler. Restore: scripts/restore.ps1.

$ErrorActionPreference = "Stop"

$BackupDir     = if ($env:BACKUP_DIR)     { $env:BACKUP_DIR }     else { ".\backups" }
$DbContainer   = if ($env:DB_CONTAINER)   { $env:DB_CONTAINER }   else { "mnema_db" }
$UploadsVolume = if ($env:UPLOADS_VOLUME) { $env:UPLOADS_VOLUME } else { "mnema_uploads_data" }
$DbUser        = if ($env:DB_USER)        { $env:DB_USER }        else { "app" }
$DbName        = if ($env:DB_NAME)        { $env:DB_NAME }        else { "app" }

$Ts = Get-Date -Format "yyyyMMdd-HHmmss"
New-Item -ItemType Directory -Force -Path $BackupDir | Out-Null

$DbOut      = Join-Path $BackupDir "mnema-db-$Ts.sql.gz"
$UploadsOut = "mnema-uploads-$Ts.tar.gz"

Write-Host "[$([DateTime]::Now.ToString('o'))] Backing up Postgres ($DbContainer -> $DbName) ..."
# Stream pg_dump | gzip from inside the container. On Windows PowerShell 5.1
# a piped binary stream gets mangled by the host encoding, so we route the
# redirect through cmd.exe (which treats stdout as raw bytes) instead.
$DbOutWin = (Resolve-Path -LiteralPath (Split-Path $DbOut -Parent)).Path + "\" + (Split-Path $DbOut -Leaf)
& cmd /c "docker exec $DbContainer sh -c `"pg_dump -U $DbUser -d $DbName --clean --if-exists | gzip`" > `"$DbOutWin`""
if ($LASTEXITCODE -ne 0) { throw "pg_dump failed (exit $LASTEXITCODE)" }
Write-Host "  -> $DbOut"

$AbsBackup = (Resolve-Path $BackupDir).Path
Write-Host "[$([DateTime]::Now.ToString('o'))] Backing up uploads volume ($UploadsVolume) ..."
docker run --rm `
  -v "${UploadsVolume}:/data:ro" `
  -v "${AbsBackup}:/backup" `
  alpine tar czf "/backup/$UploadsOut" -C /data .
Write-Host "  -> $(Join-Path $BackupDir $UploadsOut)"

Write-Host "[$([DateTime]::Now.ToString('o'))] Backup complete."
