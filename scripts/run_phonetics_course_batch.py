"""Serial, auditable coordinator for OCR-first phonetics lessons."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
COURSE_ROOT = ROOT / "course-workflow"
if str(COURSE_ROOT) not in sys.path:
    sys.path.insert(0, str(COURSE_ROOT))

import server


def write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)


def append_event(path: Path, event: str, **data: Any) -> None:
    value = {
        "time": datetime.now().astimezone().isoformat(timespec="seconds"),
        "event": event,
        "data": data,
    }
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False) + "\n")


def recoverable_transport_failure(job: dict[str, Any]) -> bool:
    paths = [
        Path(str(job[key]))
        for key in ("log_path", "error_log")
        if job.get(key)
    ]
    text = "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in paths
        if path.is_file()
    )
    markers = (
        "SSLError",
        "ConnectionResetError",
        "Connection aborted",
        "UNEXPECTED_EOF_WHILE_READING",
        "RemoteDisconnected",
        "ReadTimeout",
        "ConnectTimeout",
    )
    return any(marker in text for marker in markers)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default="BV1iV411z7Nj")
    parser.add_argument("--start-part", type=int, default=3)
    parser.add_argument("--end-part", type=int, default=47)
    args = parser.parse_args()
    parts = server.validate_part_range(args.start_part, min(args.end_part, args.start_part + 29))
    if args.end_part - args.start_part + 1 > 30:
        parts = list(range(args.start_part, args.end_part + 1))

    batch_id = f"phonetics-{datetime.now():%Y%m%d-%H%M%S}"
    batch_dir = COURSE_ROOT / "data" / "batches"
    report_path = batch_dir / f"{batch_id}.json"
    events_path = batch_dir / f"{batch_id}.events.jsonl"
    report: dict[str, Any] = {
        "id": batch_id,
        "source_id": args.source,
        "validation_profile": "phonetics_course",
        "evidence_mode": "ocr_primary",
        "parts": parts,
        "status": "running",
        "completed": [],
        "skipped": [],
        "failed": None,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    write_json_atomic(report_path, report)
    append_event(events_path, "batch_started", parts=parts)

    for part in parts:
        existing = server.find_existing_lesson(args.source, part)
        if existing:
            report["skipped"].append({
                "part": part,
                "lesson_id": existing["id"],
                "reason": "completed_lesson_exists",
            })
            append_event(
                events_path,
                "part_skipped",
                part=part,
                lesson_id=existing["id"],
            )
            write_json_atomic(report_path, report)
            continue

        source_url, source_id, _ = server.parse_source(args.source, part)
        job_id = f"phonetics-p{part:03d}-{datetime.now():%Y%m%d-%H%M%S}"
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        job = {
            "id": job_id,
            "batch_id": batch_id,
            "kind": "lesson",
            "status": "running",
            "stage": "queued",
            "stage_label": "等待音标课程密集 OCR",
            "progress": 2,
            "part": part,
            "source_id": source_id,
            "source_url": source_url,
            "ppt_complete": False,
            "validation_profile": "phonetics_course",
            "evidence_mode": "ocr_primary",
            "created_at": now,
            "updated_at": now,
        }
        server.write_json_atomic(server.JOBS_DIR / f"{job_id}.json", job)
        append_event(events_path, "part_started", part=part, job_id=job_id)
        resumable_records = []
        for path in server.JOBS_DIR.glob("*.json"):
            candidate = server.read_json(path)
            if (
                candidate.get("status") == "failed"
                and candidate.get("source_id") == source_id
                and int(candidate.get("part", -1)) == part
                and candidate.get("validation_profile") == "phonetics_course"
                and candidate.get("record_dir")
                and Path(str(candidate["record_dir"])).is_dir()
            ):
                resumable_records.append(candidate)
        resumable = max(
            resumable_records,
            key=lambda item: str(item.get("updated_at", "")),
            default=None,
        )
        resume_record = str(resumable["record_dir"]) if resumable else None
        finished = job
        for attempt in range(1, 11):
            server.run_job(
                job_id,
                source_url,
                source_id,
                part,
                True,
                False,
                "phonetics_course",
                resume_record,
            )
            finished = server.read_json(server.JOBS_DIR / f"{job_id}.json")
            if finished.get("status") == "complete":
                break
            if not recoverable_transport_failure(finished) or attempt >= 10:
                break
            workflow_log = Path(str(finished.get("log_path", "")))
            if workflow_log.is_file():
                archived = workflow_log.with_name(
                    f"{workflow_log.stem}.attempt-{attempt:02d}{workflow_log.suffix}"
                )
                shutil.copy2(workflow_log, archived)
            resume_record = str(finished.get("record_dir") or resume_record)
            server.update_job(
                job_id,
                status="running",
                stage="queued",
                stage_label=f"网络中断后继续 OCR（第 {attempt + 1} 次）",
                progress=max(28, int(finished.get("progress", 28))),
                transport_retry_count=attempt,
            )
            append_event(
                events_path,
                "transport_retry",
                part=part,
                job_id=job_id,
                attempt=attempt + 1,
                record_dir=resume_record,
            )
        if finished.get("status") != "complete":
            report["status"] = "failed"
            report["failed"] = {
                "part": part,
                "job_id": job_id,
                "error": finished.get("error"),
                "error_log": finished.get("error_log"),
                "record_dir": finished.get("record_dir"),
                "workflow_log": finished.get("log_path"),
            }
            report["updated_at"] = datetime.now().astimezone().isoformat(
                timespec="seconds"
            )
            write_json_atomic(report_path, report)
            append_event(events_path, "batch_failed", **report["failed"])
            return 1

        completed = {
            "part": part,
            "job_id": job_id,
            "lesson_id": finished.get("lesson_id"),
            "lesson_url": finished.get("lesson_url"),
            "download_dir": finished.get("download_dir"),
            "record_dir": finished.get("record_dir"),
        }
        report["completed"].append(completed)
        report["updated_at"] = datetime.now().astimezone().isoformat(
            timespec="seconds"
        )
        write_json_atomic(report_path, report)
        append_event(events_path, "part_completed", **completed)

    report["status"] = "complete"
    report["updated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    write_json_atomic(report_path, report)
    append_event(events_path, "batch_completed", completed=len(report["completed"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
