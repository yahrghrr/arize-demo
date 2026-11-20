"""A tiny in-memory knowledge base and keyword retriever for the RAG endpoint."""

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Doc:
    id: str
    title: str
    text: str


DOCS: list[Doc] = [
    Doc("kb-001", "Return policy",
        "Acme Outfitters accepts returns within 30 days of delivery. Items must be unused and in "
        "their original packaging. Refunds go back to the original payment method within 5 business days."),
    Doc("kb-002", "Shipping times",
        "Standard shipping takes 3 to 5 business days. Express shipping takes 1 to 2 business days and "
        "costs $15. Orders over $75 ship free with standard shipping."),
    Doc("kb-003", "International orders",
        "Acme Outfitters ships to Canada, the UK, the EU and Australia. International orders take 7 to 14 "
        "business days. Customs duties are paid by the customer on delivery."),
    Doc("kb-004", "Warranty",
        "All tents and backpacks carry a 2-year warranty against manufacturing defects. Damage from misuse "
        "or normal wear is not covered. Start a claim with the order number and a photo of the defect."),
    Doc("kb-005", "Loyalty program",
        "Acme Trail Club members earn 1 point per dollar spent. 100 points convert to a $5 voucher. "
        "Members also get early access to seasonal sales."),
    Doc("kb-006", "Store hours",
        "The flagship store in Denver is open Monday to Saturday 9am to 8pm and Sunday 10am to 6pm. "
        "Customer support chat is available 24/7."),
    Doc("kb-007", "Tent care",
        "Dry your tent completely before storing it to prevent mildew. Clean it with lukewarm water and a "
        "mild soap; never machine-wash or use a dryer. Re-apply waterproofing spray once a season."),
]

_STOP = {"the", "a", "an", "is", "are", "do", "does", "how", "what", "when", "can", "i", "my", "to",
         "of", "for", "and", "in", "on", "you", "your", "it", "with", "long", "much"}


def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", text.lower()) if t not in _STOP}


def search(query: str, top_k: int = 3) -> list[tuple[Doc, float]]:
    """Score documents by keyword overlap and return the best matches (score > 0)."""
    q = _tokens(query)
    if not q:
        return []
    scored = []
    for doc in DOCS:
        overlap = len(q & _tokens(doc.title + " " + doc.text))
        if overlap:
            scored.append((doc, round(overlap / len(q), 3)))
    scored.sort(key=lambda pair: pair[1], reverse=True)
    return scored[:top_k]
