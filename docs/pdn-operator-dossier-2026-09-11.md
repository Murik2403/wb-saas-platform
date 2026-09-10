# Досье для ручной проверки 152‑ФЗ (для скана НормаПДн)

Дата актуализации: **11 сентября 2026**

## 0) Что мы можем сделать здесь и сейчас

Я могу закрыть всё, что зависит от вашего репозитория: доказательства по коду/конфигу/процессам,
а также подготовить корректный досье для ручной сверки.
Часть, где требуются внешние сервисы (`РКН`, панели провайдера TimeWeb), без доступа к вашим
аккаунтам сделать не могу — но могу сразу сказать, что именно нужно принести.

## 1) Что уже закрыто технически (в проекте)

### 1.1. Политика и cookie
- Публичный `cookie notice` с выбором реализован в общей разметке:
  - [gateway/templates/base.html](gateway/templates/base.html)
- Политика обновлена по обязательным блокам:
  - **субъекты и категории**,
  - **сроки хранения**,
  - **кросс‑бордер и передача третьим лицам**,
  - **cookies**.
  - [gateway/templates/privacy.html](gateway/templates/privacy.html)
- Страница `/compliance` со статусом уведомления РКН и локализации:
  - [gateway/templates/compliance.html](gateway/templates/compliance.html)
  - [gateway/logic/compliance_status.json](gateway/logic/compliance_status.json)

### 1.2. Контрольные проверки
- Добавлены регрессионные тесты:
  - отсутствие `fonts.googleapis.com` / `fonts.gstatic.com` на публичных страницах,
  - наличие cookie notice и ссылки на `/privacy#cookies`.
  - [gateway/tests/test_public_privacy_assets.py](gateway/tests/test_public_privacy_assets.py)
- Проверка структуры `compliance_status.json`:
  - [gateway/tests/test_compliance_status.py](gateway/tests/test_compliance_status.py)

### 1.3. Что можно быстро подтвердить повторно
```powershell
python -m unittest gateway.tests.test_public_privacy_assets gateway.tests.test_compliance_status -v
rg -n "fonts.googleapis.com|fonts.gstatic.com" gateway/templates/base.html tenant-app/app.py
rg -n "cookie-notice|cookie-accept|cookie-decline|/privacy#cookies" gateway/templates/base.html gateway/templates/privacy.html
```

## 2) Доказательная база по инфраструктуре (сколько можем зафиксировать без аккаунтов внешних сервисов)

- Скрипт резервного копирования и запуска бэкапов:
  - [scripts/backup_tenant_volumes.py](scripts/backup_tenant_volumes.py)
- Endpoint бэкапа в provisioner:
  - [provisioner/app.py](provisioner/app.py)
- Монтирование папки бэкапов в контейнер и значение `WB_SAAS_BACKUP_DIR`:
  - [docker-compose.yml](docker-compose.yml)
- Описание процесса бэкапов/восстановления и retention:
  - [DEPLOY.md](DEPLOY.md)

## 3) Что нужно приложить вручную (у вас, с провайдерских систем)

### A) РКН (критично)
1. Подтверждение в реестре по ИНН:
   - `071513433550`
2. Статус поданной/неподанной формы:
   - номер, дата подачи, screenshot страницы проверки статуса.
3. Обновить [gateway/logic/compliance_status.json](gateway/logic/compliance_status.json), поле:
   - `rkn.status`
   - `rkn.status_hint`

### B) Верификация локализации и инфраструктуры
1. Где физически живет:
   - БД control-plane и tenant volumes;
   - логи контейнеров;
   - backup-директория и место хранения архивов.
2. Подтверждение retention:
   - политика хранения логов (дни/доступ);
   - политика хранения backup (дни/количество копий);
   - есть ли офф-сайт/дополнительная копия.
3. Наличие договорного документа (SLA/договор с провайдером) по каждому пункта.

### C) Регламент инцидента
1. Куда уходят уведомления:
   - первичный контакт,
   - дубль ответственному,
   - техдир/админ (если есть).
2. Подтверждение порядка оповещения и протокола теста.

## 4) Наша задача после ваших вложений

Как только вы пришлёте сканы/ссылки из разделов 3A–3C,
я обновлю `compliance_status.json` и `docs/compliance-maintenance-checklist.md`
до состояния **готового к скану досье** и дам короткий список:
что закрыто, что осталось только подстраховкой по регламенту.
