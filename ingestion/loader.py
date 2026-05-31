from pathlib import Path

import fitz  # PyMuPDF
from docx import Document as DocxDocument


_CODE_EXTENSIONS = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".go", ".rs", ".rb", ".php",
    ".cs", ".cpp", ".c", ".h", ".hpp", ".kt", ".swift", ".scala", ".sql",
    ".sh", ".bash", ".zsh", ".yaml", ".yml", ".json", ".jsonl", ".ndjson",
    ".toml", ".ini", ".cfg", ".md", ".txt", ".csv", ".tsv", ".html", ".css",
    ".xml", ".dockerfile", ".log", ".rst", ".tex", ".env",
}


class DocumentLoader:
    """Loads raw text from PDFs, DOCX files, and plain-text/code files."""

    def load(self, file_path: str, mime_type: str) -> str:
        suffix = Path(file_path).suffix.lower()

        if mime_type == "application/pdf" or suffix == ".pdf":
            return self._load_pdf(file_path)
        if (
            mime_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            or suffix == ".docx"
        ):
            return self._load_docx(file_path)
        if mime_type.startswith("text/") or suffix in _CODE_EXTENSIONS or not suffix:
            return self._load_text(file_path)

        return self._load_text(file_path)

    def _load_pdf(self, file_path: str) -> str:
        text_parts = []
        try:
            with fitz.open(file_path) as doc:
                for page_number, page in enumerate(doc, 1):
                    text = page.get_text()
                    if text.strip():
                        text_parts.append(f"[Page {page_number}]\n{text}")
        except Exception as e:
            raise RuntimeError(f"Failed to load PDF {file_path}: {e}") from e
        return "\n\n".join(text_parts)

    def _load_docx(self, file_path: str) -> str:
        try:
            doc = DocxDocument(file_path)
            parts = [paragraph.text for paragraph in doc.paragraphs if paragraph.text.strip()]
            return "\n".join(parts)
        except Exception as e:
            raise RuntimeError(f"Failed to load DOCX {file_path}: {e}") from e

    def _load_text(self, file_path: str) -> str:
        try:
            with open(file_path, encoding="utf-8", errors="replace") as f:
                return f.read()
        except Exception as e:
            raise RuntimeError(f"Failed to load file {file_path}: {e}") from e
