from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(slots=True)
class ProxySetting:
    enabled: bool
    server: str = ""
    port: int = 0


@dataclass(slots=True)
class ServiceProxyState:
    service: str
    web: ProxySetting
    secure_web: ProxySetting
    socks: ProxySetting


Runner = Callable[[list[str]], str]


def _default_runner(args: list[str]) -> str:
    result = subprocess.run(args, check=True, capture_output=True, text=True)
    return result.stdout


def _parse_setting(text: str) -> ProxySetting:
    values: dict[str, str] = {}
    for line in text.splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            values[key.strip().lower()] = value.strip()
    return ProxySetting(
        enabled=values.get("enabled", "no").lower() == "yes",
        server=values.get("server", ""),
        port=int(values.get("port", "0") or 0),
    )


class MacProxyManager:
    def __init__(self, state_path: Path, runner: Runner = _default_runner):
        self.state_path = state_path
        self.runner = runner

    def list_services(self) -> list[str]:
        lines = self.runner(["networksetup", "-listallnetworkservices"]).splitlines()
        return [line.strip().lstrip("*") for line in lines[1:] if line.strip() and not line.startswith("*")]

    def snapshot(self) -> list[ServiceProxyState]:
        states = []
        for service in self.list_services():
            states.append(ServiceProxyState(
                service=service,
                web=_parse_setting(self.runner(["networksetup", "-getwebproxy", service])),
                secure_web=_parse_setting(self.runner(["networksetup", "-getsecurewebproxy", service])),
                socks=_parse_setting(self.runner(["networksetup", "-getsocksfirewallproxy", service])),
            ))
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps([asdict(item) for item in states], ensure_ascii=False, indent=2), encoding="utf-8")
        return states

    def enable_local(self, port: int, *, consent: bool) -> None:
        if not consent:
            raise PermissionError("切换系统代理需要用户在向导中明确确认")
        if not self.state_path.exists():
            self.snapshot()
        for service in self.list_services():
            for kind in ("webproxy", "securewebproxy"):
                self.runner(["networksetup", f"-set{kind}", service, "127.0.0.1", str(port)])
                self.runner(["networksetup", f"-set{kind}state", service, "on"])

    def restore(self) -> bool:
        if not self.state_path.exists():
            return False
        raw = json.loads(self.state_path.read_text(encoding="utf-8"))
        for item in raw:
            service = item["service"]
            for field, command in (("web", "webproxy"), ("secure_web", "securewebproxy"), ("socks", "socksfirewallproxy")):
                value = item[field]
                if value["server"] and value["port"]:
                    self.runner(["networksetup", f"-set{command}", service, value["server"], str(value["port"])])
                self.runner(["networksetup", f"-set{command}state", service, "on" if value["enabled"] else "off"])
        self.state_path.unlink(missing_ok=True)
        return True

