from __future__ import annotations

import argparse
import os
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


def _add_directory(archive: ZipFile, directory: Path, prefix: str) -> None:
    if not directory.exists():
        return
    for path in sorted(item for item in directory.rglob("*") if item.is_file()):
        archive.write(path, (Path(prefix) / path.relative_to(directory)).as_posix())


def backup_once(
    *,
    database: Path,
    uploads: Path,
    exports: Path,
    destination: Path,
    retention: int,
) -> Path:
    database = database.resolve()
    destination = destination.resolve()
    if not database.is_file():
        raise FileNotFoundError(f"数据库不存在：{database}")
    destination.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    archive_path = destination / f"monthly-award-{timestamp}.zip"
    temp_archive = destination / f".{archive_path.name}.tmp"
    temp_database = destination / f".monthly-award-{timestamp}.sqlite3"

    source = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True, timeout=30)
    target = sqlite3.connect(temp_database)
    try:
        source.backup(target)
        integrity = target.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(f"数据库完整性检查失败：{integrity}")
    finally:
        target.close()
        source.close()

    try:
        with ZipFile(temp_archive, "w", compression=ZIP_DEFLATED, compresslevel=6) as archive:
            archive.write(temp_database, "database/monthly_award.sqlite3")
            _add_directory(archive, uploads.resolve(), "uploads")
            _add_directory(archive, exports.resolve(), "exports")
        os.replace(temp_archive, archive_path)
    finally:
        temp_database.unlink(missing_ok=True)
        temp_archive.unlink(missing_ok=True)

    backups = sorted(destination.glob("monthly-award-*.zip"), key=lambda path: path.stat().st_mtime, reverse=True)
    for expired in backups[max(1, retention):]:
        resolved = expired.resolve()
        if resolved.parent != destination or not resolved.name.startswith("monthly-award-"):
            raise RuntimeError(f"拒绝清理非备份文件：{resolved}")
        resolved.unlink()
    return archive_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="安全备份员工月奖 SQLite、原件和导出文件")
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--uploads", type=Path, required=True)
    parser.add_argument("--exports", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--retention", type=int, default=7)
    parser.add_argument("--interval-seconds", type=int, default=0, help="大于0时持续按该间隔备份")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.retention < 1:
        raise ValueError("至少保留1个备份")
    while True:
        archive = backup_once(
            database=args.database,
            uploads=args.uploads,
            exports=args.exports,
            destination=args.destination,
            retention=args.retention,
        )
        print(f"backup_ok {archive}", flush=True)
        if args.interval_seconds <= 0:
            return
        time.sleep(max(60, args.interval_seconds))


if __name__ == "__main__":
    main()
