from __future__ import annotations

import json
import time
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


@dataclass(slots=True)
class Response:
    status: int
    body: bytes
    headers: dict[str, str]

    def json(self) -> dict:
        return json.loads(self.body.decode("utf-8"))

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")


class HttpClient:
    def request(self, url: str, headers: dict[str, str] | None = None, retries: int = 2) -> Response:
        last: Exception | None = None
        for attempt in range(retries + 1):
            try:
                with urlopen(Request(url, headers=headers or {}), timeout=25) as res:
                    return Response(res.status, res.read(), dict(res.headers.items()))
            except HTTPError as exc:
                return Response(exc.code, exc.read(), dict(exc.headers.items()))
            except (URLError, TimeoutError) as exc:
                last = exc
                if attempt < retries:
                    time.sleep(0.4 * (2**attempt))
        raise RuntimeError(f"网络请求失败: {type(last).__name__}") from last


