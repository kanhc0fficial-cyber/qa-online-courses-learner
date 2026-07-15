"""Complete a preserved video-record run without repeating visual API calls."""

import argparse
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from video_analyzer.analyzer import VideoAnalyzer
from video_analyzer.artifacts import crop_key_objects, render_article, render_record
from video_analyzer.audio_processor import AudioProcessor, AudioTranscript
from video_analyzer.clients.generic_openai_api import GenericOpenAIAPIClient
from video_analyzer.frame import Frame
from video_analyzer.prompt import PromptLoader


PROMPTS = [
    {"name": "Frame Record", "path": "frame_analysis/frame_analysis.txt"},
    {"name": "Chronological Record", "path": "frame_analysis/record.txt"},
]

DOMAIN_PROMPT = (
    "英语音标课程。元音、辅音、清音、浊音、调音部位、调音方式、双唇音、"
    "齿龈音、软腭音、塞音、爆破音、送气、不送气、声带振动、DJ音标、KK音标。"
    "音素 /p/ /b/ /t/ /d/ /k/ /g/，示例词 pig big tip dip kit get。"
)

DOMAIN_CORRECTIONS = {
    "俯音": "辅音",
    "轻着": "清浊",
    "清着": "清浊",
    "着音": "浊音",
    "上指音": "上齿龈",
    "软恶": "软腭",
    "双纯": "双唇",
    "原音": "元音",
    "原纯": "圆唇",
    "原存": "圆唇",
    "生母": "声母",
    "色音": "塞音",
    "清色音": "清塞音",
    "喉色音": "喉塞音",
    "储组": "除阻",
    "音音和美音": "英音和美音",
}


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def apply_domain_corrections(transcript: AudioTranscript) -> List[Dict[str, Any]]:
    applied = []
    for source, target in DOMAIN_CORRECTIONS.items():
        count = transcript.text.count(source)
        if count:
            transcript.text = transcript.text.replace(source, target)
            applied.append({"from": source, "to": target, "count": count})
        for segment in transcript.segments:
            segment["text"] = str(segment.get("text", "")).replace(source, target)
            for word in segment.get("words", []) or []:
                word["word"] = str(word.get("word", "")).replace(source, target)
    return applied


def load_frames(output_dir: Path) -> List[Frame]:
    return [
        Frame(
            number=item["number"],
            path=output_dir / item["path"],
            timestamp=item["timestamp"],
            score=item["score"],
            source=item["source"],
        )
        for item in read_json(output_dir / "frames.json")
    ]


def load_or_transcribe(output_dir: Path, model: str, force: bool = False) -> AudioTranscript:
    transcript_path = output_dir / "transcript.json"
    if transcript_path.exists() and not force:
        saved = read_json(transcript_path)
        transcript = AudioTranscript(saved["text"], saved["segments"], saved["language"])
        if not saved.get("corrections_applied"):
            raw_path = output_dir / "transcript.whisper_raw.json"
            if not raw_path.exists():
                shutil.copy2(transcript_path, raw_path)
            corrections = apply_domain_corrections(transcript)
            saved.update({
                "text": transcript.text,
                "segments": transcript.segments,
                "corrections_applied": corrections,
                "raw_file": raw_path.name,
            })
            write_json(transcript_path, saved)
        return transcript

    if transcript_path.exists():
        version = 1
        while (output_dir / f"transcript.v{version}.json").exists():
            version += 1
        shutil.copy2(transcript_path, output_dir / f"transcript.v{version}.json")
    processor = AudioProcessor(
        language=None,
        model_size_or_path=model,
        device="cpu",
        initial_prompt=DOMAIN_PROMPT,
    )
    transcript = processor.transcribe(output_dir / "audio.wav")
    if transcript is None:
        raise RuntimeError("Local Whisper did not return a transcript")
    raw_path = output_dir / "transcript.whisper_raw.json"
    write_json(raw_path, {
        "text": transcript.text,
        "segments": transcript.segments,
        "language": transcript.language,
        "model": model,
        "backend": processor.backend,
    })
    corrections = apply_domain_corrections(transcript)
    write_json(transcript_path, {
        "text": transcript.text,
        "segments": transcript.segments,
        "language": transcript.language,
        "model": model,
        "backend": processor.backend,
        "corrections_applied": corrections,
        "raw_file": raw_path.name,
    })
    return transcript


def main() -> None:
    parser = argparse.ArgumentParser(description="Complete a preserved video fact record")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--model", default="mimo-v2.5")
    parser.add_argument("--whisper-model", default="medium")
    parser.add_argument("--api-key-env", default="XIAOMI_MIMO_API_KEY")
    parser.add_argument("--base-url-env", default="XIAOMI_MIMO_BASE_URL")
    parser.add_argument("--force-transcribe", action="store_true")
    parser.add_argument("--verify-existing-article", action="store_true")
    args = parser.parse_args()

    output_dir = args.output.resolve()
    state_path = output_dir / "run_state.json"
    state: Dict[str, Any] = read_json(state_path) if state_path.exists() else {}
    state["status"] = "transcribing"
    write_json(state_path, state)

    frames = load_frames(output_dir)
    transcript = load_or_transcribe(output_dir, args.whisper_model, args.force_transcribe)
    scenes = read_json(output_dir / "scenes.visual.json")
    for scene in scenes:
        scene["speech"] = VideoAnalyzer._speech_at(float(scene["timestamp"]), transcript)

    state["status"] = "editing_article"
    write_json(state_path, state)
    client = GenericOpenAIAPIClient(
        os.environ[args.api_key_env], os.environ[args.base_url_env], api_key_header="api-key"
    )
    editor = VideoAnalyzer(client, args.model, PromptLoader("", PROMPTS), 0.0)
    existing_article = output_dir / "article.md"
    article_source = output_dir / "article.source.md"
    article_text = (
        article_source.read_text(encoding="utf-8")
        if args.verify_existing_article and article_source.exists()
        else editor.compose_article(scenes, transcript)
    )
    article_text = editor.fact_check_article(article_text, scenes)

    crop_key_objects(scenes, frames, output_dir)
    record_markdown = render_record(scenes, output_dir)
    article_source.write_text(article_text, encoding="utf-8")
    article_markdown = render_article(article_text, scenes, output_dir)
    for scene, frame in zip(scenes, frames):
        scene["frame_path"] = frame.path.relative_to(output_dir).as_posix()

    visual_calls = []
    for name in ("api_calls.visual.json", "api_calls.visual_retry.json", "api_calls.visual_retry_single.json"):
        path = output_dir / name
        if path.exists():
            visual_calls.extend(read_json(path))
    api_calls = visual_calls + editor.call_log
    write_json(output_dir / "api_calls.json", api_calls)
    write_json(output_dir / "record.json", {
        "metadata": {
            "source_video": str(args.video.resolve()),
            "visual_model": args.model,
            "whisper_model": args.whisper_model,
            "whisper_language": transcript.language,
            "frame_count": len(frames),
            "record_file": record_markdown.name,
            "article_file": article_markdown.name,
        },
        "scenes": scenes,
        "article_text": article_text,
    })
    state.update({
        "status": "complete",
        "record_file": record_markdown.name,
        "article_file": article_markdown.name,
        "scene_count": len(scenes),
        "api_call_count": len(api_calls),
    })
    write_json(state_path, state)


if __name__ == "__main__":
    main()
