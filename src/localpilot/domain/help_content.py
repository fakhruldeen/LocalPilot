"""Centralized, typed help-content and contextual guidance architecture.

Provides a unified repository of Layer 1 (labels), Layer 2 (contextual help),
Layer 3 (technical details), and Layer 4 (full guides) for both CLI and TUI.
Adheres strictly to the preferred terminology and contextual explanations in
LocalPilot guidance specifications.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional


@dataclass(frozen=True)
class HelpTopic:
    """Structured help topic contract fulfilling Section 11 requirements."""

    topic_id: str
    title: str
    short_help: str  # Layer 2: 1-2 sentence contextual tooltip / focus summary
    extended_help: str  # Layer 3/4: detailed explanation of consequences & assumptions
    related_actions: List[str] = field(default_factory=list)
    technical_details: Optional[str] = None  # Layer 3: underlying flags, enums, formulas
    runtime_conditions: Optional[str] = None  # Platform or runtime applicability
    translation_key: Optional[str] = None


# Shared terminology definitions (Section 5)
TERMINOLOGY = {
    "model": "The AI you download and use on your computer (software library or artificial brain).",
    "supporting_software": "The runtime engine that runs the model (e.g. llama.cpp or Ollama).",
    "context": "How much text the model can consider at once. Larger memory settings require more RAM.",
    "graphics_memory": "Memory on your graphics card (VRAM), used to accelerate model generation.",
    "compact_model_version": "A version designed to use less memory (quantized), which may subtly affect output quality.",
    "running_model": "A model currently loaded in memory or active according to observed system state.",
    "access_key": "Permission to access a source repository (never an account password).",
    "stop_vs_remove": "Stopping frees system memory while keeping downloaded files; removing uninstalls and deletes model files to free disk space.",
}


# Canonical Help Topics fulfilling Section 6 requirements
HELP_TOPICS: Dict[str, HelpTopic] = {
    # 6.A Model Sources
    "model_sources": HelpTopic(
        topic_id="model_sources",
        title="Model Sources",
        short_help="A source is where LocalPilot gets model files or model information.",
        extended_help=(
            "A source is where LocalPilot gets model files or model information.\n\n"
            "Distinction: A source (such as Hugging Face or the Ollama Library) provides the model weights "
            "and metadata. Supporting software (like llama.cpp or Ollama) is the local engine that actually "
            "executes those files on your hardware.\n\n"
            "Not every source supports every model format or execution method. LocalPilot verifies source "
            "compatibility before offering download options."
        ),
        related_actions=["localpilot discover", "Browse models"],
        technical_details="Sources: 'huggingface' (GGUF direct download via HTTPS) or 'ollama' (registry pull API).",
    ),

    # 6.B Execution Preferences
    "exec_automatic": HelpTopic(
        topic_id="exec_automatic",
        title="Execution Preference: Automatic",
        short_help="LocalPilot chooses a supported configuration for this computer.",
        extended_help=(
            "LocalPilot automatically inspects your CPU, available RAM, and graphics card to select "
            "the most reliable and responsive configuration without manual configuration."
        ),
        related_actions=["Apply Settings", "Set up model"],
        technical_details="Evaluates unified memory (macOS) vs separate RAM/VRAM (Linux/Windows) and selects direct worker or localhost service.",
    ),
    "exec_cpu": HelpTopic(
        topic_id="exec_cpu",
        title="Execution Preference: Processor (CPU)",
        short_help="Use your processor. Responses may be slower for some models.",
        extended_help=(
            "Runs the model strictly using your computer's main processor (CPU) and system RAM. "
            "This works on virtually any computer and leaves your graphics card completely free for other tasks, "
            "though generation speed may be slower on larger models."
        ),
        related_actions=["Apply Settings"],
        technical_details="Uses llama.cpp AVX2/AVX-512 CPU thread pool. Requires no GPU drivers or CUDA.",
    ),
    "exec_gpu": HelpTopic(
        topic_id="exec_gpu",
        title="Execution Preference: Graphics Card (GPU)",
        short_help="Use supported graphics acceleration. Availability depends on this computer and the model.",
        extended_help=(
            "Accelerates text generation by offloading layers into dedicated graphics memory (VRAM). "
            "Availability depends on having a compatible graphics card (e.g. Apple Metal or NVIDIA CUDA) "
            "and sufficient free graphics memory."
        ),
        related_actions=["Apply Settings"],
        technical_details="Requires compatible GPU with safe VRAM budget >= model weights + KV cache.",
    ),
    "exec_hybrid": HelpTopic(
        topic_id="exec_hybrid",
        title="Execution Preference: Supported CPU + GPU",
        short_help="Share supported work between processor memory and graphics memory.",
        extended_help=(
            "Shares supported computation between your system RAM and graphics memory. "
            "Note: System RAM and graphics memory cannot always be combined freely; only specific runtimes "
            "(such as llama.cpp) support partial layer offloading when hardware conditions allow."
        ),
        related_actions=["Apply Settings"],
        technical_details="Calculates offload_ratio = (safe_vram - overhead) / total_weight_bytes. Remainder loaded into CPU RAM.",
    ),

    # 6.C Recommendation Score
    "recommendation_score": HelpTopic(
        topic_id="recommendation_score",
        title="Why This Recommendation?",
        short_help="This score ranks how well the proposed model configuration matches your preferences and computer.",
        extended_help=(
            "This score ranks how well the proposed model configuration matches your preferences and computer.\n\n"
            "Key Facts:\n"
            "• It is a match ranking, NOT an absolute intelligence or capability score.\n"
            "• Compatibility is strictly verified first: incompatible models are excluded before ranking.\n"
            "• Estimates are based on measured hardware budgets, not absolute guarantees.\n\n"
            "Scoring Weights:\n"
            "• Release Newness: 40% (prefers actively maintained, modern architectures)\n"
            "• Smaller Download: 25% (minimizes bandwidth and storage requirements)\n"
            "• Usable Context: 20% (rewards models supporting longer conversations up to goal target)\n"
            "• Memory Headroom: 15% (rewards models leaving plenty of RAM for other programs)"
        ),
        related_actions=["Compare Options (c)", "Set Up Model"],
        technical_details="Score formula: total = 40*newness + 25*compactness + 20*context + 15*headroom (bounded 0 to 100).",
    ),

    # 6.D Compatibility & Confidence
    "compat_status": HelpTopic(
        topic_id="compat_status",
        title="Compatibility & Confidence Levels",
        short_help="How LocalPilot determines whether a model can run safely on your computer.",
        extended_help=(
            "LocalPilot evaluates models across distinct compatibility dimensions:\n\n"
            "• Supported: The model architecture and file format are recognized and runnable by local engines.\n"
            "• Expected to Fit: The model's weights and working memory fall comfortably within your safe memory budget.\n"
            "• Tested on this Computer: The model has previously executed an inference probe successfully on your system.\n"
            "  (Note: An older successful test does not guarantee current readiness if available RAM has changed.)\n"
            "• Metadata Confidence: High (verified checksums & publisher metadata) vs Community (user-contributed).\n"
            "• Current Health: Live responsiveness check of active processes."
        ),
        related_actions=["localpilot doctor", "Run diagnostics"],
        technical_details="Memory bounds: safe_ram_budget = max(0, available_ram - 512MB). Safe VRAM = max(0, available_vram - 256MB).",
    ),

    # 6.E Download Size and Storage
    "storage_footprint": HelpTopic(
        topic_id="storage_footprint",
        title="Download Size vs Total Storage",
        short_help="Download size is the data transferred; total storage includes runtimes, temporary files, and recovery copies.",
        extended_help=(
            "Download size is how much data must be transferred over your network.\n\n"
            "Total storage may also include supporting software (runtimes), temporary decompression files, "
            "and recovery backups. LocalPilot displays measured values for existing files and clear estimates "
            "for pending downloads so you never run out of disk space unexpectedly."
        ),
        related_actions=["localpilot storage", "View storage breakdown"],
        technical_details="Stored in ~/.local/share/localpilot/. Backed by SQLite reference-counted storage_artifacts table.",
    ),

    # 6.F Context Window
    "context_window": HelpTopic(
        topic_id="context_window",
        title="Context Window",
        short_help="Context is how much text the model can consider at once. Larger settings may require more memory.",
        extended_help=(
            "Context is how much text the model can consider at once (such as your conversation history, "
            "pasted documents, or code files). Larger context settings allow the model to remember longer "
            "conversations, but require additional memory (KV cache) on your computer."
        ),
        related_actions=["Review Setup"],
        technical_details="KV cache memory scales with context tokens: context_tokens * 512 * max(0.2, params_b / 7.0) * concurrency.",
    ),

    # 6.G Memory Headroom
    "memory_headroom": HelpTopic(
        topic_id="memory_headroom",
        title="Memory Headroom",
        short_help="Headroom is memory left available under the proposed configuration.",
        extended_help=(
            "Headroom is memory left available for your operating system, web browser, and other applications "
            "while the model is loaded. Maintaining healthy headroom keeps your computer responsive, though it "
            "does not guarantee against extreme system memory pressure from third-party apps."
        ),
        related_actions=["localpilot doctor", "Computer details"],
        technical_details="OS safety buffer: 512 MB RAM reserve and 256 MB VRAM reserve enforced before model allocation.",
    ),

    # 6.H Model Variants
    "model_variants": HelpTopic(
        topic_id="model_variants",
        title="Compact Model Versions (Quantization)",
        short_help="These versions reduce memory use. They can also change output quality or performance.",
        extended_help=(
            "Compact model versions use quantization techniques to reduce file size and memory footprint. "
            "For example, a 4-bit version (Q4_K_M) uses less than half the RAM of the original model while "
            "preserving high response accuracy. However, different compact versions may have subtle differences "
            "in speed or nuances of reasoning."
        ),
        related_actions=["Browse Full Catalog"],
        technical_details="Supported quants: Q4_K_M (standard recommended balance), Q5_K_M, Q8_0, FP16.",
    ),

    # 6.I Sign-in and Access Keys
    "auth_keys": HelpTopic(
        topic_id="auth_keys",
        title="Access Keys & Permissions",
        short_help="Permission to access specific model files on Hugging Face (never your account password).",
        extended_help=(
            "Certain models (such as gated models) require agreeing to publisher license terms on Hugging Face "
            "before downloading.\n\n"
            "• Which source: Hugging Face (https://huggingface.co/settings/tokens).\n"
            "• Why: The publisher requires users to accept their terms of use.\n"
            "• How to obtain: Create a free read-only personal access token on Hugging Face.\n"
            "• How it is stored: Securely in your operating system's credential store (or encrypted local file fallback).\n"
            "• Security guarantee: LocalPilot never requests your password and never logs or transmits your token."
        ),
        related_actions=["Save Key and Continue"],
        technical_details="Stored via OS SecretService/Keychain or encrypted fallback. Automatically redacted in logs.",
    ),

    # 6.J Stop versus Remove
    "stop_vs_remove": HelpTopic(
        topic_id="stop_vs_remove",
        title="Stop vs Remove",
        short_help="Stop unloads the model from RAM; Remove deletes files from disk to reclaim storage.",
        extended_help=(
            "Understanding the difference between stopping and removing a model:\n\n"
            "• Stop Model:\n"
            "  Unloads or stops this model through its supported runtime. Your memory (RAM/GPU) is freed, "
            "  but all downloaded files and settings are kept so you can chat again instantly.\n\n"
            "• Remove Model:\n"
            "  Removes the selected installation and deletes approved weight files from your hard drive to "
            "  reclaim disk space. Your other models, conversation logs, and runtimes remain untouched.\n\n"
            "Shared-service notice: Stopping a model running on a shared service (like Ollama) unloads the model "
            "weights without killing the shared background daemon."
        ),
        related_actions=["Stop Model (X)", "Remove Model (Delete)"],
        technical_details="Stop calls unload_model() API or terminates owned PID. Remove executes reference-counted safe_remove_installation().",
    ),

    # 6.K Model vs Application Updates
    "updates_explanation": HelpTopic(
        topic_id="updates_explanation",
        title="Model Updates vs Application Updates",
        short_help="Model updates refresh model weights; application updates upgrade the LocalPilot software.",
        extended_help=(
            "LocalPilot separates three kinds of updates:\n\n"
            "1. Model Update: Downloads newer revisions of model weights or prompt templates from the publisher.\n"
            "2. Supporting Software Update: Upgrades the local execution engine (such as llama.cpp or Ollama).\n"
            "3. Application Update: Upgrades LocalPilot itself with new features and safety improvements."
        ),
        related_actions=["localpilot update", "localpilot self update"],
        technical_details="Model update checks Git commit/digest in upstream registry with rollback support. Self update queries PyPI API.",
    ),

    # 6.L Closing LocalPilot / Process Ownership
    "closing_localpilot": HelpTopic(
        topic_id="closing_localpilot",
        title="What Happens When You Close LocalPilot",
        short_help="Owned background workers stop cleanly; external services like Ollama remain untouched.",
        extended_help=(
            "When you exit LocalPilot (CLI or TUI):\n\n"
            "• Direct workers started by LocalPilot are stopped cleanly to ensure no orphaned processes consume RAM.\n"
            "• External services (such as an existing Ollama or Docker daemon) are left untouched so your other "
            "  applications continue working normally.\n"
            "• All downloaded model files and configuration remain safely stored on your disk for your next session."
        ),
        related_actions=["Exit (q)"],
        technical_details="Process ownership classes: LOCALPILOT_OWNED (terminated on exit) vs EXTERNAL_APPROVED / READONLY_DISCOVERED (preserved).",
    ),

    # Recovery Actions (Section 10)
    "recovery_actions": HelpTopic(
        topic_id="recovery_actions",
        title="Error Recovery Actions",
        short_help="Clear, supported recovery choices when an operation encounters an issue.",
        extended_help=(
            "Every recovery choice in LocalPilot has a verified, predictable outcome:\n\n"
            "• Retry: Repeats the operation after re-verifying computer health and network connectivity.\n"
            "• Resume: Continues retained progress from where it was interrupted (e.g. continuing a partial download).\n"
            "• Check Again: Refreshes system measurements and observations to confirm actual current state.\n"
            "• Restore: Safely restores a previously saved backup copy if an update was interrupted.\n"
            "• Remove / Clean Up: Safely deletes partial or corrupted temporary files while keeping other models intact."
        ),
        related_actions=["Retry", "Resume", "Check Again", "Clean Up"],
        technical_details="Recovery actions mapped to typed RecoveryAction enum with sanitized support IDs.",
    ),

    # Empty States (Section 9)
    "empty_installed": HelpTopic(
        topic_id="empty_installed",
        title="No Installed Models",
        short_help="No models are installed yet. Find a suitable model to get started.",
        extended_help=(
            "You don't have any models installed on this computer yet.\n"
            "Click 'Help me get started' or 'Browse models' to find options verified to fit your hardware."
        ),
        related_actions=["Help me get started", "Browse models"],
    ),
    "empty_running": HelpTopic(
        topic_id="empty_running",
        title="No Running Models",
        short_help="No models are currently running. Choose an installed model to start chatting.",
        extended_help=(
            "No models are currently loaded in memory. Your computer's RAM and GPU are fully free for other apps.\n"
            "To chat, go to 'Installed Models' and press Enter, or use the guided setup to add a model."
        ),
        related_actions=["My models", "Help me get started"],
    ),
    "empty_discover": HelpTopic(
        topic_id="empty_discover",
        title="No Compatible Models Found",
        short_help="No models in the current catalog fit within your computer's available memory.",
        extended_help=(
            "None of the models currently in the catalog fit within your computer's safe memory budget.\n\n"
            "Suggestions:\n"
            "• Close other memory-intensive applications and run 'localpilot doctor' to refresh available RAM.\n"
            "• Search for smaller compact models (e.g. 1B or 0.5B parameters)."
        ),
        related_actions=["localpilot doctor", "Refresh"],
    ),
}


def get_help_topic(topic_id: str) -> Optional[HelpTopic]:
    """Retrieve a HelpTopic by its stable identifier."""
    return HELP_TOPICS.get(topic_id)


def get_contextual_guidance(control_id: str) -> str:
    """Returns Layer 2 contextual guidance string for a focused UI control or action."""
    cid = (control_id or "").strip().lower()

    mapping = {
        # Home & Wizard actions
        "btn-get-started": "Start the 4-step recommended setup wizard to find and set up the best model for your computer.",
        "get-started-btn": "Start the 4-step recommended setup wizard to find and set up the best model for your computer.",
        "btn-continue-chat": "Open instant conversation with your most recently used installed model.",
        "btn-home-discover": "Browse compatible models in the catalog that are verified to fit your computer's memory.",
        "browse-models-btn": "Browse compatible models in the catalog that are verified to fit your computer's memory.",
        "btn-home-installed": "View and manage your personal library of ready-to-run models.",
        "btn-home-find-another": "Find and set up an additional model for writing, coding, or reasoning.",
        "btn-home-running": "View models currently loaded in memory and check live performance.",
        "btn-home-help": "Open the comprehensive documentation guide, FAQ, and technical manual.",
        "btn-resume-interrupted": "Resume downloading from where it stopped. Completed progress has been retained.",
        "btn-cleanup-interrupted": "Delete temporary download files safely to reclaim disk space.",
        "quit-btn": "Exit LocalPilot. Active instances are stopped and memory is freed cleanly.",

        # Wizard steps
        "step1-next-btn": "Continue to select what you would like your local AI to do.",
        "step1-perf-btn": "Choose whether LocalPilot uses your CPU, graphics card (GPU), or automatic detection.",
        "step1-details-btn": "Inspect full hardware specifications including memory budgets and CPU features.",
        "step2-next-btn": "Search the catalog for the best verified models matching your selected goal.",
        "step3-next-btn": "Review the exact download size, storage location, and execution plan before installing.",
        "step3-compare-btn": "Open a side-by-side comparison table of the top recommended models.",
        "step3-catalog-btn": "Explore all compatible models in the full catalog.",
        "step4-approve-btn": "Begin downloading and verifying the approved model. You can cancel safely at any time.",
        "step5-cancel-btn": "Stop the setup. Partial files will be safely removed without affecting anything else.",
        "step6-chat-btn": "Open full-screen chat with your new verified model.",

        # Performance options
        "perf-auto": "LocalPilot will automatically select a supported configuration for this computer.",
        "radio-auto": "LocalPilot will automatically select a supported configuration for this computer.",
        "perf-cpu": "Use your system processor (CPU). Responses may be slower for some models.",
        "radio-cpu": "Use your system processor (CPU). Responses may be slower for some models.",
        "perf-gpu": "Use your graphics card (GPU). Availability depends on this computer and the model.",
        "radio-gpu": "Use your graphics card (GPU). Availability depends on this computer and the model.",
        "perf-hybrid": "Share supported work between both your CPU and GPU where engine supports layer offloading.",
        "radio-hybrid": "Share supported work between both your CPU and GPU where engine supports layer offloading.",

        # Tables
        "installed-table": "View and manage your installed models. Press Enter or 'c' to chat, 'd' for details, 'x' to remove.",
        "running-table": "View models currently active in memory. Press 's' to stop an instance and free RAM.",
        "discover-table": "Browse verified compatible models. Press Enter or 's' to inspect setup plan and begin installation.",

        # Table actions
        "btn-discover-setup": "Review the execution plan and set up the selected model on your computer.",
        "btn-installed-chat": "Start the model and open an interactive chat session.",
        "btn-installed-details": "Inspect technical metadata, file paths, integrity hashes, and execution plans.",
        "btn-installed-remove": "Remove this model and delete its files to free up disk space.",
        "btn-running-chat": "Switch directly into the active chat session with this running model.",
        "btn-running-details": "View active process ID (PID), local endpoint URL, and health diagnostics.",
        "btn-running-stop": "Unload this model from memory so your computer's RAM and GPU are freed.",

        # Inputs & Search
        "search-input": "Type keywords (e.g. coding, llama, 3b) to filter models that fit your computer.",
        "chat-user-input": "Type your message or question and press Enter to send to the model.",
        "auth-token-input": "Enter your read-only Hugging Face access key (starts with 'hf_'). Stored securely on your device.",
        "token-input": "Enter your read-only Hugging Face access key (starts with 'hf_'). Stored securely on your device.",
    }
    return mapping.get(cid, "LocalPilot: Use arrow keys or Tab to navigate. Press '?' or 'F1' for full guide.")
