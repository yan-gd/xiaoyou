[CmdletBinding()]
param(
    [string]$Alias = "xiaoyou-release",
    [switch]$Force
)

$ErrorActionPreference = "Stop"

$appRoot = Split-Path -Parent $PSScriptRoot
$androidRoot = Join-Path $appRoot "android"
$keystoreDirectory = Join-Path $androidRoot "keystore"
$keystorePath = Join-Path $keystoreDirectory "xiaoyou-release.jks"
$propertiesPath = Join-Path $androidRoot "key.properties"

if ((Test-Path -LiteralPath $keystorePath) -or (Test-Path -LiteralPath $propertiesPath)) {
    if (-not $Force) {
        throw "Release signing files already exist. Refusing to overwrite them. Use -Force only when intentionally rotating the signing identity."
    }
}

$keytool = Get-Command keytool -ErrorAction Stop
[System.IO.Directory]::CreateDirectory($keystoreDirectory) | Out-Null

$randomBytes = New-Object byte[] 48
$randomNumberGenerator = [System.Security.Cryptography.RandomNumberGenerator]::Create()
try {
    $randomNumberGenerator.GetBytes($randomBytes)
}
finally {
    $randomNumberGenerator.Dispose()
}
$password = [Convert]::ToBase64String($randomBytes)

$previousPassword = $env:XIAOYOU_KEYSTORE_GENERATION_PASSWORD
try {
    $env:XIAOYOU_KEYSTORE_GENERATION_PASSWORD = $password
    & $keytool.Source `
        -genkeypair `
        -v `
        -keystore $keystorePath `
        -storetype PKCS12 `
        -alias $Alias `
        -keyalg RSA `
        -keysize 4096 `
        -validity 36500 `
        -dname "CN=Xiaoyou, OU=Xiaoyou, O=YoYo, L=Beijing, ST=Beijing, C=CN" `
        -storepass:env XIAOYOU_KEYSTORE_GENERATION_PASSWORD `
        -keypass:env XIAOYOU_KEYSTORE_GENERATION_PASSWORD

    if ($LASTEXITCODE -ne 0) {
        throw "keytool failed with exit code $LASTEXITCODE"
    }

    $lines = @(
        "storeFile=keystore/xiaoyou-release.jks"
        "storePassword=$password"
        "keyAlias=$Alias"
        "keyPassword=$password"
    )
    [System.IO.File]::WriteAllLines(
        $propertiesPath,
        $lines,
        [System.Text.UTF8Encoding]::new($false)
    )
}
catch {
    if (Test-Path -LiteralPath $keystorePath) {
        Remove-Item -LiteralPath $keystorePath -Force
    }
    throw
}
finally {
    $env:XIAOYOU_KEYSTORE_GENERATION_PASSWORD = $previousPassword
    [Array]::Clear($randomBytes, 0, $randomBytes.Length)
    $password = $null
}

Write-Host "Created permanent Android release signing identity."
Write-Host "Keystore: $keystorePath"
Write-Host "Properties: $propertiesPath"
Write-Host "Back up both files together. Losing them prevents future app updates."
