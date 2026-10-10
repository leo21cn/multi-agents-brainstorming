def split_text(text: str, size: int = 500, overlap: int = 50) -> list[str]:
    if size <= overlap:
        raise ValueError("size must be greater than overlap")
    parts: list[str] = []
    i = 0
    while i < len(text):
        parts.append(text[i : i + size])
        i += size - overlap
    return [p for p in parts if p.strip()]
