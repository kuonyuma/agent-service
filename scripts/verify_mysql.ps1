param(
    [string]$Python = ".venv\Scripts\python.exe"
)

$ErrorActionPreference = "Stop"

$testDatabaseUrl = $env:AGENT_SERVICE_TEST_DATABASE_URL
if ([string]::IsNullOrWhiteSpace($testDatabaseUrl)) {
    throw "请先设置 AGENT_SERVICE_TEST_DATABASE_URL。"
}

$databaseName = & $Python -c `
    "import os; from sqlalchemy.engine import make_url; print(make_url(os.environ['AGENT_SERVICE_TEST_DATABASE_URL']).database or '')"
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
if (-not $databaseName.ToLowerInvariant().EndsWith("_test")) {
    throw "只允许对名称以 _test 结尾的专用数据库执行迁移回滚。"
}

$previousDatabaseUrl = $env:AGENT_SERVICE_DATABASE_URL
try {
    $env:AGENT_SERVICE_DATABASE_URL = $testDatabaseUrl

    & $Python -m alembic upgrade head
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    & $Python -m alembic check
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    & $Python -m alembic downgrade base
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    & $Python -m alembic upgrade head
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    & $Python -m pytest --run-external -q `
        tests\external\test_mysql_migrations.py `
        tests\external\test_mysql_run_service.py `
        --basetemp .pytest_temp\mysql-verify
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
finally {
    $env:AGENT_SERVICE_DATABASE_URL = $previousDatabaseUrl
}
