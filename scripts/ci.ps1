[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$gatewayRoot = Join-Path $repoRoot "rp-gateway"
$worldpacksRoot = Join-Path $repoRoot "worldpacks"

function Resolve-Runtime {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][AllowEmptyString()][string[]]$Candidates
    )

    $seen = @{}
    foreach ($candidate in $Candidates) {
        if (-not $candidate -or $seen.ContainsKey($candidate)) {
            continue
        }
        $seen[$candidate] = $true
        if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) {
            continue
        }
        $previousPreference = $ErrorActionPreference
        try {
            $ErrorActionPreference = "SilentlyContinue"
            & $candidate --version 1> $null 2> $null
            $runtimeExitCode = $LASTEXITCODE
        }
        finally {
            $ErrorActionPreference = $previousPreference
        }
        if ($runtimeExitCode -eq 0) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }
    throw "Cannot find a working $Name runtime in the active environment or bundled Codex runtime."
}

function Path-Command {
    param([Parameter(Mandatory = $true)][string]$Name)

    $command = Get-Command $Name -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($command) {
        return $command.Source
    }
    return ""
}

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)][string]$Executable,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [Parameter(Mandatory = $true)][string]$WorkingDirectory
    )

    Push-Location $WorkingDirectory
    try {
        & $Executable @Arguments
        if ($LASTEXITCODE -ne 0) {
            throw "Command failed with exit code ${LASTEXITCODE}: $Executable $($Arguments -join ' ')"
        }
    }
    finally {
        Pop-Location
    }
}

function Test-PythonDependencies {
    param([Parameter(Mandatory = $true)][string]$Executable)

    $previousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "SilentlyContinue"
        & $Executable -c "import pytest, fastapi, httpx, pydantic" 1> $null 2> $null
        return $LASTEXITCODE -eq 0
    }
    finally {
        $ErrorActionPreference = $previousPreference
    }
}

$bundledRoot = if ($env:USERPROFILE) {
    Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies"
} else {
    ""
}
$python = Resolve-Runtime -Name "Python" -Candidates @(
    $(if ($env:VIRTUAL_ENV) { Join-Path $env:VIRTUAL_ENV "Scripts\python.exe" }),
    (Join-Path $repoRoot ".venv\Scripts\python.exe"),
    $(if ($bundledRoot) { Join-Path $bundledRoot "python\python.exe" }),
    (Path-Command "python"),
    (Path-Command "python3")
)
$node = Resolve-Runtime -Name "Node.js" -Candidates @(
    $(if ($bundledRoot) { Join-Path $bundledRoot "node\bin\node.exe" }),
    (Path-Command "node")
)

$previousPythonPath = $env:PYTHONPATH
try {
    if (-not (Test-PythonDependencies -Executable $python)) {
        $dependencyCandidates = @(
            (Join-Path $gatewayRoot ".test-deps"),
            (Join-Path (Split-Path -Parent $repoRoot) "tmp\rp-gateway-test-deps")
        )
        $dependencyRoot = $dependencyCandidates | Where-Object { Test-Path -LiteralPath $_ -PathType Container } | Select-Object -First 1
        if ($dependencyRoot) {
            $env:PYTHONPATH = if ($previousPythonPath) {
                "$dependencyRoot$([IO.Path]::PathSeparator)$previousPythonPath"
            } else {
                $dependencyRoot
            }
        }
        if (-not (Test-PythonDependencies -Executable $python)) {
            throw "Gateway test dependencies are unavailable. Activate a prepared environment or set PYTHONPATH; this script does not install dependencies."
        }
    }

    $expectedPacks = @("awareness", "awareness-one-day")
    $actualPacks = @(Get-ChildItem -LiteralPath $worldpacksRoot -Directory | Sort-Object Name | ForEach-Object Name)
    if (($actualPacks -join "`n") -ne ($expectedPacks -join "`n")) {
        throw "Expected exactly two WorldPacks: $($expectedPacks -join ', '); found: $($actualPacks -join ', ')."
    }
    $stateSeeds = @(Get-ChildItem -LiteralPath $worldpacksRoot -Recurse -Filter "state-seed.json" -File)
    if ($stateSeeds.Count -ne 2) {
        throw "Expected exactly two state-seed.json files; found $($stateSeeds.Count)."
    }
    foreach ($slug in $expectedPacks) {
        Invoke-Checked -Executable $python -WorkingDirectory $repoRoot -Arguments @(
            "scripts/validate-state.py",
            "--state", "worldpacks/$slug/state-seed.json",
            "--schema", "state/schema.json"
        )
    }
    Invoke-Checked -Executable $python -WorkingDirectory $repoRoot -Arguments @(
        "scripts/validate-training-runtime.py", "--worldpacks", "worldpacks"
    )
    Invoke-Checked -Executable $python -WorkingDirectory $gatewayRoot -Arguments @(
        "-m", "compileall", "-q", "app"
    )

    $trainingTests = @(
        "tests/test_awareness_one_day.py",
        "tests/test_training_runtime.py",
        "tests/test_training_artifacts.py",
        "tests/test_training_capabilities.py",
        "tests/test_showroom_portal.py",
        "tests/test_decision_019_contracts.py",
        "tests/test_training_gateway_mode_guard.py",
        "tests/test_training_showroom_mode_guard.py",
        "tests/test_gateway.py::test_showroom_artifact_only_turn_keeps_materialized_provider_narrative",
        "tests/test_gateway.py::test_showroom_training_repair_preserves_previously_valid_profile"
    )
    Invoke-Checked -Executable $python -WorkingDirectory $gatewayRoot -Arguments (@("-m", "pytest", "-q") + $trainingTests)

    $syntaxFiles = @(
        "rp-showcase-gui/app.js",
        "rp-showcase-gui/structured-content.js",
        "rp-showcase-gui/message-time.js",
        "rp-showcase-gui/training-only.test.js",
        "ui-shared/training-artifacts.js"
    )
    foreach ($file in $syntaxFiles) {
        Invoke-Checked -Executable $node -WorkingDirectory $repoRoot -Arguments @("--check", $file)
    }
    $nodeTests = @(
        "rp-showcase-gui/request-policy.test.js",
        "rp-showcase-gui/structured-content.test.js",
        "rp-showcase-gui/message-time.test.js",
        "rp-showcase-gui/training-only.test.js",
        "ui-shared/training-artifacts.test.js"
    )
    foreach ($test in $nodeTests) {
        Invoke-Checked -Executable $node -WorkingDirectory $repoRoot -Arguments @($test)
    }

    Write-Host "Training-only source checks passed."
}
finally {
    $env:PYTHONPATH = $previousPythonPath
}
