from __future__ import annotations
from typing import List, Dict
from pypdf import PdfReader
from functools import lru_cache

def build_page_index(pdf_path: str) -> List[str]:
    reader = PdfReader(pdf_path)
    pages: List[str] = []
    for i, p in enumerate(reader.pages):
        try:
            pages.append(p.extract_text() or "")
        except Exception:
            pages.append("")
    return pages

@lru_cache(maxsize=128)
def build_page_index_cached(pdf_path: str) -> List[str]:
    return build_page_index(pdf_path)

def search_pages(pages: List[str], keyword: str, limit: int=10) -> List[Dict]:
    res = []
    k = keyword.strip()
    if not k:
        return res
    for idx, text in enumerate(pages, start=1):
        tl = (text or "").lower()
        kl = k.lower()
        if kl in tl:
            pos = tl.find(kl)
            start = max(0, pos-50)
            end = min(len(text), pos+50)
            snippet = text[start:end]
            res.append({"page": idx, "snippet": snippet})
            if len(res) >= limit:
                break
    return res
