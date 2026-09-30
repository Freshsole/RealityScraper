"""Test isolation: testy nesmí záviset na ambientním prostředí.

Sandbox/proxy proměnné (https_proxy, …) rozbíjejí httpx URL parsing
a síťové testy by jinak chodily přes egress proxy. Testy mají být hermetické.
"""

import os
import tempfile

for _var in (
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "no_proxy",
    "NO_PROXY",
):
    os.environ.pop(_var, None)

# DATA_DIR musí být nastavený DŘÍV, než jakýkoliv test naimportuje app.*
# (app/config.py si DATA_DIR přečte při importu). Jinak testy zapisují
# do reálného ./data a navzájem se ovlivňují.
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="realitify-test-data-"))
