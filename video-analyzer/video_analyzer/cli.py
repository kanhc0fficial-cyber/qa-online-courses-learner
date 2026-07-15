import argparse
from pathlib import Path
import json
import logging
import shutil
from typing import Optional
import torch
import torch.backends.mps

from .config import Config, get_client, get_model
from .frame import VideoProcessor
from .prompt import PromptLoader
from .analyzer import VideoAnalyzer
from .audio_processor import AudioProcessor, AudioTranscript
from .clients.ollama import OllamaClient
from .clients.generic_openai_api import GenericOpenAIAPIClient
from .artifacts import crop_key_objects, render_article, render_record

# Initialize logger at module level
logger = logging.getLogger(__name__)

def get_log_level(level_str: str) -> int:
    """Convert string log level to logging constant."""
    levels = {
        'DEBUG': logging.DEBUG,
        'INFO': logging.INFO,
        'WARNING': logging.WARNING,
        'ERROR': logging.ERROR,
        'CRITICAL': logging.CRITICAL
    }
    return levels.get(level_str.upper(), logging.INFO)

def cleanup_files(output_dir: Path):
    """Clean up temporary files and directories."""
    try:
        for frames_dir in (output_dir / "frames", output_dir / "assets" / "frames"):
            if frames_dir.exists():
                shutil.rmtree(frames_dir)
                logger.debug(f"Cleaned up frames directory: {frames_dir}")
            
        audio_file = output_dir / "audio.wav"
        if audio_file.exists():
            audio_file.unlink()
            logger.debug(f"Cleaned up audio file: {audio_file}")
    except Exception as e:
        logger.error(f"Error during cleanup: {e}")

def create_client(config: Config):
    """Create the appropriate client based on configuration."""
    client_type = config.get("clients", {}).get("default", "ollama")
    client_config = get_client(config)
    
    if client_type == "ollama":
        return OllamaClient(client_config["url"])
    elif client_type == "openai_api":
        return GenericOpenAIAPIClient(
            client_config["api_key"],
            client_config["api_url"],
            api_key_header=client_config.get("api_key_header", "Authorization"),
        )
    else:
        raise ValueError(f"Unknown client type: {client_type}")

