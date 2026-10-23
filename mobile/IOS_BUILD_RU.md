# Сборка Marketshelper для iPhone

Используется существующее приложение Capacitor: вход через https://marketshelper.ru/login,
затем действующий кабинет пользователя. Идентификатор: ru.marketshelper.app.

## Автоматическая сборка

Workflow .github/workflows/ios-build.yml запускается при отправке ветки
codex/ios-build-20261023 или вручную через GitHub Actions.
Он устанавливает зависимости, проверяет JavaScript, синхронизирует iOS,
собирает приложение для симулятора и неподписанный пакет для устройства.

Артефакты: Marketshelper-simulator.zip, Marketshelper-unsigned.ipa, SHA256SUMS.txt.
Неподписанный IPA нельзя установить на iPhone без подписи Apple и provisioning profile.
Workflow не публикует приложение и не использует аккаунты покупателей.

## На Mac

В каталоге mobile выполните:
```sh
npm ci
npm run typecheck
npm run build
npx cap sync ios
bash scripts/build-ios.sh
```

Для подписанной версии откройте ios/App/App.xcodeproj в Xcode,
выберите свою Apple Developer Team, настройте профиль и выполните Archive / Distribute.
Для TestFlight потребуется доступ к Apple Developer и App Store Connect.

## Состояние 23 октября 2026

iOS-компиляция в этой сессии не выполнена: Windows, Swift отсутствует,
WSL возвращает E_ACCESSDENIED, Docker недоступен.
GitHub CLI сообщает о недействительном токене; удалённый workflow не запускался.
Проверка на физическом iPhone, входа и экспорта ещё требуется.
Push не реализован полностью.

xtool предназначен для SwiftPM-приложений. Здесь сохранена существующая
Capacitor-реализация с плагинами; совместимость её сборки с xtool не подтверждена.

Локально: TypeScript typecheck и сборка JS прошли с установленным ранее компилятором соседнего checkout. Чистая установка npm ci не завершилась: DNS ENOTFOUND registry.npmjs.org. Проверка bash заблокирована средой Windows; выполнение Xcode workflow не проверено.
