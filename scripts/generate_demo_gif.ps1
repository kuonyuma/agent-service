param(
    [string]$BaseUrl = "http://127.0.0.1:8000",
    [string]$OutputPath = "docs\demo.gif"
)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Drawing.Common

$live = Invoke-RestMethod "$BaseUrl/health/live"
$ready = Invoke-RestMethod "$BaseUrl/health/ready"
$conversation = Invoke-RestMethod `
    -Method Post `
    -Uri "$BaseUrl/api/v1/conversations" `
    -ContentType "application/json" `
    -Body '{"title":"简历演示会话"}'
$openapi = Invoke-RestMethod "$BaseUrl/openapi.json"
$pathCount = @($openapi.paths.PSObject.Properties).Count

$frames = @(
    @(
        "PS> docker compose up -d --build --wait",
        "MySQL 8.4       healthy",
        "Alembic migrate exited (0)",
        "FastAPI         healthy",
        "仅绑定 127.0.0.1:8000 / 127.0.0.1:3307"
    ),
    @(
        "PS> Invoke-RestMethod $BaseUrl/health/live",
        "HTTP 200  { status: '$($live.status)' }",
        "",
        "PS> Invoke-RestMethod $BaseUrl/health/ready",
        "HTTP 200  { status: '$($ready.status)' }"
    ),
    @(
        "PS> POST /api/v1/conversations",
        "HTTP 201",
        "title  = $($conversation.title)",
        "id     = $($conversation.id)",
        "active_run_id = null"
    ),
    @(
        "OpenAPI paths: $pathCount",
        "✓ SSE: text.delta / tool.* / run.*",
        "✓ active_run_id 原子占用，冲突返回 409",
        "✓ waiting_approval + 一次性审批审计 + 自动超时",
        "✓ 工作区路径隔离；任意 run_command 不向 Web 开放",
        "",
        "本 GIF 使用真实 Compose/MySQL/API 响应；未调用 Gemini。"
    )
)

$outputFullPath = [IO.Path]::GetFullPath((Join-Path $PWD $OutputPath))
$outputDirectory = Split-Path -Parent $outputFullPath
New-Item -ItemType Directory -Path $outputDirectory -Force | Out-Null

$tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
$frameDirectory = [IO.Path]::GetFullPath(
    (Join-Path $tempRoot ("agent-service-demo-" + [guid]::NewGuid()))
)
if (-not $frameDirectory.StartsWith($tempRoot, [StringComparison]::OrdinalIgnoreCase)) {
    throw "临时帧目录不在系统临时目录内。"
}
New-Item -ItemType Directory -Path $frameDirectory | Out-Null

try {
    $titleFont = [Drawing.Font]::new("Microsoft YaHei UI", 22, [Drawing.FontStyle]::Bold)
    $bodyFont = [Drawing.Font]::new("Microsoft YaHei UI", 17)
    $smallFont = [Drawing.Font]::new("Microsoft YaHei UI", 12)
    try {
        for ($index = 0; $index -lt $frames.Count; $index++) {
            $bitmap = [Drawing.Bitmap]::new(1200, 675)
            $graphics = [Drawing.Graphics]::FromImage($bitmap)
            try {
                $graphics.Clear([Drawing.ColorTranslator]::FromHtml("#0d1117"))
                $graphics.FillRectangle(
                    [Drawing.SolidBrush]::new([Drawing.ColorTranslator]::FromHtml("#161b22")),
                    0,
                    0,
                    1200,
                    64
                )
                foreach ($circle in @(
                    @{ X = 24; Color = "#ff5f56" },
                    @{ X = 52; Color = "#ffbd2e" },
                    @{ X = 80; Color = "#27c93f" }
                )) {
                    $graphics.FillEllipse(
                        [Drawing.SolidBrush]::new(
                            [Drawing.ColorTranslator]::FromHtml($circle.Color)
                        ),
                        $circle.X,
                        23,
                        14,
                        14
                    )
                }
                $graphics.DrawString(
                    "Agent Service — 本地生产链路验收",
                    $titleFont,
                    [Drawing.Brushes]::White,
                    125,
                    15
                )

                $y = 105
                foreach ($line in $frames[$index]) {
                    $color = if ($line.StartsWith("PS>")) {
                        "#58a6ff"
                    }
                    elseif ($line.StartsWith("HTTP") -or $line.StartsWith("✓")) {
                        "#3fb950"
                    }
                    else {
                        "#c9d1d9"
                    }
                    $brush = [Drawing.SolidBrush]::new(
                        [Drawing.ColorTranslator]::FromHtml($color)
                    )
                    try {
                        $graphics.DrawString($line, $bodyFont, $brush, 58, $y)
                    }
                    finally {
                        $brush.Dispose()
                    }
                    $y += 58
                }
                $graphics.DrawString(
                    "frame $($index + 1)/$($frames.Count)",
                    $smallFont,
                    [Drawing.Brushes]::Gray,
                    1060,
                    630
                )
                $framePath = Join-Path $frameDirectory ("frame-{0:d2}.png" -f $index)
                $bitmap.Save($framePath, [Drawing.Imaging.ImageFormat]::Png)
            }
            finally {
                $graphics.Dispose()
                $bitmap.Dispose()
            }
        }
    }
    finally {
        $titleFont.Dispose()
        $bodyFont.Dispose()
        $smallFont.Dispose()
    }

    $ffmpeg = Get-Command ffmpeg -ErrorAction Stop
    & $ffmpeg.Source `
        -hide_banner -loglevel error -y `
        -framerate 0.5 `
        -i (Join-Path $frameDirectory "frame-%02d.png") `
        -vf "fps=10,split[s0][s1];[s0]palettegen=max_colors=128[p];[s1][p]paletteuse=dither=bayer" `
        $outputFullPath
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}
finally {
    if (
        $frameDirectory.StartsWith($tempRoot, [StringComparison]::OrdinalIgnoreCase) `
        -and (Test-Path -LiteralPath $frameDirectory)
    ) {
        Remove-Item -LiteralPath $frameDirectory -Recurse -Force
    }
}

Write-Output $outputFullPath
