[CmdletBinding()]
param(
    [ValidateSet('setup','prepare','toolchain','dependencies','mpv','ffmpeg','package','status')]
    [string]$Action = 'status',
    [string]$Distribution = 'Ubuntu-22.04',
    [string]$WslUser = 'diamond',
    [string]$Storage = 'E:\MPVBuild',
    [ValidateRange(1,128)][int]$Jobs = 4
)
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$workspace = "/home/$WslUser/mpv-build"
$image = Join-Path $Storage 'build.ext4'

function Invoke-Wsl([string]$UserName, [string[]]$Arguments) {
    & wsl.exe --distribution $Distribution --user $UserName --exec @Arguments
    if ($LASTEXITCODE -ne 0) { throw "WSL command failed ($LASTEXITCODE): $($Arguments -join ' ')" }
}
function Convert-WslPath([string]$Path) {
    $result = & wsl.exe --distribution $Distribution --exec wslpath -a -u $Path
    if ($LASTEXITCODE -ne 0) { throw "Cannot convert path: $Path" }
    return ($result | Select-Object -Last 1).Trim()
}
if ($Action -eq 'setup' -and -not (Test-Path -LiteralPath $image)) {
    New-Item -ItemType Directory -Path $Storage -Force | Out-Null
    $stream = [IO.File]::Open($image, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::ReadWrite)
    $stream.Dispose()
    & fsutil.exe sparse setflag $image
    if ($LASTEXITCODE -ne 0) { throw 'Cannot make a sparse workspace image.' }
    $stream = [IO.File]::OpenWrite($image)
    try { $stream.SetLength(48GB) } finally { $stream.Dispose() }
    [IO.File]::WriteAllText("$image.unformatted", 'Created for Local MPV builds')
}
if (-not (Test-Path -LiteralPath $image)) { throw 'Run build.ps1 -Action setup first.' }
$linuxRepo = Convert-WslPath $repo
$linuxImage = Convert-WslPath $image
$linuxStorage = Convert-WslPath $Storage
Invoke-Wsl root @('bash', "$linuxRepo/scripts/local/mount-workspace.sh", $linuxImage, $workspace, $WslUser)
if ($Action -eq 'setup') {
    Invoke-Wsl root @('bash', "$linuxRepo/scripts/local/setup-wsl.sh")
    Invoke-Wsl root @('bash', "$linuxRepo/scripts/local/mount-workspace.sh", $linuxImage, $workspace, $WslUser)
}
Invoke-Wsl $WslUser @('bash', "$linuxRepo/scripts/local/build.sh", $Action, $workspace, $linuxRepo, "$Jobs", "$linuxStorage/artifacts")
