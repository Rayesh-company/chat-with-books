# Start the Session sheet (http://localhost:8765).
# Cognee must already be up on port 8000: docker compose up -d
$keyLine = Get-Content .env | Where-Object { $_ -match '^LLM_API_KEY=' }
if (-not $keyLine) {
    Write-Error "LLM_API_KEY not found in .env (copy .env.example .env and set it first)"
    exit 1
}
$env:LLM_API_KEY = ($keyLine -replace '^LLM_API_KEY=', '').Trim('"')
python ui/serve.py
