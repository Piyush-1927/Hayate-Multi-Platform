param([string]$Dist = "dist\Hayate")
function Size($p){ [math]::Round(((Get-ChildItem $p -Recurse -File | Measure-Object Length -Sum).Sum)/1MB,1) }
$Dist = (Resolve-Path $Dist).Path
$before = Size $Dist
$root = Join-Path $Dist "libmpv-2.dll"; $int = Join-Path $Dist "_internal\libmpv-2.dll"
if ((Test-Path $root) -and (Test-Path $int)) { Remove-Item $root -Force; "removed duplicate libmpv-2.dll" }
$qt = Join-Path $Dist "_internal\PyQt6\Qt6"
foreach ($pat in 'avcodec-*.dll','avformat-*.dll','avutil-*.dll','swresample-*.dll','swscale-*.dll') {
  Get-ChildItem (Join-Path $qt "bin") -Filter $pat -ErrorAction SilentlyContinue | ForEach-Object { Remove-Item $_.FullName -Force; "removed $($_.Name)" }
}
Get-ChildItem (Join-Path $qt "plugins\multimedia") -Filter "ffmpegmediaplugin*.dll" -ErrorAction SilentlyContinue | ForEach-Object { Remove-Item $_.FullName -Force; "removed $($_.Name)" }
# move QtWebEngine out into a separate add-on zip
$parent = Split-Path $Dist -Parent
$addon = Join-Path $parent "Hayate-webengine"
Remove-Item $addon -Recurse -Force -ErrorAction SilentlyContinue
$n = 0
Get-ChildItem $Dist -Recurse -File | ForEach-Object {
  $rel = $_.FullName.Substring($Dist.Length).TrimStart('\')
  if ($rel -match 'WebEngine|qtwebengine' -or $_.Name -in 'icudtl.dat','v8_context_snapshot.bin') {
    $dest = Join-Path $addon $rel
    New-Item -ItemType Directory -Force (Split-Path $dest -Parent) | Out-Null
    Move-Item $_.FullName $dest -Force; $n++
  }
}
if ($n -eq 0) { throw "no WebEngine files found to move" }
$zip = Join-Path $parent "Hayate-WebEngine-addon.zip"
Remove-Item $zip -Force -ErrorAction SilentlyContinue
Compress-Archive -Path "$addon\*" -DestinationPath $zip -Force
$sha = (Get-FileHash $zip -Algorithm SHA256).Hash.ToLower()
"#define WebSha `"$sha`"" | Set-Content -Encoding ASCII (Join-Path $PSScriptRoot "webengine-hash.iss")
"moved $n WebEngine files; addon zip $([math]::Round((Get-Item $zip).Length/1MB,1)) MB, sha256 $sha"
"size: $before MB -> $(Size $Dist) MB"
