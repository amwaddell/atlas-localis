"""Proxy-free HTTP session for the explicitly configured local trace exporter."""

import requests

from workbench.core.offline import local_url


class LocalSession(requests.Session):
    def __init__(self):
        super().__init__()
        self.trust_env = False

    def request(self, method, url, **kwargs):
        kwargs["allow_redirects"] = False
        return super().request(method, local_url(url), **kwargs)

    def send(self, request, **kwargs):
        request.url = local_url(request.url)
        kwargs["allow_redirects"] = False
        kwargs["proxies"] = {}
        response = super().send(request, **kwargs)
        if 300 <= response.status_code < 400:
            response.close()
            raise requests.HTTPError("Local exporter redirects are forbidden")
        return response
