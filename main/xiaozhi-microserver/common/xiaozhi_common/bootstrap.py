"""Bootstrap sys.path so services can import xiaozhi_common and generated stubs."""

from __future__ import annotations

import sys
from pathlib import Path


def bootstrap_paths(service_root: Path | None = None) -> Path:
    """
    service_root: .../xiaozhi-microserver/<service>
    Adds:
      - xiaozhi-microserver/common
      - xiaozhi-microserver/common/generated
      - service_root (for `app` package)
    """
    if service_root is None:
        service_root = Path.cwd()
    service_root = service_root.resolve()
    micro_root = service_root.parent if (service_root / "app").exists() else service_root
    # If called from service dir, parent is xiaozhi-microserver
    if not (micro_root / "common").exists():
        micro_root = service_root
    common = micro_root / "common"
    generated = common / "generated"
    for path in (common, generated, service_root):
        text = str(path)
        if text not in sys.path:
            sys.path.insert(0, text)
    return micro_root
