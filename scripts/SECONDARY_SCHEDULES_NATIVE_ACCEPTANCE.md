# Изолированная проверка расписаний в точном Linux-образе

`secondary_schedules_native_acceptance.py` монтируется только для чтения. Проверка создаёт новую временную базу, синтетические магазины и новый ключ резервной копии. Производственные тома, токены и расписания не используются. Системный keyring отключён, фоновые локальные задания отключены.

Запуск в PowerShell (заменить обе обязательные строки на проверенный image ID и его полный `REDESIGN_BUILD_ID`):

```powershell
$scheduleImage = 'sha256:REPLACE_WITH_VERIFIED_IMAGE_ID'
$scheduleBuild = 'REPLACE_WITH_FULL_BUILD_REVISION'
$scheduleHelper = (Resolve-Path './scripts/secondary_schedules_native_acceptance.py').Path
$scheduleProof = Join-Path 'C:/Users/mvvor/Downloads/wbsaasplatform/release-rehearsal-tools/test-artifacts' ('schedule-native-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $scheduleProof | Out-Null
docker run --rm --network none --memory 1536m --memory-swap 1536m --cpus 2 --entrypoint python --mount "type=bind,source=$scheduleHelper,target=/acceptance.py,readonly" --mount "type=bind,source=$scheduleProof,target=/proof" $scheduleImage /acceptance.py --expected-build $scheduleBuild --output /proof/result.json
```

Образ обязан содержать именно указанный build ID в `/tmp/redesign-build-id`. В результате сохраняются хеши реально импортированных API, реестра маршрутов и модулей расписаний; root отдельно сверяет их с принятым исходным кодом кандидата. Ограничение памяти совпадает с действующим tenant: 1,5 GiB. Единственный записываемый внешний mount — новая папка доказательств.

Проверяются реальные ASGI API настройки и replay, два настоящих приватных PDF через дочерние процессы, хеши сохранённых файлов, локальный статус без доставки, зашифрованная резервная копия и восстановление, отсутствие повторного due-задания, отключение владельцем после отзыва ACL. Linux crash-проверка убивает родительский процесс настоящего executor; контролируемый ожидающий дочерний процесс обязан удерживать наследуемую блокировку до завершения.

Проверка не использует Nginx или браузер. Ответ текущей роли Gateway задаётся явной изолированной фикстурой; живая проверка Gateway выполняется отдельно. Реальные запросы WB и включение расписаний действующих пользователей не выполняются. Только успешный Linux запуск без `--local-source` является доказательством точного образа. Локальный Windows запуск проверяет исходники и PDF/backup, но пропускает Linux crash-проверку.
