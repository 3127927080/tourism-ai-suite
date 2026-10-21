<#
.SYNOPSIS
    复制课程/原有项目到旅游实战工作目录。源仓库只读，绝不修改。

.DESCRIPTION
    将 kb_0515、customer-service、ai-service、ecommerce-service、day07
    复制到 C:\Users\th\Desktop\4组_陶桓_实战 下的三个新项目目录。
    默认排除 .git/.venv/__pycache__/node_modules 等；默认不复制大模型权重与训练产物。

.EXAMPLE
    .\复制项目代码.ps1 -WhatIfOnly
    只打印复制计划，不实际复制。

.EXAMPLE
    .\复制项目代码.ps1
    复制全部项目（不含模型权重与训练产物）。

.EXAMPLE
    .\复制项目代码.ps1 -Only kb
    只复制知识库。

.EXAMPLE
    .\复制项目代码.ps1 -IncludeModels -IncludeFinetuned -IncludeLogs
    连同 model/、finetuned/、logs/、outputs/ 一起复制。

.EXAMPLE
    .\复制项目代码.ps1 -CreateEnv
    复制完成后，若目标没有 .env，则从 .env.example 生成一份。
#>
[CmdletBinding()]
param(
    [ValidateSet("all", "kb", "customer", "ai", "tourism", "triple")]
    [string]$Only = "all",

    [switch]$IncludeModels,
    [switch]$IncludeFinetuned,
    [switch]$IncludeLogs,
    [switch]$CreateEnv,
    [switch]$WhatIfOnly
)

$ErrorActionPreference = "Stop"

$Root = "C:\Users\th\Desktop\4组_陶桓_实战"

# ---------- 排除项 ----------
$excludeDirs = @(
    ".git", ".venv", "venv", "__pycache__", "node_modules", ".idea",
    ".pytest_cache", ".uv-cache", ".deepeval", ".gitnexus",
    "pytest-cache-files-*", "dist", "build", ".cache",
    ".mypy_cache", ".ruff_cache"
)
if (-not $IncludeModels) { $excludeDirs += "model" }
if (-not $IncludeFinetuned) { $excludeDirs += "finetuned" }
if (-not $IncludeLogs) { $excludeDirs += @("logs", "outputs") }

$excludeFiles = @(".env", "*.pyc", "*.pyo", ".DS_Store", "Thumbs.db")

# ---------- 复制任务 ----------
$jobs = @(
    [pscustomobject]@{
        Key    = "kb"
        Name   = "旅游知识库"
        Source = "D:\pycharm-0515-th\kb_0515"
        Target = (Join-Path $Root "4组_陶桓_旅游_知识库")
    },
    [pscustomobject]@{
        Key    = "customer"
        Name   = "customer-service"
        Source = "E:\LLM_student_project\customer-service"
        Target = (Join-Path $Root "4组_陶桓_旅游_客服\customer-service")
    },
    [pscustomobject]@{
        Key    = "ai"
        Name   = "ai-service"
        Source = "E:\LLM_student_project\ai-service"
        Target = (Join-Path $Root "4组_陶桓_旅游_客服\ai-service")
    },
    [pscustomobject]@{
        Key    = "tourism"
        Name   = "tourism-service（fork 电商服务）"
        Source = "E:\项目一_电商小二\day01_项目介绍&客服项目初始化\4_other\电商后台项目+前端项目\后端电商服务部署\ecommerce-service"
        Target = (Join-Path $Root "4组_陶桓_旅游_客服\tourism-service")
    },
    [pscustomobject]@{
        Key    = "triple"
        Name   = "三元组提取微调"
        Source = "D:\pycharm-0515-th\LLM_student\src\test\day07"
        Target = (Join-Path $Root "4组_陶桓_三元组提取微调")
    }
)

# ---------- 工具函数 ----------
function Write-Step {
    param([string]$Message)
    Write-Host "`n=== $Message ===" -ForegroundColor Cyan
}

function Invoke-Robocopy {
    param(
        [string]$Source,
        [string]$Target,
        [string[]]$ExcludeDirs,
        [string[]]$ExcludeFiles
    )

    $rcArgs = @(
        $Source, $Target,
        "/E", "/NFL", "/NDL", "/NJH", "/NJS", "/NP", "/R:1", "/W:1"
    )
    if ($ExcludeDirs.Count -gt 0) {
        $rcArgs += "/XD"
        $rcArgs += $ExcludeDirs
    }
    if ($ExcludeFiles.Count -gt 0) {
        $rcArgs += "/XF"
        $rcArgs += $ExcludeFiles
    }

    if ($WhatIfOnly) {
        Write-Host ("[DRY-RUN] robocopy " + ($rcArgs -join " ")) -ForegroundColor Yellow
        return
    }

    New-Item -ItemType Directory -Path $Target -Force | Out-Null
    & robocopy @rcArgs | Out-Null
    $code = $LASTEXITCODE

    # robocopy: 0-7 表示成功（含有文件被复制），>=8 表示失败
    if ($code -ge 8) {
        throw "复制失败（robocopy 退出码 $code）：$Source -> $Target"
    }
    Write-Host "完成（robocopy 退出码 $code）" -ForegroundColor Green
}

# ---------- 主流程 ----------
$selected = if ($Only -eq "all") { $jobs } else { $jobs | Where-Object { $_.Key -eq $Only } }
if (-not $selected) { throw "未找到项目：$Only" }

Write-Step "复制计划"
Write-Host ("工作根目录：{0}" -f $Root)
Write-Host ("模式：{0}" -f $(if ($WhatIfOnly) { "试运行" } else { "实际复制" }))
Write-Host ("包含模型权重：{0}；训练产物：{1}；日志/输出：{2}" -f $IncludeModels, $IncludeFinetuned, $IncludeLogs)

foreach ($job in $selected) {
    Write-Step $job.Name
    Write-Host ("源：{0}" -f $job.Source)
    Write-Host ("目标：{0}" -f $job.Target)

    if (-not (Test-Path -LiteralPath $job.Source)) {
        Write-Warning ("源不存在，跳过：{0}" -f $job.Source)
        continue
    }

    Invoke-Robocopy -Source $job.Source -Target $job.Target -ExcludeDirs $excludeDirs -ExcludeFiles $excludeFiles

    if ($CreateEnv -and -not $WhatIfOnly) {
        $example = Join-Path $job.Target ".env.example"
        $envFile = Join-Path $job.Target ".env"
        if ((Test-Path -LiteralPath $example) -and -not (Test-Path -LiteralPath $envFile)) {
            Copy-Item -LiteralPath $example -Destination $envFile
            Write-Host "已从 .env.example 生成 .env（请填写真实密钥）" -ForegroundColor Yellow
        }
    }
}

Write-Step "完成"
Write-Host @"
复制完成。下一步：
1. 进入各工作副本执行 uv sync；
2. 复制 .env.example 为 .env 并填写真实密钥；
3. 启动并连通 PostgreSQL / Redis / Milvus / Neo4j / MongoDB / MinIO；
4. 原仓库只读，所有改动只在新项目目录内进行。

默认未复制：model/、finetuned/、logs/、outputs/。
需要时加 -IncludeModels、-IncludeFinetuned、-IncludeLogs。
"@ -ForegroundColor Green

# robocopy 的成功码 1-7 会残留到 $LASTEXITCODE，这里显式以 0 退出，避免被误判为失败
exit 0
