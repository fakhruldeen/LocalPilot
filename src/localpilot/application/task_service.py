"""Task execution engine and registry for all 18 LocalPilot AI tool interfaces."""

from __future__ import annotations
import json
import time
import uuid
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from localpilot.domain.task_contract import (
    TaskArtifact,
    TaskCategory,
    TaskDescriptor,
    TaskPlan,
    TaskRun,
    TaskStatus,
)
from localpilot.storage.db import DatabaseManager, get_db

TASK_DEFINITIONS: List[TaskDescriptor] = [
    TaskDescriptor(
        task_id="chat",
        name="General Chat",
        category=TaskCategory.TEXT,
        description="Interactive or single-turn conversational assistant.",
        required_capabilities=["text"],
        preferred_model_tags=["chat", "general"],
        example_usage="localpilot tools chat 'Explain quantum computing in one sentence'",
    ),
    TaskDescriptor(
        task_id="summarize",
        name="Summarize",
        category=TaskCategory.TEXT,
        description="Condense documents, articles, or transcripts into concise summaries.",
        required_capabilities=["text"],
        preferred_model_tags=["chat", "general"],
        example_usage="localpilot tools summarize report.txt --bullets",
    ),
    TaskDescriptor(
        task_id="rewrite",
        name="Rewrite & Polish",
        category=TaskCategory.TEXT,
        description="Refactor text for tone, clarity, grammar, conciseness, or formality.",
        required_capabilities=["text"],
        preferred_model_tags=["chat", "general"],
        example_usage="localpilot tools rewrite draft.txt --tone professional",
    ),
    TaskDescriptor(
        task_id="translate",
        name="Translate",
        category=TaskCategory.TEXT,
        description="Translate text accurately between languages preserving context.",
        required_capabilities=["text"],
        preferred_model_tags=["general"],
        example_usage="localpilot tools translate article.md --target ar",
    ),
    TaskDescriptor(
        task_id="compare",
        name="Compare Texts",
        category=TaskCategory.TEXT,
        description="Compare two texts, configurations, or proposals and highlight trade-offs.",
        required_capabilities=["text"],
        preferred_model_tags=["general", "reasoning"],
        example_usage="localpilot tools compare doc1.txt doc2.txt",
    ),
    TaskDescriptor(
        task_id="explain-code",
        name="Explain Code",
        category=TaskCategory.CODE,
        description="Analyze source code files, explaining logic, architecture, and bugs.",
        required_capabilities=["code"],
        preferred_model_tags=["coding", "code"],
        example_usage="localpilot tools explain-code main.py",
    ),
    TaskDescriptor(
        task_id="generate-code",
        name="Generate Code",
        category=TaskCategory.CODE,
        description="Generate clean, well-structured functions, scripts, or tests from specifications.",
        required_capabilities=["code"],
        preferred_model_tags=["coding", "code"],
        example_usage="localpilot tools generate-code 'FastAPI endpoint for user signup'",
    ),
    TaskDescriptor(
        task_id="refactor-code",
        name="Refactor & Fix Code",
        category=TaskCategory.CODE,
        description="Inspect source files, fix syntax/logic bugs, optimize performance, and improve code structure.",
        required_capabilities=["code"],
        preferred_model_tags=["coding", "code"],
        example_usage="localpilot tools refactor-code script.py --issue 'Fix memory leak'",
    ),
    TaskDescriptor(
        task_id="draft",
        name="Draft Document",
        category=TaskCategory.TEXT,
        description="Draft emails, memos, READMEs, announcements from brief outlines.",
        required_capabilities=["text"],
        preferred_model_tags=["chat", "general"],
        example_usage="localpilot tools draft 'Meeting agenda for product launch'",
    ),
    TaskDescriptor(
        task_id="extract",
        name="Structured Extract",
        category=TaskCategory.STRUCTURED,
        description="Extract structured JSON or key-value fields from messy text.",
        required_capabilities=["text", "structured"],
        preferred_model_tags=["general", "reasoning"],
        example_usage="localpilot tools extract invoice.txt --schema 'invoice_id,amount,date'",
    ),
    TaskDescriptor(
        task_id="classify",
        name="Classify Text",
        category=TaskCategory.STRUCTURED,
        description="Categorize text into one or more user-defined labels.",
        required_capabilities=["text"],
        preferred_model_tags=["general"],
        example_usage="localpilot tools classify feedback.txt --labels 'bug,feature,billing'",
    ),
    TaskDescriptor(
        task_id="review-table",
        name="Review Table",
        category=TaskCategory.STRUCTURED,
        description="Analyze CSV or Markdown tables, computing summaries and finding outliers.",
        required_capabilities=["text", "structured"],
        preferred_model_tags=["general", "reasoning"],
        example_usage="localpilot tools review-table sales.csv",
    ),
    TaskDescriptor(
        task_id="meeting-notes",
        name="Meeting Notes",
        category=TaskCategory.TEXT,
        description="Distill transcripts into executive summaries, decisions, and action items.",
        required_capabilities=["text"],
        preferred_model_tags=["chat", "general"],
        example_usage="localpilot tools meeting-notes transcript.txt",
    ),
    TaskDescriptor(
        task_id="ask-files",
        name="Ask Files (RAG)",
        category=TaskCategory.RETRIEVAL,
        description="Answer questions grounded in local indexed files with source citations.",
        required_capabilities=["retrieval", "text"],
        preferred_model_tags=["general"],
        example_usage="localpilot tools ask-files 'What is the refund policy?'",
    ),
    TaskDescriptor(
        task_id="find-passages",
        name="Find Passages",
        category=TaskCategory.RETRIEVAL,
        description="Retrieve relevant text passages matching a query from indexed collections.",
        required_capabilities=["retrieval"],
        preferred_model_tags=["general"],
        example_usage="localpilot tools find-passages 'network timeout error'",
    ),
    TaskDescriptor(
        task_id="transcribe",
        name="Audio Transcribe",
        category=TaskCategory.AUDIO,
        description="Convert recorded audio or speech into text transcriptions.",
        required_capabilities=["audio"],
        preferred_model_tags=["audio", "whisper"],
        example_usage="localpilot tools transcribe memo.wav",
    ),
    TaskDescriptor(
        task_id="speak",
        name="Text-to-Speech",
        category=TaskCategory.AUDIO,
        description="Synthesize spoken audio from text.",
        required_capabilities=["audio"],
        preferred_model_tags=["tts"],
        example_usage="localpilot tools speak 'Hello and welcome to LocalPilot'",
    ),
    TaskDescriptor(
        task_id="voice",
        name="Voice Conversation",
        category=TaskCategory.AUDIO,
        description="Interactive push-to-talk voice conversation loop.",
        required_capabilities=["audio", "text"],
        preferred_model_tags=["chat", "audio"],
        example_usage="localpilot tools voice",
    ),
    TaskDescriptor(
        task_id="describe-image",
        name="Describe Image",
        category=TaskCategory.MULTIMODAL,
        description="Inspect and provide a comprehensive description of an image.",
        required_capabilities=["vision"],
        preferred_model_tags=["vision", "multimodal"],
        example_usage="localpilot tools describe-image photo.png",
    ),
    TaskDescriptor(
        task_id="read-image",
        name="Read Image (OCR)",
        category=TaskCategory.MULTIMODAL,
        description="Extract text, signs, diagrams, or numbers visible in an image.",
        required_capabilities=["vision"],
        preferred_model_tags=["vision", "multimodal"],
        example_usage="localpilot tools read-image diagram.png",
    ),
    TaskDescriptor(
        task_id="generate-image",
        name="Generate Image (Diffusion)",
        category=TaskCategory.MULTIMODAL,
        description="Formulate expert diffusion prompts or invoke local image synthesis (Stable Diffusion/Flux).",
        required_capabilities=["vision"],
        preferred_model_tags=["diffusion", "vision"],
        example_usage="localpilot tools generate-image 'A cozy reading room with rain outside'",
    ),
    TaskDescriptor(
        task_id="generate-video",
        name="Generate Video (Diffusion)",
        category=TaskCategory.MULTIMODAL,
        description="Formulate cinematic camera/motion direction or invoke local video diffusion pipelines.",
        required_capabilities=["vision"],
        preferred_model_tags=["diffusion", "vision"],
        example_usage="localpilot tools generate-video 'Cinematic drone shot over autumn forest'",
    ),
]