def main():
    parser = argparse.ArgumentParser(description="Create an auditable video record using Vision models")
    parser.add_argument("video_path", type=str, help="Path to the video file")
    parser.add_argument("--config", type=str, default="config",
                        help="Path to configuration directory")
    parser.add_argument("--output", type=str, help="Output directory for video records")
    parser.add_argument("--client", type=str, help="Client to use (ollama or openrouter)")
    parser.add_argument("--ollama-url", type=str, help="URL for the Ollama service")
    parser.add_argument("--api-key", type=str, help="API key for OpenAI-compatible service")
    parser.add_argument("--api-url", type=str, help="API URL for OpenAI-compatible API")
    parser.add_argument("--api-key-header", type=str, help="API key header name (MiMo uses api-key)")
    parser.add_argument("--model", type=str, help="Name of the vision model to use")
    parser.add_argument("--duration", type=float, help="Duration in seconds to process")
    frame_retention = parser.add_mutually_exclusive_group()
    frame_retention.add_argument(
        "--keep-frames", dest="keep_frames", action="store_true", default=None,
        help="Keep extracted frames after analysis (default)",
    )
    frame_retention.add_argument(
        "--discard-frames", dest="keep_frames", action="store_false",
        help="Delete extracted frames after analysis",
    )
    parser.add_argument("--whisper-model", type=str, help="Whisper model size (tiny, base, small, medium, large), or path to local Whisper model snapshot")
    parser.add_argument("--start-stage", type=int, default=1, help="Stage to start processing from (1-3)")
    parser.add_argument("--max-frames", type=int, default=None, help="Maximum number of frames to process")
    parser.add_argument("--log-level", type=str, default="INFO", 
                        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
                        help="Set the logging level (default: INFO)")
    parser.add_argument("--prompt", type=str, default="",
                        help="Question to ask about the video")
    parser.add_argument("--language", type=str, default=None)
    parser.add_argument("--device", type=str, default="cpu")
    parser.add_argument("--temperature", type=float, help="Temperature for LLM generation")
    args = parser.parse_args()

    # Set up logging with specified level
    log_level = get_log_level(args.log_level)
    # Configure the root logger
    logging.basicConfig(
        level=log_level,
        format='%(asctime)s - %(levelname)s - %(message)s',
        force=True  # Force reconfiguration of the root logger
    )
    # Ensure our module logger has the correct level
    logger.setLevel(log_level)

    # Load and update configuration
    config = Config(args.config)
    config.update_from_args(args)

    # Initialize components
    video_path = Path(args.video_path)
    output_dir = Path(config.get("output_dir"))
    client = create_client(config)
    model = get_model(config)
    prompt_loader = PromptLoader(config.get("prompt_dir"), config.get("prompts", []))
    
    try:
        transcript = None
        frames = []
        scenes = []
        article_text = ""
        
        # Stage 1: Frame and Audio Processing
        if args.start_stage <= 1:
            # Initialize audio processor and extract transcript, the AudioProcessor accept following parameters that can be set in config.json:
            # language (str): Language code for audio transcription (default: None)
            # whisper_model (str): Whisper model size or path (default: "medium")
            # device (str): Device to use for audio processing (default: "cpu")
            logger.debug("Initializing audio processing...")
            audio_processor = AudioProcessor(language=config.get("audio", {}).get("language", ""), 
                                             model_size_or_path=config.get("audio", {}).get("whisper_model", "medium"),
                                             device=config.get("audio", {}).get("device", "cpu"),
                                             initial_prompt=config.get("audio", {}).get("initial_prompt", ""))
            
            logger.info("Extracting audio from video...")
            try:
                audio_path = audio_processor.extract_audio(video_path, output_dir)
            except Exception as e:
                logger.error(f"Error extracting audio: {e}")
                audio_path = None
            
            if audio_path is None:
                logger.debug("No audio found in video - skipping transcription")
                transcript = None
            else:
                logger.info("Transcribing audio...")
                transcript = audio_processor.transcribe(audio_path)
                if transcript is None:
                    logger.warning("Could not generate reliable transcript. Proceeding with video analysis only.")
            
            logger.info(f"Extracting frames from video using model {model}...")
            processor = VideoProcessor(
                video_path, 
                output_dir / "assets" / "frames",
                model
            )
            frame_config = config.get("frames", {})
            configured_max = frame_config.get("max_count", 30)
            effective_max = min(args.max_frames, configured_max) if args.max_frames else configured_max
            frames = processor.extract_keyframes(
                frames_per_minute=frame_config.get("per_minute", 60),
                duration=config.get("duration"),
                max_frames=effective_max,
                scene_threshold=frame_config.get("scene_threshold", 0.1),
                black_threshold=frame_config.get("black_threshold", 10.0),
                hash_distance=frame_config.get("hash_distance", 5),
            )
            
        # Stage 2: Frame Analysis
        if args.start_stage <= 2:
            logger.info("Analyzing frames...")
            analyzer = VideoAnalyzer(
                client, 
                model, 
                prompt_loader,
                config.get("clients", {}).get("temperature", 0.2),
                config.get("prompt", "")
            )
            scenes = analyzer.analyze_frames(
                frames,
                transcript,
                group_size=config.get("recording", {}).get("group_size", 6),
            )
                
        # Stage 3: Create a readable article from the auditable scene record.
        if args.start_stage <= 3:
            logger.info("Creating publishable article from scene records...")
            article_text = analyzer.compose_article(scenes, transcript)
            article_text = analyzer.fact_check_article(article_text, scenes)

        crop_key_objects(scenes, frames, output_dir)
        record_path = render_record(scenes, output_dir)
        (output_dir / "article.source.md").write_text(article_text, encoding="utf-8")
        article_path = render_article(article_text, scenes, output_dir)
        for scene, frame in zip(scenes, frames):
            scene["frame_path"] = str(frame.path.relative_to(output_dir)).replace("\\", "/")
        
        output_dir.mkdir(parents=True, exist_ok=True)
        results = {
            "metadata": {
                "client": config.get("clients", {}).get("default"),
                "model": model,
                "whisper_model": config.get("audio", {}).get("whisper_model"),
                "frames_per_minute": config.get("frames", {}).get("per_minute"),
                "duration_processed": config.get("duration"),
                "frames_extracted": len(frames),
                "frames_recorded": len(scenes),
                "start_stage": args.start_stage,
                "audio_language": transcript.language if transcript else None,
                "transcription_successful": transcript is not None,
                "record_file": record_path.name,
                "article_file": article_path.name,
            },
            "transcript": {
                "text": transcript.text if transcript else None,
                "segments": transcript.segments if transcript else None
            } if transcript else None,
            "frames": [
                {
                    "number": frame.number,
                    "timestamp": frame.timestamp,
                    "path": str(frame.path.relative_to(output_dir)).replace("\\", "/"),
                    "score": frame.score,
                    "source": frame.source,
                }
                for frame in frames
            ],
            "scenes": scenes,
            "article_text": article_text,
        }
        
        with open(output_dir / "record.json", "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, ensure_ascii=False)
        if transcript:
            with open(output_dir / "transcript.json", "w", encoding="utf-8") as f:
                json.dump({
                    "text": transcript.text,
                    "segments": transcript.segments,
                    "language": transcript.language,
                }, f, indent=2, ensure_ascii=False)
            
        logger.info("\nTranscript:")
        if transcript:
            logger.info(transcript.text)
        else:
            logger.info("No reliable transcript available")
            
        if not config.get("keep_frames"):
            cleanup_files(output_dir)
        
        logger.info(f"Recording complete. Results saved to {output_dir / 'record.json'}")
            
    except Exception as e:
        logger.error(f"Error while creating video record: {e}")
        if not config.get("keep_frames"):
            cleanup_files(output_dir)
        raise

if __name__ == "__main__":
    main()
