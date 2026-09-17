$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$qwen = 'C:\Users\mvvor\Downloads\qwen38-27b-rtx3090\qwen.ps1'

$graph = @(
    (graphify query "tenant isolation authentication x-tenant-id gateway tenant-api" --budget 850 2>&1 | Out-String)
    (graphify query "financial calculations margin_rub FIFO reconciliation data accuracy" --budget 850 2>&1 | Out-String)
    (graphify query "backup restore Docker deployment secrets privacy compliance" --budget 850 2>&1 | Out-String)
) -join "`n--- GRAPH QUERY ---`n"

$files = @(
    'AGENTS.md','docker-compose.yml','gateway/app.py','gateway/config.py','gateway/internal_routes.py',
    'gateway/client_ip.py','gateway/logic/accounts.py','gateway/logic/compliance.py','tenant-app/api_server.py',
    'tenant-app/calculations.py','tenant-app/db/wb_fifo_reconciliation.py','tenant-app/backup_tools.py',
    'mh-redesign-lab/tenant-api/api_server.py','mh-redesign-lab/src/data-source.ts','frontend/src/lib/overview.ts',
    'docs/PROJECT_AUDIT_2026-09-09.md','AUDIT_2026-09-10.md','tenant-app/tests/test_api_server_health.py',
    'tenant-app/tests/test_wb_fifo_reconciliation.py','gateway/tests/test_internal_routes.py'
)
$evidence = foreach ($relative in $files) {
    $path = Join-Path $root $relative
    if (Test-Path $path) {
        $text = Get-Content $path -Raw
        if ($text.Length -gt 2200) { $text = $text.Substring(0, 2200) + "`n[excerpt clipped; inspect file in follow-up]" }
        "`n===== $relative =====`n$text"
    }
}

$prompt = @"
Ты проводишь независимый инженерный аудит MARKETSHELPER по предоставленным выдержкам кода и карте Graphify.
Дата аудита: 2026-09-13. Цель: найти реальные риски, которые могут привести к утечке данных между tenant, неверной финансовой аналитике, потере данных, неработающему продакшену или нарушению privacy/compliance.

Правила:
1. Не выдумывай отсутствующий код и не считай документированный план доказательством реализации.
2. Для каждого вывода укажи severity P0/P1/P2/P3, конкретный файл и строку/символ из evidence.
3. Разделяй VERIFIED (видно в коде), INDICATED (сильный сигнал) и UNKNOWN (нужна проверка).
4. Не повторяй старые аудиты без новой проверки; проверь их утверждения по исходникам.
5. Не показывай секреты и не проси их.
6. Сначала дай 5-8 главных findings, затем таблицу проверок и короткий план исправлений по приоритету.
7. Если доказательств мало, прямо напиши, какой тест или команда нужна для подтверждения.

КАРТА GRAPHIFY:
$graph

ВЫДЕРЖКИ ФАЙЛОВ:
$($evidence -join "`n")

Формат ответа:
# MARKETSHELPER audit by local Qwen
## Executive verdict
## Findings
Для каждого: [P?] [VERIFIED/INDICATED/UNKNOWN] заголовок; Evidence; Impact; Validation/fix.
## Coverage and unknowns
## Priority next actions
"@

& $qwen -Prompt $prompt -MaxTokens 1800 -Temperature 0
