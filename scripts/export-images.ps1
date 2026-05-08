# ============================================================
# Ops-Navigator 이미지 빌드 + 내보내기 (Windows PowerShell)
#
# 사용법:
#   cd D:\personalPJT\SMAgentLab
#   powershell -ExecutionPolicy Bypass -File scripts\export-images.ps1 [-Tag v2.16]
#
# 결과물: smagentlab-images-{태그}.tar.gz
# ============================================================
param(
    [string]$Tag = ""
)

$ErrorActionPreference = "Stop"

# .env에서 IMAGE_TAG 읽기 (인자 미지정 시)
if ([string]::IsNullOrEmpty($Tag) -and (Test-Path ".env")) {
    $envLine = Get-Content ".env" | Where-Object { $_ -match "^IMAGE_TAG=" } | Select-Object -First 1
    if ($envLine) {
        $Tag = ($envLine -split "=", 2)[1].Trim('"').Trim("'")
    }
}
if ([string]::IsNullOrEmpty($Tag)) { $Tag = "latest" }

$ExportFile  = "smagentlab-images-$Tag.tar.gz"
$BackendImg  = "smagentlab-backend:$Tag"
$FrontendImg = "smagentlab-frontend:$Tag"
$PgImg       = "pgvector/pgvector:pg16"
$RedisImg    = "redis:7-alpine"

Write-Host "==========================================" -ForegroundColor Cyan
Write-Host " Ops-Navigator 이미지 빌드 + 내보내기" -ForegroundColor Cyan
Write-Host " 태그: $Tag" -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan

# 1. 빌드 (IMAGE_TAG 환경변수로 주입)
Write-Host "`n[1/4] 이미지 빌드 중... (10~15분 소요)" -ForegroundColor Yellow
$env:IMAGE_TAG = $Tag
docker compose build --no-cache
if ($LASTEXITCODE -ne 0) { throw "빌드 실패" }

# 2. 외부 이미지 pull
Write-Host "`n[2/4] 외부 이미지 pull..." -ForegroundColor Yellow
docker pull $PgImg
docker pull $RedisImg

# 3. tar로 저장 + gzip 압축
Write-Host "`n[3/4] 이미지 내보내기 → $ExportFile" -ForegroundColor Yellow
$TempTar = "smagentlab-images-$Tag.tar"
docker save -o $TempTar $BackendImg $FrontendImg $PgImg $RedisImg
if ($LASTEXITCODE -ne 0) { throw "docker save 실패" }

# PowerShell 5.1 호환 gzip 압축
$inStream  = [IO.File]::OpenRead($TempTar)
$outStream = [IO.File]::Create($ExportFile)
$gzip      = New-Object IO.Compression.GZipStream($outStream, [IO.Compression.CompressionMode]::Compress)
$inStream.CopyTo($gzip)
$gzip.Close(); $outStream.Close(); $inStream.Close()
Remove-Item $TempTar

# 4. 결과
$SizeMB = [math]::Round((Get-Item $ExportFile).Length / 1MB, 1)
Write-Host "`n[4/4] 완료!" -ForegroundColor Green
Write-Host "  파일: $ExportFile"
Write-Host "  크기: ${SizeMB} MB"
Write-Host "`n다음 단계:" -ForegroundColor Cyan
Write-Host "  1) $ExportFile 를 폐쇄망 서버로 전송"
Write-Host "  2) docker-compose.yml, docker-compose.prod.yml, init/, .env 도 함께 전송"
Write-Host "  3) 서버에서: bash scripts/import-and-run.sh $ExportFile"
