from __future__ import annotations

import argparse
import shutil
import sqlite3
import tempfile
from pathlib import Path
from zipfile import ZipFile

from backup import backup_once


def _safe_extract(archive: ZipFile, destination: Path) -> None:
    root = destination.resolve()
    for member in archive.infolist():
        target = (root / member.filename).resolve()
        if root != target and root not in target.parents:
            raise ValueError(f"备份包含非法路径：{member.filename}")
    archive.extractall(root)


def _validate_database(path: Path) -> None:
    connection = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
    try:
        result = connection.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        connection.close()
    if result != "ok":
        raise RuntimeError(f"备份数据库完整性检查失败：{result}")


def _replace_directory(source: Path, target: Path) -> None:
    target = target.resolve()
    if target.parent == target:
        raise ValueError("拒绝覆盖文件系统根目录")
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)
    if source.exists():
        shutil.copytree(source, target, dirs_exist_ok=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="离线恢复员工月奖系统备份")
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--uploads", type=Path, required=True)
    parser.add_argument("--exports", type=Path, required=True)
    parser.add_argument("--safety-backup-destination", type=Path, required=True)
    parser.add_argument("--confirm", required=True, help="必须填写 RESTORE")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.confirm != "RESTORE":
        raise ValueError("恢复会覆盖当前数据；停止服务后使用 --confirm RESTORE")
    archive_path = args.archive.resolve()
    if not archive_path.is_file():
        raise FileNotFoundError(f"备份不存在：{archive_path}")
    for target in (args.database.resolve(), args.uploads.resolve(), args.exports.resolve()):
        if target.parent == target:
            raise ValueError("恢复目标不能是文件系统根目录")

    backup_once(
        database=args.database,
        uploads=args.uploads,
        exports=args.exports,
        destination=args.safety_backup_destination,
        retention=7,
    )
    with tempfile.TemporaryDirectory(prefix="monthly-award-restore-") as temporary:
        staging = Path(temporary)
        with ZipFile(archive_path) as archive:
            _safe_extract(archive, staging)
        restored_database = staging / "database" / "monthly_award.sqlite3"
        _validate_database(restored_database)
        args.database.resolve().parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(restored_database, args.database.resolve())
        _replace_directory(staging / "uploads", args.uploads)
        _replace_directory(staging / "exports", args.exports)
    print("restore_ok", flush=True)


if __name__ == "__main__":
    main()
