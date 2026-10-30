# Builds public/audio/manifest.json from whatever clips exist on disk.
#
# TTS quota is per model and small on the free tier, so a full 14-scene reel
# often cannot be generated from one model in one sitting. Clips therefore
# accumulate under public/audio/<model>/, and this picks each scene from the
# first model in -Prefer that actually has it.
#
#   powershell -File build-manifest.ps1
#   powershell -File build-manifest.ps1 -Prefer "gemini-3.8-flash-tts,gemini-3.8-flash-lite-tts"
#
# It reports which model each clip came from, because a reel drawing on two
# models has two voices in it and that is the operator's call, not a detail to
# bury.

param(
  [string]$Prefer = "gemini-3.8-flash-tts,gemini-2.5-flash-preview-tts,gemini-3.8-flash-lite-tts"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$audioDir = Join-Path $root "public\audio"
$spec = Get-Content (Join-Path $root "narration.json") -Raw | ConvertFrom-Json
$order = $Prefer -split "," | ForEach-Object { $_.Trim() } | Where-Object { $_ }

function Get-WavDuration([string]$path) {
  $b = [IO.File]::ReadAllBytes($path)
  if ($b.Length -lt 44) { return 0 }
  if ([Text.Encoding]::ASCII.GetString($b[0..3]) -ne "RIFF") { return 0 }
  $channels = [BitConverter]::ToUInt16($b, 22)
  $rate = [BitConverter]::ToUInt32($b, 24)
  $bits = [BitConverter]::ToUInt16($b, 34)
  $bps = $rate * $channels * ($bits / 8)
  if ($bps -le 0) { return 0 }
  return [Math]::Round(($b.Length - 44) / $bps, 3)
}

$manifest = @()
$used = @{}
$missing = @()
$index = 0

Write-Output ("{0,-22} {1,8}  {2}" -f "scene", "seconds", "from model")
foreach ($scene in $spec.scenes) {
  $index++
  $name = ("{0:d2}-{1}" -f $index, $scene.id)
  $picked = $null
  foreach ($model in $order) {
    $candidate = Join-Path $audioDir (Join-Path $model "$name.wav")
    if (Test-Path $candidate) {
      $dur = Get-WavDuration $candidate
      # A zero duration means the file is not valid RIFF audio - most likely
      # raw PCM written without a header. Skip it rather than time a scene
      # to a corrupt clip.
      if ($dur -gt 0) { $picked = @{ model = $model; seconds = $dur }; break }
    }
  }
  if ($picked) {
    $manifest += [ordered]@{
      id = $scene.id
      file = "audio/$($picked.model)/$name.wav"
      seconds = $picked.seconds
      model = $picked.model
      text = $scene.text
    }
    $used[$picked.model] = 1 + ($used[$picked.model] | ForEach-Object { if ($_) { $_ } else { 0 } })
    Write-Output ("{0,-22} {1,8}  {2}" -f $name, $picked.seconds, $picked.model)
  } else {
    $missing += $scene.id
    Write-Output ("{0,-22} {1,8}  -- NO CLIP (scene will be silent)" -f $name, "")
  }
}

$total = 0.0
foreach ($entry in $manifest) { $total += [double]$entry.seconds }

[ordered]@{
  voice = $spec.voice
  preference = $order
  generatedAt = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
  totalSeconds = [Math]::Round($total, 3)
  missing = $missing
  scenes = $manifest
} | ConvertTo-Json -Depth 10 | Set-Content (Join-Path $audioDir "manifest.json") -Encoding utf8

Write-Output ""
Write-Output ("narrated {0}/{1} scenes, {2} ({3})" -f $manifest.Count, $spec.scenes.Count,
  [TimeSpan]::FromSeconds($total).ToString("mm\:ss"), (($used.Keys | Sort-Object) -join " + "))
if ($missing.Count) {
  Write-Output ("silent: {0}" -f ($missing -join ", "))
}
if ($used.Keys.Count -gt 1) {
  Write-Output "NOTE: clips come from more than one model, so the reel has more than one voice."
}
