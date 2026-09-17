# Аудит воспроизводимости релиза — 18.09.2026

Проверка выполнена без удаления файлов, staging, commit и push.

## Что подтверждено

- `HEAD` рабочей копии: `12c912c5b8821796d0b6de118768a3a334301ee2`.
- Чистый clone из этого `HEAD` имеет нулевой вывод `git status --porcelain`.
- В чистом clone выполнены `npm ci --ignore-scripts` и `npm run build` в `mh-redesign-lab`: сборка Vite завершилась успешно, обработано 1835 модулей.
- В чистом clone присутствуют все каталоги и файлы, на которые ссылается CI: `frontend`, `mh-redesign-lab`, `gateway`, `tenant-app`, `provisioner` и их lock-файлы.
- `.dockerignore` исключает базы, резервные копии, секреты, `node_modules`, `dist`, логи и архивы из root build context.

## Найденный блокер

В чистом clone из `HEAD` отсутствуют `scripts/deploy_verified_tenant.py` и `scripts/verify_release_container.py`. В текущей рабочей копии они существуют как незакоммиченные файлы. Поэтому сборка фронтенда воспроизводится, а полный релизный цикл из clone — нет.

Кроме того, `mh-redesign-lab/Dockerfile.tenant-staging` в `HEAD` не содержит OCI label `org.opencontainers.image.revision`. В рабочей копии label уже добавлен, но его нужно включить в проверенный source commit и передавать при сборке реальный SHA исходного коммита.

## Read-only gate

Добавлен `scripts/check_clean_clone.py`. Он проверяет:

1. чистое состояние Git;
2. наличие CI, Docker и deployment source paths;
3. отсутствие секретов, runtime data, архивов и локальных окружений среди tracked paths;
4. наличие `org.opencontainers.image.revision=$REDESIGN_BUILD_ID` в tenant Dockerfile.

Команда:

```powershell
python scripts/check_clean_clone.py
```

Gate намеренно только читает состояние checkout и не выполняет `git add`, `git commit`, `git push`, удаление или сетевые операции.

