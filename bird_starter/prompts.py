"""Pure generation template shared by the existing graph and strict baseline."""
PROMPT_VERSION = "baseline-v1"


def generation_prompt(question: str, evidence: str, schema: str) -> tuple[str, str]:
    return (
        "Write one read-only SQLite SELECT query that answers the question. "
        "Return only SQL, not explanations. Never use non-existent schema fields.",
        f"Schema:\n{schema}\nQuestion: {question}\nBusiness evidence: {evidence}",
    )
