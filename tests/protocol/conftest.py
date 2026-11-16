"""Protocol tests run real loopback TCP servers and clients.

pytest-homeassistant-custom-component blocks sockets for every test; these
tests opt back in.
"""

import pytest


@pytest.fixture(autouse=True)
def _allow_sockets(socket_enabled: None) -> None:
    """Enable sockets (pytest-socket's ``socket_enabled``)."""
