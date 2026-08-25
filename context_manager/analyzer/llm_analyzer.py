"""AI Context Analyzer: turns an ExtractedDocument + folder path into a
coarse index (summary + per-location summaries).

The goal is explicitly NOT full understanding - just enough of a "map" that
an Agent can later decide where to read_range() / read_resource() next.
Folder names are passed in as context (e.g. "Gen9/GDR") since the folder
structure itself is meaningful, but the model is never asked to fully parse
the file.
"""
from __future__ import annotations

import json
import os

from ..models import AnalysisResult, ExtractedDocument

ANALYSIS_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "locations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "location": {"type": "string"},
                    "summary": {"type": "string"},
                },
                "required": ["location", "summary"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["summary", "locations"],
    "additionalProperties": False,
}


def build_prompt(path: str, parent_folders: list[str], extracted: ExtractedDocument) -> str:
    folder_context = "/".join(parent_folders) if parent_folders else "(root)"
    lines = [
        "あなたは社内資料の索引化を行うアシスタントです。",
        "以下のファイルについて、後でAgentが「どこに何があるか」を判断できる程度の",
        "粗い案内図を作成してください。内容の完全な理解や正確な要約は不要です。",
        "",
        f"ファイルパス: {path}",
        f"フォルダ階層: {folder_context}",
        f"ファイル種別: {extracted.kind}",
        "",
        "抽出内容（一部のみ・全文ではありません）:",
    ]
    for section in extracted.sections:
        lines.append(f"--- {section.location} ---")
        lines.append(section.text)
    if extracted.note:
        lines.append(f"(注: {extracted.note})")
    lines += [
        "",
        "summary: このファイル全体が何かを1〜2文で。",
        "locations: 上記の各Sheet/Slide/Section等について、そこに何がありそうかを短く。",
    ]
    return "\n".join(lines)


class LLMAnalyzer:
    """Calls the Claude API to produce the coarse index.

    Defaults to claude-opus-5. Override via the CONTEXT_MANAGER_MODEL env
    var or the `model` argument if you want a cheaper model for high-volume
    indexing runs - that's a deliberate cost/quality call the caller makes,
    not something this class decides on its own.
    """

    def __init__(self, model: str | None = None):
        self.model = model or os.environ.get("CONTEXT_MANAGER_MODEL", "claude-opus-5")
        self._client = None

    def _client_or_create(self):
        if self._client is None:
            import anthropic

            self._client = anthropic.Anthropic()
        return self._client

    def analyze(self, path: str, parent_folders: list[str], extracted: ExtractedDocument) -> AnalysisResult:
        client = self._client_or_create()
        prompt = build_prompt(path, parent_folders, extracted)
        response = client.messages.create(
            model=self.model,
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}],
            output_config={"format": {"type": "json_schema", "schema": ANALYSIS_SCHEMA}},
        )
        text = next(b.text for b in response.content if b.type == "text")
        data = json.loads(text)
        return AnalysisResult(summary=data["summary"], locations=data["locations"])


