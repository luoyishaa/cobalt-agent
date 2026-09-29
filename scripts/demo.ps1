param(
    [string]$PythonBin = ""
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
if (-not $PythonBin) {
    $localPython = Join-Path $repoRoot '.venv\Scripts\python.exe'
    $PythonBin = if (Test-Path -LiteralPath $localPython) { $localPython } else { 'python' }
}
$envFile = Join-Path $repoRoot '.env'
if (-not (Test-Path -LiteralPath $envFile)) {
    throw 'Create a local .env with DEEPSEEK_API_KEY before running this demo.'
}
docker image inspect cobalt/python:3.11 *> $null
if ($LASTEXITCODE -ne 0) {
    throw 'Build the demo image first: docker build -t cobalt/python:3.11 -f docker/Dockerfile .'
}

$workspace = Join-Path ([System.IO.Path]::GetTempPath()) ('cobalt-demo-' + [guid]::NewGuid().ToString('N'))
Copy-Item -LiteralPath (Join-Path $repoRoot 'benchmarks\fixtures\grades') -Destination $workspace -Recurse
Write-Output "Demo workspace: $workspace"
Write-Output 'Baseline: the empty-average test should fail.'

$previousPythonPath = $env:PYTHONPATH
$env:PYTHONPATH = $workspace
try {
    Push-Location $workspace
    try {
        & $PythonBin -m unittest discover -s tests -q
        if ($LASTEXITCODE -eq 0) { throw 'The fixture unexpectedly passes before repair.' }

        Write-Output 'Request: fix the empty average, preserve normal behavior, run tests.'
        $agentOutput = & $PythonBin -m cobalt --workspace $workspace --env-file $envFile `
            --execution container --yes `
            'The grade average function crashes on an empty collection. Make it return 0.0 for empty input without changing its normal behavior. Run the tests afterward.'
        if ($LASTEXITCODE -ne 0) { throw 'Cobalt command failed.' }
        $agentOutput | Write-Output
        $isolationLine = $agentOutput | Where-Object { $_ -like 'Isolated workspace: *' } | Select-Object -First 1
        if (-not $isolationLine) { throw 'Cobalt did not report its isolated workspace.' }
        $isolatedWorkspace = $isolationLine.Substring('Isolated workspace: '.Length)
        $env:PYTHONPATH = $isolatedWorkspace

        Write-Output 'Independent verifier:'
        Push-Location $isolatedWorkspace
        try {
            & $PythonBin -m unittest discover -s (Join-Path $repoRoot 'benchmarks\verifiers\grades') -q
            if ($LASTEXITCODE -ne 0) { throw 'Independent verifier failed.' }
        }
        finally {
            Pop-Location
        }

        $record = Get-ChildItem -LiteralPath (Join-Path $isolatedWorkspace '.cobalt\runs') -Filter result.json -Recurse |
            Sort-Object LastWriteTime -Descending | Select-Object -First 1
        $result = Get-Content -LiteralPath $record.FullName -Raw -Encoding UTF8 | ConvertFrom-Json
        Write-Output ("Outcome: " + $result.status + "; tool calls: " + $result.tool_calls)
        if ($result.status -ne 'completed') { throw 'The independent verifier passed, but Cobalt did not complete its evidence checks.' }
        Write-Output ("Run record: " + $record.FullName)
    }
    finally {
        Pop-Location
    }
}
finally {
    $env:PYTHONPATH = $previousPythonPath
}
