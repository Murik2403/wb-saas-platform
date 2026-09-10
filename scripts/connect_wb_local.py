"""Interactive local credential entry; never log or persist plaintext tokens."""
from getpass import getpass
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tenant-app"))
from config import KEYRING_SERVICE, KEYRING_USER
from keyring.backends.Windows import WinVaultKeyring


def main() -> int:
    print("Подключение WB на этом компьютере.")
    print("Токен будет сохранён в защищённом хранилище Windows.")
    print("Вставьте токен в это окно; символы не отображаются. Не отправляйте его в чат.")
    try:
        token = getpass("Токен WB: ").strip()
        if not token:
            print("Пустой токен. Ничего не изменено.")
            return 1
        vault = WinVaultKeyring()
        vault.set_password(KEYRING_SERVICE, KEYRING_USER, token)
        if vault.get_password(KEYRING_SERVICE, KEYRING_USER) != token:
            print("Не удалось подтвердить сохранение токена.")
            return 1
    except (KeyboardInterrupt, EOFError):
        print("Ввод отменён.")
        return 1
    except Exception:
        print("Не удалось сохранить токен в хранилище Windows. Токен не записывался в файл.")
        return 1
    print("Токен сохранён. Доступ к WB ещё не проверен.")
    print("Откройте Настройки в приложении, обновите страницу и нажмите Синхронизировать сейчас.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
