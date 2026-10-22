# PREPARATION ONLY. No command in this file sends a model request.
# Each group requires a new explicit authorization. Do not reuse historical approval.
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$ProjectPython = Join-Path $ProjectRoot '.venv/Scripts/python.exe'
$commonArguments = @(
    (Join-Path $ProjectRoot 'scripts/run_ablation.py'),
    '--config', (Join-Path $ProjectRoot 'configs/phase3c/ablation_v1.json'),
    '--cases', (Join-Path $ProjectRoot 'data/mini_dev_data/mini_dev_sqlite.json'),
    '--db-root', (Join-Path $ProjectRoot 'data/minidev/MINIDEV/dev_databases')
)

# A/B: separate run IDs and output directories; combined ceiling 99 attempts.
$aArguments = @('--groups', 'A', '--group-budget', 'A=33', '--batch-budget', '33',
    '--live', '--allow-api', '--out-dir', (Join-Path $ProjectRoot 'outputs/phase3c_live/A_v1'))
$bArguments = @('--groups', 'B', '--group-budget', 'B=66', '--batch-budget', '66',
    '--live', '--allow-api', '--out-dir', (Join-Path $ProjectRoot 'outputs/phase3c_live/B_v1'))

# C/D require their own later approval; combined ceiling 165 attempts.
$cArguments = @('--groups', 'C', '--group-budget', 'C=66', '--batch-budget', '66',
    '--live', '--allow-api', '--out-dir', (Join-Path $ProjectRoot 'outputs/phase3c_live/C_v1'))
$dArguments = @('--groups', 'D', '--group-budget', 'D=99', '--batch-budget', '99',
    '--live', '--allow-api', '--out-dir', (Join-Path $ProjectRoot 'outputs/phase3c_live/D_v1'))

# Only after new authorization and all required preflight blockers are resolved:
# Remove-Item Env:PYTHON_DOTENV_DISABLED -ErrorAction SilentlyContinue
# $env:LANGSMITH_TRACING = 'false'
# $env:LANGCHAIN_TRACING_V2 = 'false'
# & $ProjectPython @commonArguments @aArguments
# if ($LASTEXITCODE -ne 0) { throw 'A stopped: inspect evidence; do not retry or start B automatically.' }
# & $ProjectPython @commonArguments @bArguments
# if ($LASTEXITCODE -ne 0) { throw 'B stopped: inspect evidence; do not retry.' }
#
# Separate later C/D authorization:
# & $ProjectPython @commonArguments @cArguments
# if ($LASTEXITCODE -ne 0) { throw 'C stopped: inspect evidence; do not retry or start D automatically.' }
# & $ProjectPython @commonArguments @dArguments
# if ($LASTEXITCODE -ne 0) { throw 'D stopped: inspect evidence; do not retry.' }
