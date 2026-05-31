from core.config import settings


class TextChunker:
    """
    Splits text into overlapping chunks at natural language boundaries.
    Priority order: paragraph breaks → line breaks → sentences → words → characters.
    """

    def __init__(
        self,
        chunk_size: int | None = None,
        chunk_overlap: int | None = None,
    ) -> None:
        self.chunk_size = chunk_size or settings.CHUNK_SIZE
        self.chunk_overlap = chunk_overlap or settings.CHUNK_OVERLAP
        self.separators = ["\n\n", "\n", ". ", "! ", "? ", " ", ""]

    def chunk(self, text: str) -> list[dict]:
        """Returns a list of {"content": str} dicts, one per chunk."""
        raw_chunks = self._split(text, self.separators)
        return [{"content": c} for c in raw_chunks if c.strip()]

    def _split(self, text: str, separators: list[str]) -> list[str]:
        separator = separators[0]
        remaining = separators[1:]

        if separator == "":
            return self._hard_split(text)

        parts = text.split(separator)
        sep_len = len(separator)
        result: list[str] = []
        current_parts: list[str] = []
        current_len = 0

        def flush() -> None:
            nonlocal current_parts, current_len
            if current_parts:
                result.append(separator.join(current_parts))
                current_parts = []
                current_len = 0

        for part in parts:
            part_len = len(part)

            # A single part that can't fit in a chunk on its own must be broken
            # down further with the finer separators — otherwise it would emit
            # one oversized chunk. This recursive descent is what the chunker
            # was previously missing: it only ever split on separators[0], so a
            # long single paragraph (no "\n\n") came out as one giant chunk.
            if part_len > self.chunk_size:
                flush()
                result.extend(self._split(part, remaining))
                continue

            would_exceed = (
                current_len + part_len + (sep_len if current_parts else 0)
                > self.chunk_size
            )

            if would_exceed and current_parts:
                result.append(separator.join(current_parts))

                # Build overlap from tail of current_parts so adjacent chunks
                # share context (the property RAG retrieval depends on).
                overlap_parts: list[str] = []
                overlap_len = 0
                for p in reversed(current_parts):
                    if overlap_len + len(p) + sep_len > self.chunk_overlap:
                        break
                    overlap_parts.insert(0, p)
                    overlap_len += len(p) + sep_len

                current_parts = overlap_parts
                current_len = sum(len(p) for p in current_parts) + sep_len * max(0, len(current_parts) - 1)

            current_parts.append(part)
            current_len += part_len + (sep_len if len(current_parts) > 1 else 0)

        flush()
        return result

    def _hard_split(self, text: str) -> list[str]:
        """Last resort: split purely by character count."""
        chunks = []
        start = 0
        while start < len(text):
            end = min(start + self.chunk_size, len(text))
            chunks.append(text[start:end])
            start = end - self.chunk_overlap
            if start >= end:
                break
        return chunks
