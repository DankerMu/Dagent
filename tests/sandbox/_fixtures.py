"""Fixtures imported explicitly by the native sandbox integration suites."""

import asyncio

import pytest


@pytest.fixture(scope="module")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()
