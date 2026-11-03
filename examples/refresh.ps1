# Keep a markfind index up to date. Meant to run on a schedule (Task Scheduler).
#
#   .\examples\refresh.ps1 "$env:LOCALAPPDATA\Google\Chrome\User Data\Default\Bookmarks"
#
# Register a weekly task (Sundays at 09:00) from PowerShell:
#   $action  = New-ScheduledTaskAction -Execute "powershell.exe" `
#       -Argument "-NoProfile -File C:\path\to\markfind\examples\refresh.ps1 C:\path\to\bookmarks.html"
#   $trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At 9am
#   Register-ScheduledTask -TaskName "markfind refresh" -Action $action -Trigger $trigger

param(
    [Parameter(Mandatory = $true)]
    [string] $BookmarksFile
)

$ErrorActionPreference = "Stop"

function Invoke-Markfind {
    markfind @args
    if ($LASTEXITCODE -ne 0) { throw "markfind $args failed with exit code $LASTEXITCODE" }
}

Write-Output "== markfind refresh: $(Get-Date)"
Invoke-Markfind import $BookmarksFile
# New bookmarks, pages older than 30 days, and earlier failures.
Invoke-Markfind fetch --older-than 30d --failed --quiet
Invoke-Markfind stats
Invoke-Markfind broken
