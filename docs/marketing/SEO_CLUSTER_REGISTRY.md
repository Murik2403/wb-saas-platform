# SEO cluster registry MARKETSHELPER

Дата фиксации: 20.09.2026  
Следующий общий пересмотр: 27.09.2026  
Статус документа: рабочий реестр для SEO-итераций, без обещания трафика или индексации.

## Правила реестра

- Один поисковый интент имеет один основной URL. Синонимичные формулировки `WB`, `ВБ` и `Wildberries` не получают отдельные страницы.
- Частотность, конкуренция, позиции и поисковые конверсии здесь не указаны: они не были измерены. До получения данных из Яндекс Вебмастера и Search Console спрос имеет статус `unverified`.
- `route verified` означает, что маршрут присутствует в `gateway/marketing_routes.py` или `gateway/app.py` и покрыт текущими маршрутными/SEO-проверками. Это не доказывает индексацию.
- Внутренние ссылки должны оставаться обычными ссылками без UTM. Новые URL в этой итерации не создаются.

## Начальные кластеры

| ID | Кластер и примеры намерения | Интент | Основной URL | Поддерживающие URL | Источник доказательств | Статус | Следующий пересмотр |
|---|---|---|---|---|---|---|---|
| C01 | Себестоимость товара на WB: как рассчитать себестоимость, что входит в себестоимость, расчёт себестоимости товара | Информационный с переходом к проверке своих данных | [`/sebestoimost-wb`](https://marketshelper.ru/sebestoimost-wb) | `/calculator`, `/fifo-uchet`, `/guides/pribyl-i-rashody-wb`, `/dlya-proizvoditeley-wb`, `/guides/iz-excel-v-uchet` | `docs/marketing/SEO_SEBESTOIMOST_WB_REVIEW.md`, `gateway/templates/marketing_cost.html`, `gateway/tests/test_marketing_routes.py` | route verified; demand unverified | 27.09.2026 |
| C02 | Программа учёта себестоимости и себестоимость по партиям WB | Коммерческо-инструментальный: выбрать способ вести собственные данные | [`/sebestoimost-wb#service`](https://marketshelper.ru/sebestoimost-wb#service) | `/register`, `/pricing`, `/dlya-proizvoditeley-wb`, `/guides/fifo-po-partiyam` | Review, блок «Связь с продуктом»; product claims сверены с `tenant-app/db/fifo_materials.py`; CTA в `marketing_base.html` | route/claim evidence verified; demand unverified | 27.09.2026 |
| C03 | Калькулятор себестоимости и предварительной маржи WB | Транзакционно-инструментальный: подставить свои значения | [`/calculator`](https://marketshelper.ru/calculator) | `/sebestoimost-wb#unit-cost`, `/pricing`, `/register`, `/guides/pribyl-i-rashody-wb` | `gateway/templates/marketing_calculator_page.html`, `gateway/tests/test_seo.py`, Review; расчёт обозначен как предварительная маржа | route verified; demand unverified | 27.09.2026 |
| C04 | FIFO и меняющаяся закупочная цена: учёт по партиям, пример FIFO, FIFO против средней | Информационный: понять метод и стоимость остатка | [`/fifo-uchet`](https://marketshelper.ru/fifo-uchet) | `/guides/fifo-po-partiyam`, `/guides/fifo-ili-srednyaya-v-akcii`, `/sebestoimost-wb#unit-cost`, `/dlya-proizvoditeley-wb` | Review; `gateway/templates/marketing_fifo.html`; `docs/content/03-fifo-vs-srednyaya.md`; Graphify связи с FIFO-источниками | route/content evidence verified; demand unverified | 27.09.2026 |
| C05 | Себестоимость собственного производства для WB: материалы, выпуск партии, стоимость изделий | Коммерческо-информационный: связать сырьё, выпуск и товар | [`/dlya-proizvoditeley-wb`](https://marketshelper.ru/dlya-proizvoditeley-wb) | `/sebestoimost-wb#service`, `/fifo-uchet`, `/guides/nezavershennoe-proizvodstvo`, `/register` | Review; `gateway/templates/marketing_producers.html`; product source `tenant-app/db/fifo_materials.py` | route/claim evidence verified; demand unverified | 27.09.2026 |
| C06 | Расходы и финансовый результат на WB: прибыль с учётом себестоимости, почему выплата не равна прибыли | Информационный с коммерческим переходом: сверить результат периода | [`/guides/pribyl-i-rashody-wb`](https://marketshelper.ru/guides/pribyl-i-rashody-wb) | `/sebestoimost-wb#unit-cost`, `/calculator`, `/pricing`, `/register` | Review; `gateway/templates/marketing_guide_profit.html`; existing finance/margin semantics in project tests/docs | route/content evidence verified; demand unverified | 27.09.2026 |
| C07 | Переход из Excel к учёту партий: подготовка данных и сверка одного товара | Информационный с коммерческим переходом: подготовить миграцию | [`/guides/iz-excel-v-uchet`](https://marketshelper.ru/guides/iz-excel-v-uchet) | `/sebestoimost-wb#unit-cost`, `/fifo-uchet`, `/dlya-proizvoditeley-wb`, `/register` | Review; `gateway/templates/marketing_guide_excel.html`; `docs/content/01-sebestoimost-wildberries.md` | route/content evidence verified; demand unverified | 27.09.2026 |

## Коммерческие страницы и поддерживающие маршруты

Эти страницы уже существуют и используются в воронке. Они не являются новыми SEO-кластерами, пока не получено отдельное доказательство спроса.

| URL | Роль | Связанные кластеры | Проверка маршрута | Статус спроса |
|---|---|---|---|---|
| [`/pricing`](https://marketshelper.ru/pricing) | Тариф, условия оплаты и переход к регистрации | C02, C03, C05, C06, C07 | `gateway/app.py`; `gateway/tests/test_seo.py`; sitemap | unverified |
| [`/register`](https://marketshelper.ru/register) | Регистрация и начало пробного сценария | Все коммерческие кластеры | `gateway/app.py`; private/noindex checks | не продвигать как SEO-посадочную; служебный маршрут |
| [`/`](https://marketshelper.ru/) | Главная и распределение ссылочного веса | Все кластеры | `gateway/app.py`; sitemap/structured-data tests | unverified |
| [`/guides`](https://marketshelper.ru/guides) | Индекс руководств и навигация | C04, C06, C07 и дополнительные guide-темы | `gateway/marketing_routes.py`; sitemap | unverified |
| [`/sverka-ostatkov-wb`](https://marketshelper.ru/sverka-ostatkov-wb) | Сервисная страница сверки остатков | Отдельный кластер сверки; не смешивать с C01 | `gateway/marketing_routes.py`; sitemap | unverified |

## Реестр существующих руководств

| URL | Назначение | Основной кластер/связь | Статус |
|---|---|---|---|
| [`/guides/fifo-po-partiyam`](https://marketshelper.ru/guides/fifo-po-partiyam) | Подробный числовой пример списания партий | C04 | route verified; demand unverified |
| [`/guides/fifo-ili-srednyaya-v-akcii`](https://marketshelper.ru/guides/fifo-ili-srednyaya-v-akcii) | Проверка цены акции по стоимости партий | C04, поддерживающий сценарий | route verified; demand unverified |
| [`/guides/pribyl-i-rashody-wb`](https://marketshelper.ru/guides/pribyl-i-rashody-wb) | Состав расходов и проверка результата | C06 | route verified; demand unverified |
| [`/guides/iz-excel-v-uchet`](https://marketshelper.ru/guides/iz-excel-v-uchet) | Контрольный сценарий перехода из таблицы | C07 | route verified; demand unverified |
| [`/guides/sverka-ostatkov-wb`](https://marketshelper.ru/guides/sverka-ostatkov-wb) | Сверка остатков, товара в пути и пересорта | отдельный поддерживающий кластер | route verified; demand unverified |
| [`/guides/nezavershennoe-proizvodstvo`](https://marketshelper.ru/guides/nezavershennoe-proizvodstvo) | НЗП между сырьём, выпуском и продажей | C05, C06 | route verified; demand unverified |
| [`/guides/zamena-servisov-analitiki`](https://marketshelper.ru/guides/zamena-servisov-analitiki) | Выбор класса сервиса аналитики | отдельный сравнительный интент | route verified; demand unverified |

## Контроль каннибализации

- C01 владеет объяснением состава и расчёта себестоимости. Не создавать отдельные страницы под «себестоимость WB», «себестоимость ВБ» и «себестоимость Wildberries».
- C03 владеет вводом чисел и предварительной маржой; не дублировать полный калькулятор в C01 или руководствах.
- C04 владеет методом FIFO; подробные числовые разборы остаются в `/guides/fifo-po-partiyam`, а акция — в `/guides/fifo-ili-srednyaya-v-akcii`.
- C05 владеет собственным производством; C06 — расходами продажи и результатом. Не смешивать стоимость товара с комиссией, логистикой, рекламой, налогами и общими расходами.
- C07 объясняет подготовку и сверку данных из Excel. Не обещать универсальный импорт произвольного XLSX.
- Сравнительные кластеры с бухгалтерскими системами исключены из публичной SEO-очереди: отдельного маршрута и подтверждённой продуктовой задачи для них нет.
- Для нового кластера сначала нужна запись в этом реестре, подтверждённый существующий маршрут или отдельное решение о создании маршрута, источник фактов и план перелинковки.

## Источники и ограничения

- Основной редакционный источник: `docs/marketing/SEO_SEBESTOIMOST_WB_REVIEW.md` (проверка 20.09.2026).
- Реестр маршрутов: `gateway/marketing_routes.py`; системные публичные страницы, robots и sitemap: `gateway/app.py`.
- Регрессионная проверка: `gateway/tests/test_marketing_routes.py` и `gateway/tests/test_seo.py`. Эти тесты подтверждают серверный маршрут, мета-теги, canonical, sitemap, JSON-LD и внутреннюю навигацию, но не индексирование.
- Graphify использован для поиска связей между review, маршрутами, SEO-тестами и FIFO-источниками. Ключевые продуктовые утверждения должны дополнительно сверяться с актуальным кодом перед публикацией.
- Отсутствуют в этом документе: частотность, позиции, прогнозы кликов, стоимость лида, отзывы клиентов и обещания поискового трафика. Их можно добавить только с датой, источником и URL/срезом данных.
