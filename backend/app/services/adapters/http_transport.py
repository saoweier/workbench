"""Bounded HTTP transport for configured providers, without automatic retries."""
from __future__ import annotations
import httpx
from ..network_policy import proxies_apply
from .base import TransportResponse

class HttpTransport:
    def request(self, method, url, *, headers, json_body=None, timeout=60, **kwargs):
        # 本机/内网地址直连：走系统代理既没意义，还会把「端口没人监听」
        # 变成代理返回的 502，让错误信息指向完全错误的方向。
        try:
            with httpx.Client(timeout=timeout, follow_redirects=False,
                              trust_env=proxies_apply(url)) as client:
                response = client.request(method, url, headers=headers, json=json_body,
                                          params=kwargs.get("params"))
        except httpx.TimeoutException as exc:
            raise TimeoutError("Provider request timed out") from exc
        except httpx.ConnectError as exc:
            # 单独区分出来：调用方要给「连不上」和「返回了错误状态」两套话术。
            raise ConnectionRefusedError(str(exc)) from exc
        try:
            body = response.json()
        except ValueError:
            body = None
        return TransportResponse(status_code=response.status_code, json_body=body,
                                 text=response.text, headers=dict(response.headers))
