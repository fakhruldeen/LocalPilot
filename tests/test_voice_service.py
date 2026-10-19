"""Tests for VoiceService, transcription, TTS, and voice CLI commands."""

import json
import tempfile
from pathlib import Path
from typer.testing import CliRunner

from localpilot.cli.main import app
from localpilot.storage.db import DatabaseManager
from localpilot.application.voice_service import VoiceService

runner = CliRunner()


def test_voice_service_and_cli(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        db_path = tmp_path / "test_voice.db"
        test_db = DatabaseManager(db_path)

        monkeypatch.setattr("localpilot.storage.db.get_db", lambda *args, **kwargs: test_db)
        monkeypatch.setattr("localpilot.cli.main.get_db", lambda *args, **kwargs: test_db)
        monkeypatch.setattr("localpilot.application.voice_service.get_db", lambda *args, **kwargs: test_db)

        svc = VoiceService(test_db)

        # 1. Dependency check
        deps = svc.check_audio_dependencies()
        assert "can_record" in deps
        assert "tts_engine" in deps

        # 2. Transcribe mock
        dummy_wav = tmp_path / "sample.wav"
        dummy_wav.write_bytes(b"RIFF" + b"\x00" * 32000)
        run_stt = svc.transcribe_audio_file(dummy_wav, mock_transcript="Test voice transcription")
        assert run_stt.transcript == "Test voice transcription"
        assert run_stt.status == "completed"

        # 3. Speak mock
        spoken_log = []
        run_tts = svc.speak_text("Hello from LocalPilot", mock_handler=lambda t: spoken_log.append(t))
        assert spoken_log == ["Hello from LocalPilot"]
        assert run_tts.transcript == "Hello from LocalPilot"

        # 4. CLI voice status
        res_stat = runner.invoke(app, ["voice", "status", "--json"])
        assert res_stat.exit_code == 0
        stat_data = json.loads(res_stat.output)
        assert "can_record" in stat_data

        # 5. CLI voice speak
        res_speak = runner.invoke(app, ["voice", "speak", "Welcome to LocalPilot", "--json"])
        assert res_speak.exit_code == 0
        speak_data = json.loads(res_speak.output)
        assert speak_data["transcript"] == "Welcome to LocalPilot"

        # 6. CLI voice transcribe
        res_trans = runner.invoke(app, ["voice", "transcribe", str(dummy_wav), "--json"])
        assert res_trans.exit_code == 0
        trans_data = json.loads(res_trans.output)
        assert "transcript" in trans_data

        # 7. CLI voice chat turn
        monkeypatch.setattr(
            "localpilot.application.task_service.TaskService._call_inference",
            lambda *args, **kwargs: "I am doing well, thank you!"
        )
        res_chat = runner.invoke(app, ["voice", "chat", "--message", "How are you?", "--json"])
        assert res_chat.exit_code == 0
        chat_data = json.loads(res_chat.output)
        assert chat_data["user"] == "How are you?"
        assert "doing well" in chat_data["assistant"]
