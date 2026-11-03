from __future__ import annotations

import signal
import time

from app.config import settings
from app.db import Repository
from app.services.extraction_processor import ExtractionProcessor


running = True


def stop(*_: object) -> None:
    global running
    running = False


def main() -> None:
    settings.ensure_directories()
    repository = Repository(settings.database_path)
    processor = ExtractionProcessor(repository, settings)
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    while running:
        result = processor.process_job()
        if result is None:
            time.sleep(1)


if __name__ == "__main__":
    main()
