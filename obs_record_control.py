"""Small local OBS Studio recorder controller.

The WebSocket password is intentionally never stored in this file or in the
repository. It is requested interactively at runtime, or read from the
MARKETSHELPER_OBS_PASSWORD environment variable.

Examples:
    python obs_record_control.py status
    python obs_record_control.py start
    python obs_record_control.py stop
    python obs_record_control.py toggle
"""

from __future__ import annotations

import argparse
import base64
import getpass
import hashlib
import json
import os
import socket
import sys
import uuid

try:
    import websocket
except ImportError:
    print("Не найден пакет websocket-client. Установите зависимости командой:"
          " python -m pip install -r requirements-obs.txt", file=sys.stderr)
    raise SystemExit(2)


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 4455


def _auth_string(password: str, salt: str, challenge: str) -> str:
    secret = base64.b64encode(hashlib.sha256((password + salt).encode()).digest()).decode()
    auth = base64.b64encode(hashlib.sha256((secret + challenge).encode()).digest()).decode()
    return auth


class OBSClient:
    def __init__(self, host: str, port: int, password: str, timeout: float = 5.0) -> None:
        self.url = f"ws://{host}:{port}"
        self.password = password
        self.ws = websocket.create_connection(self.url, timeout=timeout)
        self._identify()

    def _send(self, op: int, data: dict | None = None) -> None:
        message = {"op": op}
        if data:
            message["d"] = data
        self.ws.send(json.dumps(message))

    def _receive(self) -> dict:
        return json.loads(self.ws.recv())

    def _identify(self) -> None:
        hello = self._receive()
        if hello.get("op") != 0:
            raise RuntimeError("OBS не прислал приветственное сообщение")
        hello_data = hello.get("d", {})
        identify_data = {"rpcVersion": 1}
        auth = hello_data.get("authentication")
        if auth:
            identify_data["authentication"] = _auth_string(
                self.password, auth["salt"], auth["challenge"]
            )
        self._send(1, identify_data)
        identified = self._receive()
        if identified.get("op") != 2:
            reason = identified.get("d", {}).get("error", "неизвестная ошибка")
            raise RuntimeError(f"OBS отклонил подключение: {reason}")

    def request(self, request_type: str, request_data: dict | None = None) -> dict:
        request_id = str(uuid.uuid4())
        self._send(6, {
            "requestType": request_type,
            "requestId": request_id,
            "requestData": request_data or {},
        })
        while True:
            response = self._receive()
            if response.get("op") != 7:
                continue
            data = response.get("d", {})
            if data.get("requestId") != request_id:
                continue
            if data.get("requestStatus", {}).get("result") is not True:
                comment = data.get("requestStatus", {}).get("comment", "без описания")
                raise RuntimeError(comment)
            return data.get("responseData", {})

    def close(self) -> None:
        self.ws.close()


def get_password() -> str:
    password = os.environ.get("MARKETSHELPER_OBS_PASSWORD", "")
    if password:
        return password
    return getpass.getpass("Пароль OBS WebSocket (не отображается): ")


def main() -> int:
    parser = argparse.ArgumentParser(description="Управление записью OBS Studio через WebSocket")
    parser.add_argument("command", choices=["status", "start", "stop", "toggle"])
    parser.add_argument("--host", default=os.environ.get("MARKETSHELPER_OBS_HOST", DEFAULT_HOST))
    parser.add_argument("--port", type=int, default=int(os.environ.get("MARKETSHELPER_OBS_PORT", DEFAULT_PORT)))
    args = parser.parse_args()

    try:
        client = OBSClient(args.host, args.port, get_password())
    except (OSError, socket.error) as exc:
        print(f"Не удалось подключиться к OBS на {args.host}:{args.port}: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Ошибка подключения к OBS: {exc}", file=sys.stderr)
        return 1

    try:
        status = client.request("GetRecordStatus")
        recording = bool(status.get("outputActive"))
        if args.command == "status":
            print("Запись идёт" if recording else "Запись не идёт")
        elif args.command == "start":
            if recording:
                print("Запись уже идёт")
            else:
                client.request("StartRecord")
                print("Запись запущена")
        elif args.command == "stop":
            if recording:
                client.request("StopRecord")
                print("Запись остановлена")
            else:
                print("Запись уже остановлена")
        elif args.command == "toggle":
            client.request("StopRecord" if recording else "StartRecord")
            print("Запись остановлена" if recording else "Запись запущена")
        return 0
    except Exception as exc:
        print(f"Команда OBS не выполнена: {exc}", file=sys.stderr)
        return 1
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
