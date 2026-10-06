# Технический остаток аудита — 6 октября 2026

## Согласованный объём

1. urllib3 и HTTP headers — закрыто и выпущено.
2. Мобильная приёмка, FBS и самовыкупы — закрыто и выпущено.
3. Ozon automatic normalization — пауза по прямому решению владельца до появления непустой статистики после отгрузки. Не реализовано и не объявляется закрытым.
4. Исторический FIFO — принят владельцем: «мной проверен, работает». Это пользовательская приёмка, а не новая документальная сверка агентом.
5. Коммерческие доказательства — закрыто по решению владельца; данные будут предоставлены позже. Интервью, реальные публичные кейсы, D7 и оплаты этим решением не подтверждаются.

## Проверенные исправления

- urllib3 2.8.0 во всех трёх Linux locks и применимых Windows constraints; остальные версии сохранены. Pip-audit: 0 известных уязвимостей в закреплённых графах; это не результат полноценного penetration test.
- Gateway: nosniff для обычных HTTP responses, SAMEORIGIN/referrer policy и CSP frame-ancestors/object-src/base-uri для HTML. Не добавляет script-src, не ломает inline theme/consent/media. HSTS остаётся ответственностью edge; middleware не обещает покрытие необработанных outer ASGI 500.
- FBS: ручной refresh не ждёт повторов 429/5xx/timeout; предыдущий snapshot сохраняется при отказе. Сумма в рублях берётся из convertedPrice при коде 643; неизвестная иностранная валюта не маркируется ₽. Статусы доставки перекрывают устаревшую сборку, просрочка только у processing.
- Cabinet: устранён горизонтальный сдвиг при viewport 320 px из-за min-width body и вертикального scrollbar.

## Приёмка и пределы

- Gateway 447 passed / 2 skipped / 203 subtests; tenant-app и agents 916 passed / 1 skipped / 10 subtests; tenant API 508 passed на первом этапе, 524 passed после финального guard; provisioner 51 passed; frontend 483 passed / 73 files, TypeScript/Vite pass. Focused FBS 83 passed. Независимое финальное ревью FBS: существенных замечаний нет, 38 passed.
- Изолированная установка с require-hashes/pip check всех трёх Linux locks успешна. Совместимость requests: потоковое/gzip чтение и удаление авторизации при междоменном redirect проверены локально.
- На синтетической базе все 11 страниц при 320 px: clientWidth=scrollWidth=305; при 390/768 px также нет горизонтального overflow. Проверены светлая тема, mobile menu, Escape и возврат фокуса.
- Самовыкупы на изолированной базе: неизвестный SKU отклонён; create/read/update точны до копейки (12345−2345=10000, затем 22345−2345=20000), запись удалена и журнал пуст. Клиентские записи не изменены.
- Реальный WB FBS read: 200, 0.103 s, пустая выборка. Production FBS state/orders/analytics и SelfBuyouts GET: 200 за 0.017–0.054 s. Непустые реальные цены/статусы не наблюдались; семантика проверена контрактом и fixtures. Общий manual refresh не имеет обещанного SLA 10 s для всех страниц.
- Озвученные 16 уроков должны остаться побайтово прежними; подтверждение после выкатки будет добавлено ниже.

## Подтверждённый выпуск

**Согласованный технический объём закрыт и выпущен.** Единственный отложенный функциональный блок из текущего списка — Ozon normalization, на паузе по решению владельца.

| Компонент | Runtime revision | Image SHA256 |
|---|---|---|
| Gateway | `f426b6bcb578de5750145c2c19895d361ea8f14d` | `044b352b6d8bd951d341ad3fd49910900878891e8e9d4a34be2eb81986016749` |
| Demo + mvvorokov | `47e655ab6886edcf2d2af11e81aa65567c51a441` | `aa19ec932120eeaf47d6f53173aae0e8ad808ec54569f747eef759659be54dc0` |
| Provisioner | `f426b6bcb578de5750145c2c19895d361ea8f14d` | `b9af39b99e706dd7b500e6368af8e43c07c1c0721251c5275bab071452994c07` |

Provisioner закреплён на `mh-tenant:audit-47e655a`, тот же pin записан в `/opt/wb-saas/.env`, alias `provisioner` сохранён. Gateway → provisioner health: HTTP200. Оба tenant имеют 1536 MiB, mounts/aliases сохранены. Traefik не менялся.

