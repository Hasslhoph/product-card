"""Общие фикстуры: тесты не зависят от живого DNS."""

from __future__ import annotations

import ipaddress
import socket

import pytest


@pytest.fixture
def public_dns(monkeypatch: pytest.MonkeyPatch):
    """Подменяет getaddrinfo: внешние хосты резолвятся в публичный IP без сети."""

    def fake_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):  # noqa: A002
        port_num = int(port) if port else 0
        try:
            ip = str(ipaddress.ip_address(host))
        except ValueError:
            ip = "93.184.216.34"  # example.com — заведомо публичный
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port_num))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    return fake_getaddrinfo
