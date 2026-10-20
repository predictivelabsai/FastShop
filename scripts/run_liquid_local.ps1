# Local sandbox with an explicit disposable SQLite database.
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
$env:PYTHONUTF8 = '1'
New-Item -ItemType Directory -Force 'output/playwright/h24you-liquid/m2-preview-data' | Out-Null
$env:DB_URL = "sqlite:///$((Join-Path $PWD 'output/playwright/h24you-liquid/m2-preview-data/fastshop.sqlite3').Replace('\', '/'))"
$env:FASTSHOP_SESSION_SECRET = [guid]::NewGuid().ToString('N') + [guid]::NewGuid().ToString('N')
$env:FASTSHOP_ENV = 'development'
$env:FASTSHOP_AUTO_CREATE_SCHEMA = '1'
$env:FASTSHOP_DATA_DIR = "$PWD/output/playwright/h24you-liquid/m2-preview-data"
$env:FASTSHOP_PUBLIC_URL = 'http://127.0.0.1:5025'
$env:XAI_API_KEY = ''
$env:POSTMARK_API_TOKEN = ''
$env:POSTMARK_SERVER_TOKEN = ''
$env:STRIPE_SECRET_KEY = ''
$env:STRIPE_WEBHOOK_SECRET = ''
uv run python -m uvicorn app.main:app --host 127.0.0.1 --port 5025 --reload
