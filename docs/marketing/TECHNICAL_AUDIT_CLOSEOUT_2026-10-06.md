# Технический остаток аудита — 6 октября 2026

## Согласованный объём

1. urllib3 и HTTP headers — изменения проверены, выпуск в процессе.
2. Мобильная приёмка, FBS и самовыкупы — изменения проверены, выпуск в процессе.
3. Ozon automatic normalization — пауза по прямому решению владельца до появления непустой статистики после отгрузки. Не реализовано и не объявляется закрытым.
4. Исторический FIFO — принят владельцем: «мной проверен, работает». Это пользовательская приёмка, а не новая документальная сверка агентом.
5. Коммерческие доказательства — закрыто по решению владельца; данные будут предоставлены позже. Интервью, реальные публичные кейсы, D7 и оплаты этим решением не подтверждаются.

## Проверенные исправления

- urllib3 2.8.0 во всех трёх Linux locks и применимых Windows constraints; остальные версии сохранены. Pip-audit: 0 известных уязвимостей в закреплённых графах; это не результат полноценного penetration test.
- Gateway: nosniff для обычных HTTP responses, SAMEORIGIN/referrer policy и CSP frame-ancestors/object-src/base-uri для HTML. Не добавляет script-src, не ломает inline theme/consent/media. HSTS остаётся ответственностью edge; middleware не обещает покрытие необработанных outer ASGI 500.
- FBS: ручной refresh не ждёт повторов 429/5xx/timeout; предыдущий snapshot сохраняется при отказе. Сумма в рублях берётся из convertedPrice при коде 643; неизвестная иностранная валюта не маркируется ₽. Статусы доставки перекрывают устаревшую сборку, просрочка только у processing.
- Cabinet: устранён горизонтальный сдвиг при viewport 320 px из-за min-width body и вертикального scrollbar.

## Приёмка и пределы

- Gateway 447 passed / 2 skipped / 203 subtests; tenant-app и agents 916 passed / 1 skipped / 10 subtests; tenant API 508 passed; provisioner 51 passed; frontend 483 passed / 73 files, TypeScript/Vite pass. Focused FBS 83 passed. Независимое финальное ревью FBS: существенных замечаний нет, 38 passed.
- Изолированная установка с require-hashes/pip check всех трёх Linux locks успешна. Совместимость requests: потоковое/gzip чтение и удаление авторизации при междоменном redirect проверены локально.
- На синтетической базе все 11 страниц при 320 px: clientWidth=scrollWidth=305; при 390/768 px также нет горизонтального overflow. Проверены светлая тема, mobile menu, Escape и возврат фокуса.
- Самовыкупы на изолированной базе: неизвестный SKU отклонён; create/read/update точны до копейки (12345−2345=10000, затем 22345−2345=20000), запись удалена и журнал пуст. Клиентские записи не изменены.
- Реальный WB FBS read: 200, 0.103 s, пустая выборка. Production FBS state/orders/analytics и SelfBuyouts GET: 200 за 0.017–0.054 s. Непустые реальные цены/статусы не наблюдались; семантика проверена контрактом и fixtures. Общий manual refresh не имеет обещанного SLA 10 s для всех страниц.
- Озвученные 16 уроков должны остаться побайтово прежними; подтверждение после выкатки будет добавлено ниже.

## Release gate

Деплой ещё не подтверждён этим документом. Требуются committed HEAD, неизменные base layers, source/package hashes, backup restore и candidate/rollback, обе production health проверки, provisioner pin/alias и media verification. Итоговые SHA и live receipts будут добавлены после выполнения.
