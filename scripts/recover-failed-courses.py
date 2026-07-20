"""Recover failed strict-course jobs from versioned copies of saved artifacts."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import traceback
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
COURSE_ROOT = ROOT / "course-workflow"
ANALYZER_ROOT = ROOT / "video-analyzer"
JOBS_DIR = COURSE_ROOT / "data" / "jobs"
LESSONS_DIR = COURSE_ROOT / "data" / "lessons"
BATCHES_DIR = COURSE_ROOT / "data" / "batches"
SOURCE_ID = "BV1pS4y1g7D9"
DEFAULT_PARTS = (17, 20, 21, 23, 25)

if str(COURSE_ROOT) not in sys.path:
    sys.path.insert(0, str(COURSE_ROOT))

from lesson_builder import build_lesson  # noqa: E402


report_lock = threading.Lock()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)


def latest_failed_job(part: int) -> dict[str, Any]:
    candidates = []
    for path in JOBS_DIR.glob(f"p{part:02d}-*.json"):
        value = read_json(path)
        if value.get("status") == "failed" and value.get("record_dir"):
            candidates.append((path.stat().st_mtime, value))
    if not candidates:
        raise FileNotFoundError(f"P{part} has no failed job with a record directory")
    return max(candidates, key=lambda item: item[0])[1]


def rewrite_copied_paths(source: Path, target: Path) -> None:
    old = str(source.resolve())
    new = str(target.resolve())
    for path in target.rglob("*"):
        if path.is_file() and path.suffix.lower() in {".json", ".md"}:
            text = path.read_text(encoding="utf-8")
            if old in text:
                path.write_text(text.replace(old, new), encoding="utf-8")


def update_report(
    report_path: Path,
    report: dict[str, Any],
    *,
    active: dict[str, Any] | None = None,
    finished: dict[str, Any] | None = None,
    failed: dict[str, Any] | None = None,
) -> None:
    with report_lock:
        if active is not None:
            report["active"][str(active["part"])] = active
        if finished is not None:
            report["active"].pop(str(finished["part"]), None)
            report["completed"].append(finished)
        if failed is not None:
            report["active"].pop(str(failed["part"]), None)
            report["failed"].append(failed)
        report["updated_at"] = datetime.now().astimezone().isoformat(
            timespec="seconds"
        )
        write_json_atomic(report_path, report)


def recover_part(
    part: int,
    batch_timestamp: str,
    report_path: Path,
    report: dict[str, Any],
) -> dict[str, Any]:
    failed_job = latest_failed_job(part)
    source_record = Path(failed_job["record_dir"]).resolve()
    if not source_record.is_dir():
        raise FileNotFoundError(source_record)

    job_id = f"p{part:02d}-recovery-{batch_timestamp}"
    target_record = source_record.with_name(
        f"{source_record.name}_recovery_{batch_timestamp}"
    )
    if target_record.exists():
        raise FileExistsError(target_record)
    shutil.copytree(source_record, target_record)
    rewrite_copied_paths(source_record, target_record)

    source_url = f"https://www.bilibili.com/video/{SOURCE_ID}?p={part}"
    log_path = JOBS_DIR / f"{job_id}.workflow.log"
    error_path = JOBS_DIR / f"{job_id}.error.log"
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    job = {
        "id": job_id,
        "status": "running",
        "stage": "analysis",
        "stage_label": "从版本化失败现场续跑",
        "progress": 28,
        "part": part,
        "source_id": SOURCE_ID,
        "source_url": source_url,
        "ppt_complete": True,
        "validation_profile": "strict_course",
        "created_at": now,
        "updated_at": now,
        "download_dir": failed_job.get("download_dir"),
        "record_dir": str(target_record),
        "recovery_source_record": str(source_record),
        "log_path": str(log_path),
    }
    write_json_atomic(JOBS_DIR / f"{job_id}.json", job)
    update_report(
        report_path,
        report,
        active={
            "part": part,
            "job_id": job_id,
            "stage": "analysis",
            "record_dir": str(target_record),
            "recovery_source_record": str(source_record),
        },
    )

    command = [
        sys.executable,
        str(ANALYZER_ROOT / "scripts" / "bilibili_workflow.py"),
        str(failed_job["download_dir"]),
        "--part",
        str(part),
        "--output",
        str(target_record),
        "--model",
        "mimo-v2.5",
        "--api-key-env",
        "XIAOMI_MIMO_API_KEY_TEM1",
        "--validation-profile",
        "strict_course",
        "--ppt-complete",
        "--ppt-ignore-head",
        "18",
        "--ppt-ignore-tail",
        "10",
        "--resume",
    ]
    environment = dict(os.environ)
    environment.update({
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
        "WHISPER_MODEL": "medium",
        "WHISPER_BACKEND": "openai",
        "HF_HUB_DISABLE_XET": "1",
        "HF_HUB_DISABLE_SYMLINKS_WARNING": "1",
    })

    try:
        with log_path.open("w", encoding="utf-8") as log:
            subprocess.run(
                command,
                cwd=ANALYZER_ROOT,
                env=environment,
                stdout=log,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                check=True,
            )
        job.update({
            "stage": "quiz",
            "stage_label": "生成题目并定位老师讲完的时间",
            "progress": 82,
            "updated_at": datetime.now().astimezone().isoformat(
                timespec="seconds"
            ),
        })
        write_json_atomic(JOBS_DIR / f"{job_id}.json", job)
        update_report(
            report_path,
            report,
            active={
                "part": part,
                "job_id": job_id,
                "stage": "quiz",
                "record_dir": str(target_record),
            },
        )

        lesson_path = build_lesson(
            target_record,
            job_id,
            part,
            source_url,
            source_id=SOURCE_ID,
            strict_validation=True,
        )
        lesson = read_json(lesson_path)
        job.update({
            "status": "complete",
            "stage": "complete",
            "stage_label": "课程网址已就绪",
            "progress": 100,
            "lesson_id": lesson["id"],
            "lesson_url": f"/lessons/{lesson['id']}",
            "updated_at": datetime.now().astimezone().isoformat(
                timespec="seconds"
            ),
        })
        write_json_atomic(JOBS_DIR / f"{job_id}.json", job)
        result = {
            "part": part,
            "job_id": job_id,
            "lesson_id": lesson["id"],
            "lesson_url": job["lesson_url"],
            "record_dir": str(target_record),
            "recovery_source_record": str(source_record),
        }
        update_report(report_path, report, finished=result)
        return result
    except Exception as exc:
        error_path.write_text(traceback.format_exc(), encoding="utf-8")
        job.update({
            "status": "failed",
            "stage": "failed",
            "stage_label": "恢复任务已停止",
            "error": str(exc),
            "error_log": str(error_path),
            "updated_at": datetime.now().astimezone().isoformat(
                timespec="seconds"
            ),
        })
        write_json_atomic(JOBS_DIR / f"{job_id}.json", job)
        failure = {
            "part": part,
            "job_id": job_id,
            "error": str(exc),
            "error_log": str(error_path),
            "workflow_log": str(log_path),
            "record_dir": str(target_record),
        }
        update_report(report_path, report, failed=failure)
        return failure


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--parts", nargs="+", type=int, default=DEFAULT_PARTS)
    parser.add_argument("--max-workers", type=int, default=2)
    args = parser.parse_args()

    invalid = [part for part in args.parts if part not in DEFAULT_PARTS]
    if invalid:
        raise ValueError(f"Recovery is limited to {DEFAULT_PARTS}; got {invalid}")

    BATCHES_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    report_path = BATCHES_DIR / f"recovery-p17-p25-{timestamp}.json"
    report: dict[str, Any] = {
        "batch_id": f"recovery-p17-p25-{timestamp}",
        "status": "running",
        "scope": list(args.parts),
        "max_workers": max(1, args.max_workers),
        "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "active": {},
        "completed": [],
        "failed": [],
        "report_path": str(report_path),
    }
    write_json_atomic(report_path, report)

    with ThreadPoolExecutor(max_workers=max(1, args.max_workers)) as executor:
        futures = {
            executor.submit(
                recover_part,
                part,
                timestamp,
                report_path,
                report,
            ): part
            for part in args.parts
        }
        for future in as_completed(futures):
            future.result()

    report["status"] = "complete" if not report["failed"] else "completed_with_failures"
    report["completed_at"] = datetime.now().astimezone().isoformat(
        timespec="seconds"
    )
    write_json_atomic(report_path, report)
    return 0 if not report["failed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
