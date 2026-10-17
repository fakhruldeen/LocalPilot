"""Integrated in-TUI conversational ChatScreen with live token streaming."""

from __future__ import annotations
import asyncio
from typing import Dict, List, Optional
from textual import work
from textual.app import ComposeResult
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, Input, Label, Static

from localpilot.application.chat_service import ChatService


class ChatMessageWidget(Static):
    """Render a single message bubble for user or assistant."""

    def __init__(self, role: str, content: str, model_name: str = "Assistant") -> None:
        super().__init__()
        self._role = role
        self._content = content
        self._model_name = model_name

    def compose(self) -> ComposeResult:
        if self._role == "user":
            header = "[bold cyan]You[/bold cyan]"
        else:
            header = f"[bold green]{self._model_name}[/bold green]"
        with Vertical(classes=f"message-box message-{self._role}"):
            yield Label(header, classes="message-header")
            yield Label(self._content, classes="message-content", id="msg-body")

    def append_text(self, text: str) -> None:
        self._content += text
        try:
            body = self.query_one("#msg-body", Label)
            body.update(self._content)
        except Exception:
            pass


class ChatScreen(Screen[None]):
    """Full-screen interactive chat screen inside the Textual TUI."""

    BINDINGS = [
        ("escape", "action_back", "Back to Models"),
        ("ctrl+n", "action_new_chat", "New Chat"),
    ]

    CSS = """
    ChatScreen {
        background: #121212;
    }
    #chat-header-bar {
        dock: top;
        height: 3;
        background: #1f1f1f;
        padding: 0 2;
        align: left middle;
        border-bottom: solid #00e5ff;
    }
    #chat-model-title {
        color: #00e5ff;
        text-style: bold;
    }
    #chat-status-label {
        margin-left: 2;
        color: #888888;
    }
    #chat-messages-scroll {
        height: 1fr;
        padding: 1 2;
    }
    .message-box {
        margin-bottom: 1;
        padding: 1 2;
        border: round #333333;
        background: #181818;
    }
    .message-user {
        border-left: thick #00e5ff;
    }
    .message-assistant {
        border-left: thick #00e676;
    }
    .message-header {
        margin-bottom: 1;
    }
    .message-content {
        color: #e0e0e0;
    }
    #chat-input-bar {
        dock: bottom;
        height: 4;
        background: #1f1f1f;
        padding: 0 1;
        align: left middle;
        border-top: solid #333333;
    }
    #chat-user-input {
        width: 1fr;
    }
    #chat-actions {
        width: auto;
        margin-left: 1;
    }
    #chat-actions Button {
        margin-left: 1;
    }
    """

    def __init__(
        self,
        model_name: str,
        model_id: str,
        endpoint_url: str,
        runtime_name: str = "llamacpp",
        instance_id: Optional[str] = None,
    ) -> None:
        super().__init__()
        self.model_name = model_name
        self.model_id = model_id
        self.endpoint_url = endpoint_url
        self.runtime_name = runtime_name
        self.instance_id = instance_id or model_id
        self.messages: List[Dict[str, str]] = []
        self._current_assistant_widget: Optional[ChatMessageWidget] = None
        self._is_generating = False

    def compose(self) -> ComposeResult:
        with Horizontal(id="chat-header-bar"):
            yield Label(f"Chatting with {self.model_name}", id="chat-model-title")
            yield Label("• Ready", id="chat-status-label")

        with VerticalScroll(id="chat-messages-scroll"):
            # Initial welcoming placeholder
            yield Label(
                f"[dim]Connected to {self.model_name}. Type a message below and press Enter to chat.[/dim]",
                id="chat-welcome-placeholder",
            )

        with Horizontal(id="chat-input-bar"):
            yield Input(
                placeholder="Type your message here... (Press Enter to send)",
                id="chat-user-input",
            )
            with Horizontal(id="chat-actions"):
                yield Button("Send", variant="primary", id="send-msg-btn")
                yield Button("New Chat", variant="default", id="new-chat-btn")
                yield Button("Back", variant="default", id="back-to-models-btn")

    def on_mount(self) -> None:
        self.query_one("#chat-user-input", Input).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "send-msg-btn":
            self._submit_current_input()
        elif event.button.id == "new-chat-btn":
            self.action_new_chat()
        elif event.button.id == "back-to-models-btn":
            self.action_back()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "chat-user-input":
            self._submit_current_input()

    def _submit_current_input(self) -> None:
        if self._is_generating:
            return
        inp = self.query_one("#chat-user-input", Input)
        user_text = inp.value.strip()
        if not user_text:
            return

        inp.value = ""
        # Remove welcome placeholder if still visible
        try:
            placeholder = self.query_one("#chat-welcome-placeholder")
            placeholder.remove()
        except Exception:
            pass

        # 1. Mount user message widget
        scroll_area = self.query_one("#chat-messages-scroll", VerticalScroll)
        user_widget = ChatMessageWidget("user", user_text, self.model_name)
        scroll_area.mount(user_widget)

        # 2. Add to conversation history
        self.messages.append({"role": "user", "content": user_text})

        # 3. Mount placeholder assistant widget
        self._current_assistant_widget = ChatMessageWidget("assistant", "", self.model_name)
        scroll_area.mount(self._current_assistant_widget)
        scroll_area.scroll_end(animate=False)

        # 4. Update status and launch streaming worker
        self._is_generating = True
        self.query_one("#chat-status-label", Label).update("• Generating response...")
        self._stream_response_worker()

    @work(exclusive=True, thread=True)
    def _stream_response_worker(self) -> None:
        async def _run_stream():
            full_reply: List[str] = []
            try:
                async for token in ChatService.stream_message(
                    self.endpoint_url,
                    self.runtime_name,
                    self.model_id,
                    self.messages,
                ):
                    full_reply.append(token)
                    self.call_from_thread(self._append_token_to_assistant, token)

                reply_text = "".join(full_reply)
                self.messages.append({"role": "assistant", "content": reply_text})
                self.call_from_thread(self._finish_generation, None)
            except Exception as e:
                err_msg = f"Failed to get response from model: {e}"
                self.call_from_thread(self._finish_generation, err_msg)

        asyncio.run(_run_stream())

    def _append_token_to_assistant(self, token: str) -> None:
        if self._current_assistant_widget:
            self._current_assistant_widget.append_text(token)
            scroll_area = self.query_one("#chat-messages-scroll", VerticalScroll)
            scroll_area.scroll_end(animate=False)

    def _finish_generation(self, error: Optional[str] = None) -> None:
        self._is_generating = False
        status = self.query_one("#chat-status-label", Label)
        if error:
            status.update(f"[bold red]• Error: {error}[/bold red]")
            if self._current_assistant_widget:
                self._current_assistant_widget.append_text(f"\n\n[red]{error}[/red]")
        else:
            status.update("• Ready")
        self.query_one("#chat-user-input", Input).focus()

    def action_new_chat(self) -> None:
        self.messages.clear()
        scroll_area = self.query_one("#chat-messages-scroll", VerticalScroll)
        scroll_area.remove_children()
        scroll_area.mount(
            Label(
                f"[dim]New conversation started with {self.model_name}. Type your question below.[/dim]",
                id="chat-welcome-placeholder",
            )
        )
        self.query_one("#chat-status-label", Label).update("• Ready")
        self.query_one("#chat-user-input", Input).focus()

    def action_back(self) -> None:
        self.app.pop_screen()
