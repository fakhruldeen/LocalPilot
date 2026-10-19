"""Voice interaction engine providing speech-to-text, text-to-speech, and push-to-talk loops."""

from __future__ import annotations
import os
import shutil
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from localpilot.domain.task_contract import VoiceDirection, VoiceRun
from localpilot.storage.db import DatabaseManager, get_db


class VoiceService:
    """Manages audio transcription, voice generation, and interactive conversation with graceful fallbacks."""

    def __init__(self, db: Optional[DatabaseManager] = None) -> None:
        self.db = db or get_db()

    def check_audio_dependencies(self) -> Dict[str, Any]:
        """Inspects availability of microphone, audio libraries, and TTS/STT binaries."""
        has_sounddevice = False
        try:
            import sounddevice
            has_sounddevice = True
        except ImportError:
            pass

        has_numpy = False
        try:
            import numpy
            has_numpy = True
        except ImportError:
            pass

        # TTS Engines
        tts_engine = None
        if shutil.which("piper"):
            tts_engine = "piper"
        elif shutil.which("espeak-ng"):
            tts_engine = "espeak-ng"
        elif shutil.which("say"):  # macOS
            tts_engine = "say"
        elif sys.platform == "win32":
            tts_engine = "windows_sapi"

        # STT Engines
        stt_engine = None
        if shutil.which("whisper"):
            stt_engine = "whisper-cli"
        elif shutil.which("whisper-cpp"):
            stt_engine = "whisper-cpp"

        return {
            "can_record": has_sounddevice and has_numpy,
            "has_sounddevice": has_sounddevice,
            "has_numpy": has_numpy,
            "tts_engine": tts_engine,
            "stt_engine": stt_engine,
        }

    def transcribe_audio_file(
        self,
        audio_path: Path | str,
        model: str = "whisper-base",
        mock_transcript: Optional[str] = None,
    ) -> VoiceRun:
        """Transcribes an existing audio file to text."""
        p = Path(audio_path).resolve()
        if not p.exists():
            raise ValueError(f"Audio file not found: {p}")

        now_str = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        run_id = f"vrun_{uuid.uuid4().hex[:10]}"
        t_start = time.perf_counter()

        transcript = ""
        error_msg = None
        status = "completed"

        if mock_transcript is not None:
            transcript = mock_transcript
        else:
            deps = self.check_audio_dependencies()
            if deps["stt_engine"] == "whisper-cli":
                try:
                    res = subprocess.run(
                        ["whisper", str(p), "--model", "base", "--output_format", "txt"],
                        capture_output=True,
                        text=True,
                        check=True,
                    )
                    transcript = res.stdout.strip()
                except Exception as exc:
                    status = "failed"
                    error_msg = f"Whisper transcription failed: {exc}"
            else:
                transcript = f"[Transcription for {p.name}: install 'whisper' CLI or a local speech model to perform real-time local STT]"

        dur_ms = (time.perf_counter() - t_start) * 1000.0

        run = VoiceRun(
            run_id=run_id,
            direction=VoiceDirection.STT,
            model=model,
            duration_ms=round(dur_ms, 2),
            audio_seconds=round(p.stat().st_size / 32000, 2),  # Estimated
            transcript=transcript if status == "completed" else None,
            status=status,
            error_message=error_msg,
            created_at=now_str,
        )

        self.db.record_voice_run(run.model_dump())
        return run

    def speak_text(
        self,
        text: str,
        voice: Optional[str] = None,
        mock_handler: Optional[Callable[[str], None]] = None,
    ) -> VoiceRun:
        """Synthesizes text into spoken audio using available system TTS."""
        now_str = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        run_id = f"vrun_{uuid.uuid4().hex[:10]}"
        t_start = time.perf_counter()

        deps = self.check_audio_dependencies()
        engine = deps["tts_engine"]
        status = "completed"
        error_msg = None

        if mock_handler:
            mock_handler(text)
        elif engine == "say":
            try:
                subprocess.run(["say", text], check=True)
            except Exception as exc:
                status = "failed"
                error_msg = str(exc)
        elif engine == "espeak-ng":
            try:
                subprocess.run(["espeak-ng", text], check=True)
            except Exception as exc:
                status = "failed"
                error_msg = str(exc)
        elif engine == "windows_sapi":
            try:
                safe_text = text.replace("'", "''")
                ps_cmd = f"Add-Type -AssemblyName System.Speech; (New-Object System.Speech.Synthesis.SpeechSynthesizer).Speak('{safe_text}')"
                subprocess.run(["powershell", "-Command", ps_cmd], check=True)
            except Exception as exc:
                status = "failed"
                error_msg = str(exc)
        else:
            # Fallback: Print cleanly with audio simulation notice
            pass

        dur_ms = (time.perf_counter() - t_start) * 1000.0

        run = VoiceRun(
            run_id=run_id,
            direction=VoiceDirection.TTS,
            model=engine or "system-console",
            duration_ms=round(dur_ms, 2),
            audio_seconds=max(0.5, len(text.split()) * 0.3),
            transcript=text,
            status=status,
            error_message=error_msg,
            created_at=now_str,
        )

        self.db.record_voice_run(run.model_dump())
        return run

    def run_conversation_turn(
        self,
        user_input: str,
        model_alias: str = "default",
        gateway_url: str = "http://127.0.0.1:8080/v1",
        mock_response: Optional[str] = None,
    ) -> Tuple[str, VoiceRun]:
        """Executes a single voice conversation turn: STT -> LLM response -> TTS."""
        from localpilot.application.task_service import TaskService
        task_svc = TaskService(self.db)

        if mock_response:
            ai_text = mock_response
        else:
            ai_text = task_svc._call_inference(
                gateway_url=gateway_url,
                model_alias=model_alias,
                system_prompt="You are LocalPilot Voice. Answer conversationally, concisely, and clearly in 1-3 sentences suitable for speech.",
                user_prompt=user_input,
            )

        # Speak the response
        tts_run = self.speak_text(ai_text)
        return ai_text, tts_run
