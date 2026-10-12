# Автоматическое восстановление процессов и запуск после перезагрузки

Проверка 13 октября 2026 года по московскому времени. Исходная ревизия проверки —
`7a9fd88`; production gateway/provisioner остаются на runtime `b13fa97`, пять
кабинетов — на `e7b1582`. Код приложения, Compose, systemd и настройки Docker
не изменялись. Docker и сервер на production не перезапускались.

## Production: фактически проверено

Read-only запросы выполнены 12 октября в 21:19:48 UTC:

- Все десять работающих контейнеров — gateway, provisioner, пять кабинетов,
  Traefik, управляющий и discovery Docker proxy — имеют
  `RestartPolicy.Name=unless-stopped`, `MaximumRetryCount=0`.
- Gateway, provisioner и кабинеты имеют `healthy`. У всех десяти
  `RestartCount=0`, `OOMKilled=false`. Публичные `/healthz` всех пяти кабинетов
  отвечают `{"status":"ok"}`.
- Provisioner создан через Compose run, но сохранённая после выкладки политика
  `unless-stopped` действительно установлена: ограничение Compose run из
  [акта выкладки](BACKUP_REFUND_RELEASE_2026-10-12.md) проверено повторно.
- 89 сохранённых остановленных контейнеров также имеют `unless-stopped`.
  Остановленных контейнеров с `always` нет. Это важно: вручную остановленные
  rollback-контейнеры не должны сами включаться после перезапуска Docker.
- `docker.service`, `docker.socket`, `containerd.service` и `cron.service`
  загружены, активны и включены для запуска при загрузке ОС. Для Docker и
  containerd systemd задаёт `Restart=always`, для cron — `on-failure`.
- Docker упорядочен после `docker.socket`, `containerd.service` и
  `network-online.target`; socket упорядочен перед Docker. Отдельного
  project-specific systemd unit, запускающего Compose при загрузке, не найдено.
- Production Docker Engine — `29.7.2`, Ubuntu `22.04.5`.
  `LiveRestoreEnabled=false`; собственных значений в `/etc/docker/daemon.json`
  для live-restore/shutdown-timeout не обнаружено. Это не доказательство
  непрерывной доступности при перезапуске Docker: сейчас live-restore выключен.

Ни окружение контейнеров, ни секреты, ни содержимое баз, ни журналы с личными
данными в evidence не сохранялись. Сохранены только метаданные состояния,
политик, сетевых алиасов, systemd и health.
Повторный read-only осмотр в 21:24:32 UTC подтвердил неизменность IDs, образов,
политик, RestartCount, времени запуска и running-state всех 99 текущих и
сохранённых контейнеров. Время загрузки хоста также не изменилось.

## Порядок запуска и его предел

В эффективном Compose подтверждены связи:

| Сервис | Зависимость | Условие Compose |
| --- | --- | --- |
| Gateway | Provisioner | `service_healthy` |
| Provisioner | Управляющий Docker proxy | `service_started` |
| Traefik | Discovery Docker proxy | `service_started` |

Это правила операций Compose. При автоматическом восстановлении контейнеров
самим Docker после загрузки хоста нельзя считать, что были выполнены эти
Compose health-ожидания. Ни запуск всей цепочки после reboot, ни отсутствие
коротких 502/503 на время старта этим read-only осмотром не подтверждены.
Текущие политики подтверждают конфигурацию автозапуска, а не полный сценарий
аварии сервера. Объяснение политики и условий запуска сверено с официальными
[Docker restart policies](https://docs.docker.com/engine/containers/start-containers-automatically/)
и [Compose startup order](https://docs.docker.com/compose/how-tos/startup-order/).

Обычный `docker compose up --force-recreate provisioner` на production по-прежнему
не использовать без проверки: dry-run предыдущей выкладки показал удаление
17 сохранённых rollback-контейнеров с теми же service labels. Эта проверка
не удаляла их и не выполняла Compose up.

## Изолированная локальная проверка

На Docker Desktop Engine `29.8.1` запущен только синтетический контейнер из
уже имеющегося локального образа. Сеть отключена, порты не опубликованы,
root filesystem read-only, capabilities сняты, лимит памяти — 64 MiB.
Рабочие данные — отдельный подписанный audit-меткой named volume; базы,
ключи и тома production не использованы.

Проверяется именно завершение основного процесса, а не ручной Docker stop:
после первого успешного запуска PID 1 получает синтетический маркер и выходит
с кодом 42. Выполнено два таких цикла. Docker автоматически запускает процесс
снова в том же контейнере, увеличивает RestartCount, а записанный в том счётчик
запусков сохраняется. Затем ручной stop остаётся остановленным, явный start
восстанавливает процесс с теми же данными. Точные длительности и результаты
зафиксированы в `local-restart-proof.json` после успешного запуска.
Последний запуск: автоматическое восстановление за 0,969 и 0,953 секунды,
счётчик запусков — 2 и 3; после ручного stop/start — 4. OOM не было.

После проверки удалены только созданные ею контейнер и named volume с
совпадающей уникальной audit-меткой. Другие локальные контейнеры и Docker
Desktop не перезапускались.
Повторный поиск Docker-объектов с audit-меткой подтвердил, что созданных этой
проверкой контейнеров и томов не осталось.

## Что можно считать закрытым

- Фактические restart policies текущих production контейнеров и сохранённых
  остановленных экземпляров проверены.
- Enablement и ordering Docker/containerd/socket/cron проверены read-only.
- Автовосстановление после падения основного процесса, сохранность отдельного
  тома и отличие ручного stop от падения подтверждены локальным экспериментом.

**Не закрыто:** реальный перезапуск Docker/хоста с восстановлением всей цепочки
production, TLS/сети/маршрутизация после загрузки, recovery приложения при
неготовности зависимостей, последствия зависшего, но не завершившегося PID 1.
Для этих проверок нужен отдельный двойник всего стека или согласованное окно
обслуживания; в текущем задании production не перезапускается.

Evidence и запускаемые операторы:
`deliveries/restart-recovery-20261013/{read_live.py,live-restart-proof.json,local_restart_gate.py,local-restart-proof.json}`.
Подтверждение неизменности production:
`deliveries/restart-recovery-20261013/live-unchanged-proof.json`.
