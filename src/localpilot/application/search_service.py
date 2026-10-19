"""Document indexing, passage chunking, and local grounded retrieval (RAG) engine."""

from __future__ import annotations
import math
import os
import re
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from localpilot.domain.task_contract import (
    SearchCollection,
    SearchPassage,
    SearchResult,
)
from localpilot.storage.db import DatabaseManager, get_db

SUPPORTED_EXTENSIONS = {
    ".txt", ".md", ".markdown", ".rst", ".py", ".js", ".ts", ".html",
    ".css", ".json", ".yaml", ".yml", ".toml", ".csv", ".sh", ".c", ".cpp", ".h"
}


def _tokenize(text: str) -> List[str]:
    """Simple alphanumeric lowercase word tokenization."""
    return re.findall(r"\w+", text.lower())


class SearchService:
    """Manages document collections, passage chunking, and offline BM25 retrieval."""

    def __init__(self, db: Optional[DatabaseManager] = None) -> None:
        self.db = db or get_db()

    def add_collection(self, path: Path | str, name: Optional[str] = None) -> SearchCollection:
        """Indexes files in a directory and creates a searchable collection."""
        src_path = Path(path).resolve()
        if not src_path.exists():
            raise ValueError(f"Path does not exist: {src_path}")

        col_name = name or src_path.name or f"col_{uuid.uuid4().hex[:6]}"
        existing = self.db.get_search_collection(col_name)
        col_id = existing["collection_id"] if existing else f"col_{uuid.uuid4().hex[:8]}"

        files_to_index: List[Path] = []
        if src_path.is_file():
            files_to_index.append(src_path)
        else:
            for root, dirs, files in os.walk(src_path):
                # Skip hidden directories (.git, .venv, etc.)
                dirs[:] = [d for d in dirs if not d.startswith(".")]
                for f in files:
                    if not f.startswith("."):
                        fp = Path(root) / f
                        if fp.suffix.lower() in SUPPORTED_EXTENSIONS or fp.suffix.lower() == ".pdf":
                            files_to_index.append(fp)

        passages: List[Dict[str, Any]] = []
        for file_path in files_to_index:
            try:
                passages.extend(self._chunk_file(col_id, file_path, src_path))
            except Exception:
                continue

        now_str = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        self.db.save_search_collection(
            collection_id=col_id,
            name=col_name,
            path=str(src_path),
            file_count=len(files_to_index),
            passage_count=len(passages),
        )

        if passages:
            self.db.save_search_passages(passages)

        return SearchCollection(
            collection_id=col_id,
            name=col_name,
            path=str(src_path),
            file_count=len(files_to_index),
            passage_count=len(passages),
            last_indexed_at=now_str,
            status="ready",
        )

    def _chunk_file(self, collection_id: str, file_path: Path, base_path: Path) -> List[Dict[str, Any]]:
        """Splits file into digestible passages with line anchors."""
        passages: List[Dict[str, Any]] = []
        rel_path = str(file_path.relative_to(base_path)) if file_path.is_relative_to(base_path) else file_path.name

        # PDF handling with graceful fallback
        if file_path.suffix.lower() == ".pdf":
            text = self._extract_pdf_text(file_path)
            lines = text.splitlines()
        else:
            lines = file_path.read_text(encoding="utf-8", errors="replace").splitlines()

        if not lines:
            return passages

        chunk_size = 35  # lines per chunk
        overlap = 10     # lines overlap

        idx = 0
        while idx < len(lines):
            chunk_lines = lines[idx : idx + chunk_size]
            content = "\n".join(chunk_lines).strip()
            if content:
                passages.append({
                    "passage_id": f"p_{uuid.uuid4().hex[:10]}",
                    "collection_id": collection_id,
                    "file_path": str(file_path),
                    "relative_path": rel_path,
                    "line_start": idx + 1,
                    "line_end": min(len(lines), idx + chunk_size),
                    "content": content,
                    "metadata": {"line_count": len(chunk_lines)},
                })
            idx += (chunk_size - overlap)

        return passages

    def _extract_pdf_text(self, pdf_path: Path) -> str:
        """Extracts text from PDF if pypdf or PyMuPDF is installed, else returns notice."""
        try:
            import pypdf
            reader = pypdf.PdfReader(str(pdf_path))
            pages = [page.extract_text() or "" for page in reader.pages]
            return "\n".join(pages)
        except ImportError:
            pass

        try:
            import fitz  # PyMuPDF
            doc = fitz.open(str(pdf_path))
            pages = [page.get_text() or "" for page in doc]
            return "\n".join(pages)
        except ImportError:
            return f"[PDF Document {pdf_path.name} - Install 'pypdf' to extract full text]"

    def list_collections(self) -> List[SearchCollection]:
        """Lists all registered search collections."""
        raw = self.db.list_search_collections()
        return [SearchCollection(**r) for r in raw]

    def get_collection(self, name_or_id: str) -> Optional[SearchCollection]:
        """Retrieves collection by name or ID."""
        raw = self.db.get_search_collection(name_or_id)
        return SearchCollection(**raw) if raw else None

    def remove_collection(self, name_or_id: str) -> bool:
        """Removes a collection and all its passages."""
        col = self.get_collection(name_or_id)
        if not col:
            return False
        self.db.delete_search_collection(col.collection_id)
        return True

    def update_collection(self, name_or_id: str) -> SearchCollection:
        """Re-indexes an existing collection."""
        col = self.get_collection(name_or_id)
        if not col:
            raise ValueError(f"Collection '{name_or_id}' not found.")
        return self.add_collection(path=col.path, name=col.name)

    def find_passages(
        self,
        query: str,
        collection_name_or_id: Optional[str] = None,
        top_k: int = 5,
    ) -> List[SearchPassage]:
        """Retrieves top scoring passages using BM25 lexical ranking."""
        cols_to_search: List[SearchCollection] = []
        if collection_name_or_id:
            c = self.get_collection(collection_name_or_id)
            if c:
                cols_to_search.append(c)
        else:
            cols_to_search = self.list_collections()

        all_passages: List[Dict[str, Any]] = []
        for col in cols_to_search:
            all_passages.extend(self.db.get_search_passages(col.collection_id))

        if not all_passages:
            return []

        # BM25 Scoring
        q_tokens = _tokenize(query)
        if not q_tokens:
            return []

        doc_count = len(all_passages)
        avg_doc_len = sum(len(_tokenize(p["content"])) for p in all_passages) / max(1, doc_count)

        # Document frequencies for query terms
        df: Dict[str, int] = Counter()
        doc_tokens_list: List[List[str]] = []
        for p in all_passages:
            tokens = _tokenize(p["content"])
            doc_tokens_list.append(tokens)
            unique_terms = set(tokens)
            for t in q_tokens:
                if t in unique_terms:
                    df[t] += 1

        k1 = 1.5
        b = 0.75
        scored: List[Tuple[float, Dict[str, Any]]] = []

        for idx, p in enumerate(all_passages):
            tokens = doc_tokens_list[idx]
            doc_len = len(tokens)
            term_counts = Counter(tokens)
            score = 0.0

            for t in q_tokens:
                if t in term_counts:
                    tf = term_counts[t]
                    # IDF with standard smoothing
                    n_q = df[t]
                    idf = math.log((doc_count - n_q + 0.5) / (n_q + 0.5) + 1.0)
                    numerator = tf * (k1 + 1)
                    denominator = tf + k1 * (1 - b + b * (doc_len / max(1.0, avg_doc_len)))
                    score += idf * (numerator / max(0.001, denominator))

            if score > 0:
                scored.append((score, p))

        scored.sort(key=lambda x: x[0], reverse=True)
        results: List[SearchPassage] = []

        for score, p in scored[:top_k]:
            results.append(
                SearchPassage(
                    passage_id=p["passage_id"],
                    collection_id=p["collection_id"],
                    file_path=p["file_path"],
                    relative_path=p["relative_path"],
                    line_start=p["line_start"],
                    line_end=p["line_end"],
                    content=p["content"],
                    score=round(score, 3),
                )
            )

        return results

    def ask(
        self,
        query: str,
        collection_name_or_id: Optional[str] = None,
        model_alias: Optional[str] = None,
        gateway_url: str = "http://127.0.0.1:8080/v1",
        top_k: int = 4,
        mock_answer: Optional[str] = None,
    ) -> SearchResult:
        """Answers user question grounded in retrieved document passages with citations."""
        passages = self.find_passages(query, collection_name_or_id=collection_name_or_id, top_k=top_k)

        if not passages:
            return SearchResult(
                query=query,
                answer="No relevant documents or passages were found in the indexed collections to answer this question.",
                sources=[],
            )

        # Build grounded context
        context_blocks = []
        for i, p in enumerate(passages, 1):
            context_blocks.append(
                f"[{i}] File: {p.relative_path} (Lines {p.line_start}-{p.line_end}):\n{p.content}"
            )
        context_str = "\n\n".join(context_blocks)

        system_instruction = (
            "You are LocalPilot's Grounded Document Assistant. "
            "Answer the user's question accurately using ONLY the provided document excerpts. "
            "Always cite the source numbers (e.g. [1], [2]) and file names in your explanation."
        )
        user_prompt = f"Reference Passages:\n{context_str}\n\nQuestion: {query}"

        if mock_answer:
            answer = mock_answer
        else:
            from localpilot.application.task_service import TaskService
            task_svc = TaskService(self.db)
            answer = task_svc._call_inference(
                gateway_url=gateway_url,
                model_alias=model_alias or "default",
                system_prompt=system_instruction,
                user_prompt=user_prompt,
            )

        return SearchResult(
            query=query,
            answer=answer,
            sources=passages,
        )
