# Generates one narration WAV per scene from narration.json using the Gemini
# TTS API, into public/audio/, and writes public/audio/manifest.json with each
# clip's measured duration so the renderer can time scenes to the voice.
#
# The API key is read from the environment and is never written to disk:
#   $env:GEMINI_API_KEY = '<your key>'
#   powershell -File make-narration.ps1
#
# Re-running skips clips that already exist unless -Force is passed.

param(
  [switch]$Force,
  [string]$Voice = "",
  [string]$Model = "",
  # Comma-separated scene ids. Quota is small and per model, so filling four
  # gaps should not cost fourteen requests.
  [string]$Only = ""
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$audioDir = Join-Path $root "public\audio"

if (-not $env:GEMINI_API_KEY) {
  Write-Error "GEMINI_API_KEY is not set. Set it in your shell, not in a file."
  exit 1
}

$spec = Get-Content (Join-Path $root "narration.json") -Raw | ConvertFrom-Json
if (-not $Voice) { $Voice = $spec.voice }
if (-not $Model) { $Model = $spec.model }

# Clips live under a per-model folder. TTS quota is enforced per model, so a
# run can exhaust one model and have to finish on another - and a voice that
# changes timbre halfway through a reel is instantly audible. Keeping each
# model's set separate means switching never produces a mixed soundtrack, and
# a half-finished set survives to be resumed.
$clipDir = Join-Path $audioDir $Model
New-Item -ItemType Directory -Force -Path $clipDir | Out-Null

Write-Output "voice=$Voice model=$Model scenes=$($spec.scenes.Count)"
Write-Output "clips -> public\audio\$Model\"
Write-Output ""

# A RIFF/WAVE header carries the sample rate at byte 24, channels at 22 and
# bit depth at 34. Reading them beats assuming, because the duration is what
# every scene length is derived from.
# Models disagree about the container. gemini-3.8-flash-tts returns a complete
# RIFF/WAVE file; gemini-2.5-flash-preview-tts returns headerless signed 16-bit
# little-endian PCM, with the sample rate only in the mimeType. Writing those
# raw bytes into a .wav produces a file whose "header" is really audio data,
# which then parses as 2 channels at 65537 Hz and 3 bits - so wrap it properly.
function New-WavFromPcm([byte[]]$pcm, [int]$rate, [int]$channels, [int]$bits) {
  $stream = New-Object System.IO.MemoryStream
  $w = New-Object System.IO.BinaryWriter($stream)
  $blockAlign = $channels * [int]($bits / 8)
  $w.Write([Text.Encoding]::ASCII.GetBytes("RIFF"))
  $w.Write([int](36 + $pcm.Length))
  $w.Write([Text.Encoding]::ASCII.GetBytes("WAVE"))
  $w.Write([Text.Encoding]::ASCII.GetBytes("fmt "))
  $w.Write([int]16)
  $w.Write([int16]1)                      # 1 = uncompressed PCM
  $w.Write([int16]$channels)
  $w.Write([int]$rate)
  $w.Write([int]($rate * $blockAlign))    # byte rate
  $w.Write([int16]$blockAlign)
  $w.Write([int16]$bits)
  $w.Write([Text.Encoding]::ASCII.GetBytes("data"))
  $w.Write([int]$pcm.Length)
  $w.Write($pcm)
  $w.Flush()
  return $stream.ToArray()
}

function Get-WavDuration([string]$path) {
  $b = [IO.File]::ReadAllBytes($path)
  $channels = [BitConverter]::ToUInt16($b, 22)
  $rate = [BitConverter]::ToUInt32($b, 24)
  $bits = [BitConverter]::ToUInt16($b, 34)
  $bytesPerSecond = $rate * $channels * ($bits / 8)
  if ($bytesPerSecond -le 0) { return 0 }
  return [Math]::Round(($b.Length - 44) / $bytesPerSecond, 3)
}

$manifest = @()
$index = 0

$wanted = @()
if ($Only) { $wanted = $Only -split "," | ForEach-Object { $_.Trim() } }

foreach ($scene in $spec.scenes) {
  $index++
  $name = ("{0:d2}-{1}" -f $index, $scene.id)
  $file = Join-Path $clipDir "$name.wav"

  # Index must still advance for skipped scenes so file numbering stays aligned.
  if ($wanted.Count -and ($wanted -notcontains $scene.id)) { continue }

  if ((Test-Path $file) -and -not $Force) {
    $dur = Get-WavDuration $file
    Write-Output ("{0,-22} cached   {1,6}s" -f $name, $dur)
    $manifest += [ordered]@{ id = $scene.id; file = "audio/$Model/$name.wav"; seconds = $dur; text = $scene.text }
    continue
  }

  $prompt = "$($scene.style)`n`n$($scene.text)"
  $body = @{
    contents = @(@{ parts = @(@{ text = $prompt }) })
    generationConfig = @{
      responseModalities = @("AUDIO")
      speechConfig = @{ voiceConfig = @{ prebuiltVoiceConfig = @{ voiceName = $Voice } } }
    }
  } | ConvertTo-Json -Depth 10 -Compress

  $uri = "https://generativelanguage.googleapis.com/v1beta/models/${Model}:generateContent"
  $attempt = 0
  while ($true) {
    $attempt++
    try {
      $r = Invoke-RestMethod -Uri $uri -Method Post -TimeoutSec 180 `
        -Headers @{ "x-goog-api-key" = $env:GEMINI_API_KEY; "Content-Type" = "application/json" } `
        -Body $body
      $inline = $r.candidates[0].content.parts[0].inlineData
      if (-not $inline.data) { throw "no inlineData in response" }
      $raw = [Convert]::FromBase64String($inline.data)
      $isRiff = ($raw.Length -ge 12) -and
                ([Text.Encoding]::ASCII.GetString($raw[0..3]) -eq "RIFF")
      if ($isRiff) {
        [IO.File]::WriteAllBytes($file, $raw)
      } else {
        # Headerless PCM. The rate rides in the mimeType, e.g.
        # "audio/L16;codec=pcm;rate=24000".
        $rate = 24000
        if ($inline.mimeType -match "rate=(\d+)") { $rate = [int]$Matches[1] }
        [IO.File]::WriteAllBytes($file, (New-WavFromPcm $raw $rate 1 16))
      }
      break
    } catch {
      $msg = $_.Exception.Message
      # A 429 here is a per-minute quota, not a dead end: the free TTS tier
      # runs out after a handful of clips. Back off properly rather than
      # burning the retries in nine seconds.
      $rateLimited = $msg -match "429|Too Many Requests|RESOURCE_EXHAUSTED"
      if ($attempt -ge 5) {
        Write-Output ("{0,-22} FAILED   {1}" -f $name, $msg)
        break
      }
      $wait = if ($rateLimited) { 30 * $attempt } else { 4 * $attempt }
      Write-Output ("{0,-22} retry {1} in {2}s ({3})" -f $name, $attempt, $wait,
                    $(if ($rateLimited) { "rate limited" } else { "error" }))
      Start-Sleep -Seconds $wait
    }
  }

  if (Test-Path $file) {
    $dur = Get-WavDuration $file
    Write-Output ("{0,-22} ok       {1,6}s" -f $name, $dur)
    $manifest += [ordered]@{ id = $scene.id; file = "audio/$Model/$name.wav"; seconds = $dur; text = $scene.text }
  }
}

# Summed by hand: Measure-Object reads properties off objects, and these are
# ordered hashtables, so -Property seconds finds nothing.
$total = 0.0
foreach ($entry in $manifest) { $total += [double]$entry.seconds }
$payload = [ordered]@{
  voice = $Voice
  model = $Model
  generatedAt = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
  totalSeconds = [Math]::Round($total, 3)
  scenes = $manifest
}
$payload | ConvertTo-Json -Depth 10 | Set-Content (Join-Path $audioDir "manifest.json") -Encoding utf8

Write-Output ""
Write-Output ("{0} clips, {1}s total ({2})" -f $manifest.Count, [Math]::Round($total, 1), [TimeSpan]::FromSeconds($total).ToString("mm\:ss"))
Write-Output "manifest: public\audio\manifest.json"