Для шлюза и обеих выкаток tenant создана зашифрованная копия, выполнено фактическое независимое восстановление, SQLite integrity и репетиции candidate/rollback. Старые контейнеры сохранены. В demo отсутствовал ключ backup: применён штатный детерминированный `tenant_data_key()` из существующего gateway master, не случайная ротация. В demo нет WB/Ozon credential-файлов; оба существующих encrypted credentials mvvorokov прочитаны текущим ключом, его environment/key не изменены.

Изолированная чистая frontend-сборка из `f426b6b`: npm audit dev+prod 0 уязвимостей; исправлен build-only `source-map-js` 1.2.1→1.2.2. Для следующего tenant patch побайтово доказано совпадение Git-объектов всех frontend build inputs, все 9 ресурсов сверены. Runtime Python parity до patch: 78 tenant и 89 gateway файлов, только ожидаемые изменения. RootFS нового образа наследует проверенную предыдущую версию.

Полный повтор API после финального guard: **524 passed**. Первоначальный повтор во время изменения mock-fixture дал 523 passed/1 failed (mock не принимал omit_raw); fixture приведён к настоящему контракту, свежий полный прогон зелёный. Прежние полный frontend483, tenant-app916 (1 skipped), gateway447 (2 skipped), provisioner51 остаются действительными для неизменённых деревьев.

### Найденный при приёмке OOM и исправление

Первая выкатка `f426b6b` выявила OOM при быстрых переходах по страницам: kernel memcg убил uvicorn 14:44:15 UTC, RSS ~1.28 GiB. Custom finance успешно завершился около 40 секунд ранее. Уже существующий cache build lock не охватывал полный lifetime потребителей и прямые P&L/FIFO-запросы; точный единственный незавершённый запрос по access-log определить нельзя.

В `47e655a` добавлен request-lifetime budget capacity1 для тяжёлых локальных dashboard/finance/legacy read routes и readonly price-advice POST. Он охватывает отправку ответа и выбранный магазин; queued GET/HEAD disconnect пропускается, POST body не теряется; отмена активного запроса сохраняет слот до фактического завершения worker. Health/session/static/FBS не блокируются. External WB analytics и WB-cost job endpoints намеренно вне budget; stock history читает без raw JSON. Формулы, WB-пагинация и лимит памяти не менялись. Budget per process; действующая конфигурация — один uvicorn worker.

- Изолированная восстановленная production copy: шесть одновременных heavy GET, все200, peak **676.7 MiB**, OOM/restarts0.
- Боевой mvvorokov: те же шесть GET, все200, **7.63–14.85 s** с учётом очереди, peak **685.8 MiB** (sampling0.25s), ни OOM, ни restart increment.
- Последующий быстрый проход 11 страниц при320px: все заголовки/контекст доступны, clientWidth=scrollWidth=305, без горизонтального overflow. Дополнительный20s memory sample после переходов: peak **806.8 MiB**, oom/oom_kill0, restart0, healthy.
- Новый холодный custom finance `2026-07-12..2026-08-10`: **200 / 6.655 s**, первое обращение после пересоздания. Предел10s выполнен. Это не обещание10s для каждого участника конкурентной очереди.
- HTTPS health шлюза/demo/mvvorokov: status=ok. Все четыре текущих контейнера healthy, restart0; Python urllib3 2.8.0 проверен.
- Все **16 MP4 +16 VTT** проверены по публичным HTTPS SHA256/размеру/MIME; для каждого MP4 Range206, immutable и nosniff. Файлы уроков и readonly media mount сохранены. Никакого нового озвучивания или замены контента в этом выпуске нет.

### Receipts и ограничения

Sanitized proof-файлы вне Git: `../../../deliveries/security-20261006/release/` и `release2/`: source-proof, deployment-proof, live-proof, final-runtime-proof, resource-*-proof, mobile-final-proof. Текущая runtime revision в таблице выше; будущий документационный commit не является новым runtime deploy.

Реальное непустое FBS содержимое, речь экранного диктора и все возможные сочетания роли/магазина не объявляются проверенными: здесь контрактные/роль-тесты, учебный CRUD и описанная live-приёмка. Это не pentest и не доказательство отсутствия всех будущих ошибок. На сервере после завершения временных восстановлений около2.5GiB свободного диска (95% занято); резервные копии и чужие артефакты не удалялись.

Первичные security источники: [urllib3 changelog](https://urllib3.readthedocs.io/en/stable/changelog.html), [source-map-js advisory](https://github.com/advisories/GHSA-68fv-2mgg-jv7q), [maintainer release1.2.2](https://github.com/7rulnik/source-map-js/releases/tag/v1.2.2).
