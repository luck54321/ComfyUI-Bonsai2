param([switch]$SkipModels, [switch]$SkipRuntime, [switch]$UseGithubMirror)
$ErrorActionPreference = 'Stop'
$runtime = Join-Path $PSScriptRoot 'runtime'
$cache = Join-Path $runtime 'downloads'
$comfyRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$models = Join-Path $comfyRoot 'models\LLM\Bonsai-2-27B'
$configPath = Join-Path $PSScriptRoot 'model_paths.json'
if (Test-Path -LiteralPath $configPath) {
    $configured = Get-Content -LiteralPath $configPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($configured.model_folders.Count -gt 0) { $models = $configured.model_folders[0] }
}
New-Item -ItemType Directory -Force -Path $runtime, $cache | Out-Null

function Get-VerifiedFile([string]$Url, [string]$Destination, [string]$Sha256) {
    if (Test-Path -LiteralPath $Destination) {
        if ((Get-FileHash -LiteralPath $Destination -Algorithm SHA256).Hash -eq $Sha256) {
            Write-Host "Verified: $Destination"
            return
        }
        throw "Existing file has an unexpected checksum; left unchanged: $Destination"
    }
    $partial = $Destination + '.part'
    Write-Host "Downloading: $Url"
    & curl.exe --location --fail --retry 4 --retry-delay 3 --connect-timeout 30 --continue-at - --output $partial $Url
    if ($LASTEXITCODE -ne 0) { throw "Download failed ($LASTEXITCODE). Run this script again to resume." }
    if ((Get-FileHash -LiteralPath $partial -Algorithm SHA256).Hash -ne $Sha256) {
        throw "Checksum mismatch: $partial. File was not installed."
    }
    Move-Item -LiteralPath $partial -Destination $Destination
    Write-Host "Verified: $Destination"
}

$release = 'https://github.com/PrismML-Eng/llama.cpp/releases/download/prism-b12128-d7b2896'
if ($UseGithubMirror) { $release = 'https://ghfast.top/' + $release }
$version = Join-Path $runtime 'version.txt'
if ((Test-Path -LiteralPath $version) -and
    (Get-Content -LiteralPath $version -Raw).Trim() -eq 'prism-b12128-d7b2896-cuda13.3' -and
    (Test-Path -LiteralPath (Join-Path $runtime 'llama-server.exe')) -and
    (Test-Path -LiteralPath (Join-Path $runtime 'cublas64_13.dll'))) {
    $SkipRuntime = $true
    Write-Host 'Pinned Bonsai runtime is already installed.'
}
$archives = @(
    @('llama-prism-b12128-d7b2896-bin-win-cuda-13.3-x64.zip', '86c6546685ffe2f0954bac062d0bdb347694a2b5eee6f5ddeb1bbfe82d2be1b6'),
    @('cudart-llama-bin-win-cuda-13.3-x64.zip', '1462a050eb4c684921ba51dcc4cc488a036674c3e73e9945ee705b854808d03e')
)
if (-not $SkipRuntime) {
foreach ($archive in $archives) {
    $file = Join-Path $cache $archive[0]
    Get-VerifiedFile "$release/$($archive[0])" $file $archive[1]
    Expand-Archive -LiteralPath $file -DestinationPath $runtime -Force
    Remove-Item -LiteralPath $file
}
if (-not (Test-Path -LiteralPath (Join-Path $runtime 'llama-server.exe'))) {
    $server = @(Get-ChildItem -LiteralPath $runtime -Filter 'llama-server.exe' -Recurse)
    if ($server.Count -ne 1) { throw 'Cannot locate the installed llama-server.exe.' }
    Get-ChildItem -LiteralPath $server[0].DirectoryName -File | Copy-Item -Destination $runtime -Force
}
Set-Content -LiteralPath $version -Value 'prism-b12128-d7b2896-cuda13.3' -Encoding ASCII
}
if (-not $SkipModels) {
    New-Item -ItemType Directory -Force -Path $models | Out-Null
    $source = 'https://modelscope.cn/models/prism-ml/Ternary-Bonsai-2-27B-gguf/resolve/master'
    Get-VerifiedFile "$source/Ternary-Bonsai-2-27B-PTQ1_0.gguf" (Join-Path $models 'Ternary-Bonsai-2-27B-PTQ1_0.gguf') '53107f530aa52eb00912263ab1ee29bd199261c87cd7b4ad4ca1318c1fe33ee3'
    Get-VerifiedFile "$source/Ternary-Bonsai-2-27B-mmproj-Q8_0.gguf" (Join-Path $models 'Ternary-Bonsai-2-27B-mmproj-Q8_0.gguf') '6807ede61d570bb86ba34b756a0fa109edc33668604de867c6ea6d8f1d631903'
}
if (@(Get-ChildItem -LiteralPath $cache -Force).Count -eq 0) { Remove-Item -LiteralPath $cache }
Write-Host 'Bonsai 2 installation complete. Restart ComfyUI to load the new node.'