class TaskService:
    """Manages discovery and execution of the 18 local AI task interfaces."""

    def __init__(self, db: Optional[DatabaseManager] = None) -> None:
        self.db = db or get_db()
        self._registry = {t.task_id: t for t in TASK_DEFINITIONS}

    def list_tasks(self, category: Optional[TaskCategory] = None) -> List[TaskDescriptor]:
        """Lists available task interfaces, optionally filtered by category."""
        if category:
            return [t for t in self._registry.values() if t.category == category]
        return list(self._registry.values())

    def get_task(self, task_id: str) -> Optional[TaskDescriptor]:
        """Retrieves a task definition by its ID."""
        return self._registry.get(task_id)

    def resolve_model_for_task(self, task_id: str, requested_model: Optional[str] = None) -> str:
        """Resolves target model alias respecting task affinity and system aliases."""
        if requested_model:
            return requested_model

        task = self.get_task(task_id)
        if not task:
            return "default"

        conn = self.db.get_connection()
        # Look for aliases matching task preferred tags
        for tag in task.preferred_model_tags:
            row = conn.execute("SELECT alias_name FROM model_aliases WHERE alias_name = ?", (tag,)).fetchone()
            if row:
                return row["alias_name"]

        # Default fallback
        row_def = conn.execute("SELECT alias_name FROM model_aliases WHERE alias_name = 'default'").fetchone()
        if row_def:
            return "default"

        # First available alias
        first_alias = conn.execute("SELECT alias_name FROM model_aliases LIMIT 1").fetchone()
        if first_alias:
            return first_alias["alias_name"]

        return "default"

    def check_model_supports_vision(self, model_alias: str) -> bool:
        """Checks whether a given model alias or target model supports vision inputs."""
        alias_lower = model_alias.lower()
        if any(k in alias_lower for k in ("vision", "llava", "minicpm", "moondream", "vl", "multimodal")):
            return True
        conn = self.db.get_connection()
        row = conn.execute(
            """
            SELECT l.task_type
            FROM model_aliases a
            JOIN logical_models l ON a.logical_model_id = l.model_id
            WHERE a.alias_name = ?
            """,
            (model_alias,),
        ).fetchone()
        if row and row["task_type"] in ("vision", "multimodal"):
            return True
        return False

    def execute_task(
        self,
        task_id: str,
        input_text: str,
        parameters: Optional[Dict[str, Any]] = None,
        model_alias: Optional[str] = None,
        gateway_url: str = "http://127.0.0.1:8080/v1",
        api_key: Optional[str] = None,
        mock_handler: Optional[Callable[[str, str, Dict[str, Any]], str]] = None,
    ) -> TaskRun:
        """Executes a task interface, formatting prompt and recording the run."""
        task = self.get_task(task_id)
        if not task:
            raise ValueError(f"Unknown task ID: '{task_id}'. Run 'localpilot tools list' for valid tasks.")

        resolved_model = self.resolve_model_for_task(task_id, model_alias)
        params = parameters or {}
        now_str = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        run_id = f"task_{uuid.uuid4().hex[:10]}"

        # Validate vision requirements for image-input tasks
        if task_id in ("describe-image", "read-image"):
            img_path = Path(input_text.strip())
            if not img_path.exists() or not img_path.is_file():
                raise ValueError(f"Image file '{input_text}' does not exist on disk.")
            if not self.check_model_supports_vision(resolved_model) and not mock_handler:
                raise ValueError(
                    f"Selected model '{resolved_model}' is text-only and does not support vision processing. "
                    f"Please install or select a multimodal vision model (e.g. LLaVA, MiniCPM-V) or run "
                    f"'localpilot discover --task vision' to find compatible models."
                )

        # Construct task-specific system prompt
        system_instruction = self._build_system_instruction(task_id, params)
        user_prompt = self._build_user_prompt(task_id, input_text, params)

        t_start = time.perf_counter()
        output_text = ""
        error_msg = None
        status = TaskStatus.SUCCESS

        try:
            if mock_handler:
                output_text = mock_handler(task_id, user_prompt, params)
            else:
                output_text = self._call_inference(
                    gateway_url=gateway_url,
                    model_alias=resolved_model,
                    system_prompt=system_instruction,
                    user_prompt=user_prompt,
                    api_key=api_key,
                )
        except Exception as exc:
            status = TaskStatus.FAILED
            error_msg = str(exc)

        duration_ms = (time.perf_counter() - t_start) * 1000.0
        tokens_used = len(input_text.split()) + len(output_text.split())

        run = TaskRun(
            run_id=run_id,
            task_id=task_id,
            model_alias=resolved_model,
            status=status,
            input_summary=(input_text[:120] + "...") if len(input_text) > 120 else input_text,
            output_text=output_text if status == TaskStatus.SUCCESS else None,
            duration_ms=round(duration_ms, 2),
            tokens_used=tokens_used,
            error_message=error_msg,
            created_at=now_str,
        )

        artifacts = []
        if status == TaskStatus.SUCCESS and output_text:
            artifacts.append({
                "artifact_id": f"art_{uuid.uuid4().hex[:8]}",
                "name": f"{task_id}_output.txt",
                "artifact_type": "text",
                "content": output_text,
                "created_at": now_str,
            })

        self.db.record_task_run(run.model_dump(), artifacts=artifacts)
        return run

    def _build_system_instruction(self, task_id: str, params: Dict[str, Any]) -> str:
        """Constructs dedicated system instruction guiding model behavior for each task."""
        if task_id == "summarize":
            bullets = params.get("bullets", False)
            if bullets:
                return "You are an expert summarizer. Condense the user's text into clear, high-signal bullet points."
            return "You are an expert summarizer. Provide an executive summary of the provided text."

        if task_id == "rewrite":
            tone = params.get("tone", "clear and concise")
            return f"You are a professional editor. Rewrite the text preserving its meaning with a {tone} tone."

        if task_id == "translate":
            target_lang = params.get("target_lang", "English")
            return f"You are a professional translator. Translate the given text into {target_lang} accurately."

        if task_id == "explain-code":
            return "You are a senior software architect. Explain the following code clearly, detailing its purpose, architecture, and potential pitfalls."

        if task_id == "generate-code":
            return "You are an expert software engineer. Generate production-ready, clean, well-commented code that fulfills the user specification exactly."

        if task_id == "refactor-code":
            issue = params.get("issue", "code quality, bug fixes, and performance")
            return f"You are a principal code reviewer. Refactor and improve the provided source code addressing: {issue}. Provide the corrected code with clear explanations."

        if task_id == "generate-image":
            return (
                "You are an expert AI image generation director. Generate a detailed, highly effective Stable Diffusion / Flux text-to-image prompt, "
                "including subject description, aesthetic lighting, composition, style keywords, recommended negative prompt, and optimal sampling steps."
            )

        if task_id == "generate-video":
            return (
                "You are an expert AI video generation director. Generate cinematic shot direction for video diffusion models, "
                "including camera movement, frame composition, subject motion, lighting progression, recommended fps, and aspect ratio."
            )

        if task_id == "compare":
            return "You are an analytical evaluator. Compare the provided items thoroughly, noting similarities, differences, advantages, and trade-offs."

        if task_id == "draft":
            doc_type = params.get("type", "document")
            return f"You are an executive writer. Draft a well-structured {doc_type} based on the user's outline."

        if task_id == "extract":
            schema = params.get("schema", "JSON object")
            return f"You are a structured data extraction engine. Extract information matching: {schema}. Return strictly valid JSON."

        if task_id == "classify":
            labels = params.get("labels", [])
            labels_str = ", ".join(labels) if isinstance(labels, list) else str(labels)
            return f"You are a text classification engine. Classify the user text into one of these categories: {labels_str}."

        if task_id == "review-table":
            return "You are a senior data analyst. Review the provided tabular data, summarizing key trends, anomalies, and insights."

        if task_id == "meeting-notes":
            return "You are an executive secretary. Distill this meeting transcript into: 1. Executive Summary, 2. Key Decisions, 3. Action Items (Owner & Task)."

        if task_id in ("describe-image", "read-image"):
            return "You are an advanced visual AI. Inspect the provided image details thoroughly and report accurately."

        return "You are LocalPilot, an offline private AI assistant running on the user's computer."

    def _build_user_prompt(self, task_id: str, input_text: str, params: Dict[str, Any]) -> str:
        """Combines input text and task parameters into clean prompt formatting."""
        if task_id == "translate":
            target_lang = params.get("target_lang", "English")
            return f"Target Language: {target_lang}\n\nText:\n{input_text}"

        if task_id == "extract":
            schema = params.get("schema", "JSON")
            return f"Schema:\n{schema}\n\nInput Document:\n{input_text}"

        if task_id == "classify":
            labels = params.get("labels", "")
            return f"Allowed Categories: {labels}\n\nText to Classify:\n{input_text}"

        if task_id == "generate-code":
            lang = params.get("language", "best-suited language")
            return f"Target Language/Framework: {lang}\n\nSpecification:\n{input_text}\n\nGenerate complete, runnable code with documentation."

        if task_id == "refactor-code":
            issue = params.get("issue", "general review and optimization")
            return f"Instruction / Issue:\n{issue}\n\nSource Code:\n{input_text}\n\nProvide the refactored code and an itemized explanation of changes."

        if task_id == "generate-image":
            style = params.get("style", "photorealistic")
            return f"Image Request: {input_text}\nPreferred Style: {style}\n\nProvide the diffusion generation prompt, recommended negative prompt, and sampling parameters."

        if task_id == "generate-video":
            return f"Video Request: {input_text}\n\nProvide shot progression, camera motion vectors, lighting, and video diffusion prompt."

        return input_text

    def _call_inference(
        self,
        gateway_url: str,
        model_alias: str,
        system_prompt: str,
        user_prompt: str,
        api_key: Optional[str] = None,
    ) -> str:
        """Invokes the local gateway endpoint."""
        url = f"{gateway_url.rstrip('/')}/chat/completions"
        payload = json.dumps({
            "model": model_alias,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.3,
            "max_tokens": 2048,
        }).encode("utf-8")

        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=120.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data["choices"][0]["message"]["content"]
