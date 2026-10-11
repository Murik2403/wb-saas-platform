# App Store Connect: App Privacy (ответы по реальным данным)

Источник: https://marketshelper.ru/privacy. Tracking (по определению Apple) приложение НЕ выполняет: данные не связываются с данными других компаний для рекламы и не передаются брокерам. App Tracking Transparency не нужен; NSUserTrackingUsageDescription не добавлять.

- Privacy Policy URL: https://marketshelper.ru/privacy
- Удаление аккаунта (гайдлайн 5.1.1(v)): https://marketshelper.ru/delete-account. Apple требует, чтобы удаление можно было начать из приложения: ссылка на эту страницу должна быть доступна из кабинета (настройки аккаунта). Проверить до подачи.

## Data collected (все Linked to You; Used for Tracking: No)
| Тип Apple | Данные | Цель |
|---|---|---|
| Contact Info: Email Address | email аккаунта | App Functionality, account management |
| Identifiers: User ID | внутренний ID аккаунта, необязательный Telegram ID | App Functionality |
| Purchases: Purchase History | статусы и даты оплаты подписки (оплата на сайте через ЮKassa; в приложении покупок нет) | App Functionality |
| Financial Info: Other Financial Info | данные кабинета продавца: выручка, расходы, себестоимость | App Functionality |
| User Content: Other User Content | введённые пользователем данные: товары, закупки, остатки, отчёты | App Functionality |
| Usage Data: Product Interaction | журнал действий в кабинете | App Functionality, Security |
| Diagnostics: Other Diagnostic Data | IP, User-Agent, cookie-сессии, ошибки | App Functionality, Security |
| Identifiers: Device ID | push-токен APNs (после включения push) | App Functionality (notifications) |
| Usage Data: Product Interaction (аналитика) | Яндекс Метрика, только после согласия на сайте, без Вебвизора | Analytics |

Токены API WB/Ozon (шифруются) отдельного типа у Apple не имеют: относятся к Other User Content / Other Financial Info.

## Data NOT collected
Location, Contacts, Photos or Videos, Audio, Health and Fitness, Browsing History, Search History, Sensitive Info, Advertising Data, данные платёжной карты (карту видит только ЮKassa).

## Export compliance
Только стандартное HTTPS: `ITSAppUsesNonExemptEncryption = NO` (уже стоит в ios/App/App/Info.plist).

## Риски отклонения
- 3.1.1 (IAP): в приложении нет продажи цифровых товаров. Не добавляйте кнопки «купить/оплатить» и призывы оплатить на сайте внутри приложения без согласования.
- 4.2 (minimum functionality): обёртка над сайтом может быть отклонена как web clip. Смягчение: push-уведомления, системные share/файлы, deep links, офлайн-экран, полноценный кабинет, требующий аккаунта. Пояснение в review-notes.md.
- Sign in with Apple не нужен (нет входа через соцсети).
