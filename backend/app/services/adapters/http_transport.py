"""Bounded HTTP transport for configured providers, without automatic retries."""
from __future__ import annotations
import httpx
from .base import TransportResponse

class HttpTransport:
    def request(self, method, url, *, headers, json_body=None, timeout=60, **kwargs):
        try:
            with httpx.Client(timeout=timeout, follow_redirects=False) as client:
                response = client.request(method, url, headers=headers, json=json_body,
                                          params=kwargs.get("params"))
        except httpx.TimeoutException as exc:
            raise TimeoutError("Provider request timed out") from exc
        try:
            body = response.json()
        except ValueError:
            body = None
        return TransportResponse(status_code=response.status_code, json_body=body,
                                 text=response.text, headers=dict(response.headers))