def _extract_json_object(text: str) -> dict:
    """Local OpenAI-compatible servers don't all honor response_format as
    strictly as the Claude API's output_config - some wrap the JSON in
    markdown fences or add a stray preamble. Fall back to slicing out the
    outermost {...} before giving up."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return json.loads(text[start : end + 1])
    raise ValueError(f"model did not return valid JSON: {text[:200]!r}")


class OpenAICompatAnalyzer:
    """Calls a local/self-hosted OpenAI-compatible chat completions endpoint
    (Ollama, LM Studio, vLLM, llama.cpp server, ...) instead of the Claude
    API. Useful when file excerpts shouldn't leave the machine/network at
    all. Requires the `openai` package (`uv sync --extra local-llm`).

    Configure via env vars or constructor args:
      CONTEXT_MANAGER_OPENAI_BASE_URL (default: http://localhost:11434/v1 - Ollama's default)
      CONTEXT_MANAGER_OPENAI_API_KEY  (default: "not-needed" - most local servers ignore it)
      CONTEXT_MANAGER_OPENAI_MODEL    (default: "llama3" - must match a model the server has loaded)
    """

    def __init__(self, base_url: str | None = None, api_key: str | None = None, model: str | None = None):
        self.base_url = base_url or os.environ.get("CONTEXT_MANAGER_OPENAI_BASE_URL", "http://localhost:11434/v1")
        self.api_key = api_key or os.environ.get("CONTEXT_MANAGER_OPENAI_API_KEY", "not-needed")
        self.model = model or os.environ.get("CONTEXT_MANAGER_OPENAI_MODEL", "llama3")
        self._client = None

    def _client_or_create(self):
        if self._client is None:
            try:
                import openai
            except ImportError as e:
                raise RuntimeError(
                    "the `openai` package is required for OpenAICompatAnalyzer - "
                    "install it with `uv sync --extra local-llm`"
                ) from e
            self._client = openai.OpenAI(base_url=self.base_url, api_key=self.api_key)
        return self._client

    def analyze(self, path: str, parent_folders: list[str], extracted: ExtractedDocument) -> AnalysisResult:
        client = self._client_or_create()
        prompt = build_prompt(path, parent_folders, extracted)
        schema_hint = (
            "必ず次のJSON Schemaに一致するJSONオブジェクトのみを出力してください"
            "（説明文やコードフェンスは付けないこと）:\n" + json.dumps(ANALYSIS_SCHEMA, ensure_ascii=False)
        )
        response = client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": schema_hint},
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
        )
        text = response.choices[0].message.content or ""
        data = _extract_json_object(text)
        return AnalysisResult(summary=data.get("summary", ""), locations=data.get("locations", []))


class HeuristicAnalyzer:
    """Offline fallback used when no Anthropic credentials are configured.

    Keeps the pipeline runnable end-to-end without API access (local
    testing, CI, no key set yet). Swap in LLMAnalyzer (the default once
    credentials exist) for real indexing quality.
    """

    def analyze(self, path: str, parent_folders: list[str], extracted: ExtractedDocument) -> AnalysisResult:
        folder_context = "/".join(parent_folders) if parent_folders else "(root)"
        preview = extracted.sections[0].text[:120].strip() if extracted.sections else ""
        summary = f"{folder_context} 配下の {extracted.kind} ファイル。内容の一部: {preview}".strip()
        locations = [
            {"location": s.location, "summary": (s.text[:150].strip() or "(空)")} for s in extracted.sections
        ]
        return AnalysisResult(summary=summary, locations=locations)


BACKENDS = {
    "anthropic": LLMAnalyzer,
    "openai_compat": OpenAICompatAnalyzer,
    "heuristic": HeuristicAnalyzer,
}


def get_analyzer(force_heuristic: bool = False, backend: str | None = None):
    """Pick an analyzer backend.

    backend: explicit choice - "anthropic" | "openai_compat" | "heuristic".
    Falls back to the CONTEXT_MANAGER_ANALYZER env var, then auto-detects:
    Anthropic credentials present -> LLMAnalyzer; else
    CONTEXT_MANAGER_OPENAI_BASE_URL set -> OpenAICompatAnalyzer (local LLM);
    else HeuristicAnalyzer.
    """
    if force_heuristic:
        return HeuristicAnalyzer()

    backend = backend or os.environ.get("CONTEXT_MANAGER_ANALYZER")
    if backend:
        cls = BACKENDS.get(backend)
        if cls is None:
            raise ValueError(f"unknown analyzer backend: {backend!r} (choices: {', '.join(BACKENDS)})")
        return cls()

    has_anthropic_credentials = bool(
        os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
    )
    if has_anthropic_credentials:
        return LLMAnalyzer()
    if os.environ.get("CONTEXT_MANAGER_OPENAI_BASE_URL"):
        return OpenAICompatAnalyzer()
    return HeuristicAnalyzer()
