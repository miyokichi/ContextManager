# Context Manager (MVP)

既存の業務フォルダを登録すると、原本を一切移動・変更せずに「どこに、何の情報があるか」を
自動で索引化し、Agentが必要な箇所だけを後から読みにいけるようにするツールです。

- 原本は既存フォルダにそのまま置かれ、Context Managerは常にread-only
- 人間がやるのはフォルダ登録だけ（タグ付け・メタデータ入力は不要）
- 初回解析では粗いIndex（どこに何がありそうか）だけを作る。全文Knowledge化はしない
- 詳細内容はAgentが必要になったときだけ、原本から直接読む

## パイプライン

登録フォルダ (`registry.py`) → 走査・差分検出 (`scanner.py`) → 軽量抽出
(`extractors/`) → AI Context Analyzer (`analyzer/llm_analyzer.py`) → SQLite
Catalog (`catalog.py`) → 検索 (`search.py`) → 原本の必要箇所だけ読む
(`reader.py`)。

## セットアップ

```bash
uv sync
cp config.example.yaml config.yaml   # 中身を自分のフォルダに合わせて編集
```

LLM解析のバックエンドは3種類あり、`scan` 実行時に自動判定（後述の優先順位）
または `--llm-backend` / `CONTEXT_MANAGER_ANALYZER` で明示的に選べます。

| バックエンド | 説明 |
| --- | --- |
| `anthropic`（既定） | Claude APIで解析。`ANTHROPIC_API_KEY` 環境変数、または `ant auth login` が必要。モデルは既定 `claude-opus-5`、`CONTEXT_MANAGER_MODEL` で変更可（大量ファイルの索引化でコストを抑えたい場合など）。 |
| `openai_compat` | OpenAI互換API（Ollama / LM Studio / vLLM / llama.cpp serverなど）で解析。ファイル抜粋を外部に一切送りたくない場合に。`uv sync --extra local-llm` で `openai` パッケージが必要。 |
| `heuristic` | APIを呼ばず、抽出結果の先頭だけを機械的に要約するオフライン解析。動作確認・APIキー未設定時のフォールバック用。 |

自動判定の優先順位: `ANTHROPIC_API_KEY`/`ANTHROPIC_AUTH_TOKEN` が設定されていれば
`anthropic` → 未設定でも `CONTEXT_MANAGER_OPENAI_BASE_URL` が設定されていれば
`openai_compat` → どちらも無ければ `heuristic`。

`openai_compat` の設定は環境変数で行います:

```bash
export CONTEXT_MANAGER_OPENAI_BASE_URL="http://localhost:11434/v1"  # Ollamaの既定
export CONTEXT_MANAGER_OPENAI_API_KEY="not-needed"                  # 多くのローカルサーバは未使用
export CONTEXT_MANAGER_OPENAI_MODEL="llama3.1"                      # サーバ側でロード済みのモデル名
uv run main.py scan --llm-backend openai_compat
```

## 使い方

```bash
# フォルダを登録
uv run main.py registry add "D:/work/Gen9"
uv run main.py registry list

# 走査 + 差分検出 + 新規/更新ファイルの索引化
uv run main.py scan
uv run main.py scan --no-llm                       # APIを呼ばずオフライン解析のみで確認したい場合
uv run main.py scan --llm-backend openai_compat    # ローカルLLMで解析
uv run main.py stats

# Agent的な使い方: 検索 → 構造確認 → 必要箇所だけ読む
uv run main.py search "WL pitch typical"
uv run main.py structure "D:/work/Gen9/GDR/GDR_assumption.xlsx"
uv run main.py read "D:/work/Gen9/GDR/GDR_assumption.xlsx" --location WL_pitch
uv run main.py sheets "D:/work/Gen9/GDR/GDR_assumption.xlsx"
uv run main.py range "D:/work/Gen9/GDR/GDR_assumption.xlsx" WL_pitch A1:F20
uv run main.py formula "D:/work/Gen9/GDR/GDR_assumption.xlsx" WL_pitch C5
```

対応ファイル形式: xlsx / xlsm, pptx, pdf, docx, csv, txt, md。

## 設計メモ / MVPで意図的にやっていないこと

- 本格的なKnowledge Graph、自動Glossary、会議Decision抽出、メール連携、
  Version履歴推定、Embedding/Reranker最適化、OCR高度処理、Excel全体の完全
  構造解析、自動ファイル編集、自動タグ付けUIは対象外（`Resource` /
  `ResourceLocation` に列を足すだけで拡張できるようにテーブル設計は分離済み）。
- 検索はSQLite FTS5（bm25）。Embeddingは必須にしていない。
- ハッシュは常に全文SHA-256（サイズ+mtimeの高速パスは未実装）。大量・大容量
  ファイルでの`scan`を高速化したくなったら`scanner.py`にショートカットを足す。
