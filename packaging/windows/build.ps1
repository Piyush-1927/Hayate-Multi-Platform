param([switch]$Console)
$ErrorActionPreference = "Stop"
$root = (Resolve-Path "$PSScriptRoot\..\..").Path
Set-Location $root

if (-not (Test-Path "$root\.build-venv")) { py -3.12 -m venv "$root\.build-venv" }
$py = "$root\.build-venv\Scripts\python.exe"
& $py -m pip install --upgrade pip
& $py -m pip install -r requirements.txt pyinstaller pillow

Remove-Item -Recurse -Force "$root\build", "$root\dist" -ErrorAction SilentlyContinue
$mode = if ($Console) { "--console" } else { "--windowed" }
& $py -m PyInstaller --noconfirm --clean $mode --name Hayate `
  --icon "$root\app_icon.png" `
  --paths "$root\src" `
  --collect-submodules hayate `
  "$PSScriptRoot\launcher.py"

$out = "$root\dist\Hayate"
Copy-Item "$root\app_icon.png" $out -Force

# yt-dlp next to the exe: the provider checks there
Invoke-WebRequest "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe" -OutFile "$out\yt-dlp.exe"

# optional libmpv: drop libmpv-2.dll (or mpv-2.dll) into packaging\windows before building
Get-ChildItem $PSScriptRoot -Filter "*mpv*.dll" -ErrorAction SilentlyContinue | ForEach-Object {
  Copy-Item $_.FullName $out -Force
  if (Test-Path "$out\_internal") { Copy-Item $_.FullName "$out\_internal" -Force }
}

Compress-Archive -Path $out -DestinationPath "$root\dist\Hayate-Windows-portable.zip" -Force

$iscc = @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe", "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe") |
  Where-Object { Test-Path $_ } | Select-Object -First 1
if ($iscc) { & $iscc "$PSScriptRoot\hayate.iss" } else { Write-Host "Inno Setup not found - installer skipped (portable zip is ready)" }
Write-Host "Done: $root\dist"
