# Управление записью OBS для видео MARKETSHELPER

## 1. Установить зависимость

В PowerShell из корня проекта:

```powershell
python -m pip install -r requirements-obs.txt
```

## 2. Проверить подключение

Скрипт использует `127.0.0.1:4455`, потому что OBS и скрипт запускаются на одном компьютере:

```powershell
python obs_record_control.py status
```

Пароль вводится в консоли и не отображается. Он не сохраняется в файле и не попадает в историю команд.

## 3. Управлять записью

```powershell
python obs_record_control.py start
python obs_record_control.py stop
python obs_record_control.py toggle
```

## 4. Если нужен другой адрес

Можно указать адрес и порт параметрами:

```powershell
python obs_record_control.py status --host 192.168.1.42 --port 4455
```

Или задать переменные окружения на время текущего окна PowerShell:

```powershell
$env:MARKETSHELPER_OBS_HOST = "127.0.0.1"
$env:MARKETSHELPER_OBS_PORT = "4455"
$env:MARKETSHELPER_OBS_PASSWORD = "введите_пароль_локально"
python obs_record_control.py start
```

Переменную с паролем не добавляйте в `.env`, git, скрипты или документацию проекта.

## 5. Проверка перед первой записью

1. В OBS должна быть выбрана нужная сцена и источник экрана.
2. Проверьте область захвата MARKETSHELPER.
3. Проверьте микрофон и уровень звука.
4. Выполните `status`.
5. Запустите короткую тестовую запись командой `start`.
6. Остановите её командой `stop` и проверьте файл.

## Что делает скрипт

Скрипт подключается к встроенному OBS WebSocket, проверяет состояние записи и выполняет только команды `GetRecordStatus`, `StartRecord` и `StopRecord`. Пароль не записывается в проект.
