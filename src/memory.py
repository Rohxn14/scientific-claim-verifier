from generation import generate, fast_model_choice

def contextualize_question(history: list, question: str) -> str:
    """Rewrite a follow-up question into a standalone one using recent
    conversation history. If there's no history, or the rewrite fails, fall
    back to the original question unchanged - this step should never be
    allowed to block answering."""
    if not history:
        return question

    history_text = "\n".join(
        f"{'User' if m['role'] == 'user' else 'Assistant'}: {m['text']}"
        for m in history[-6:]
    )

    prompt = f"""Given this conversation history and a follow-up question, rewrite the
follow-up as a standalone question that makes sense without the history.
If the follow-up is already standalone, return it unchanged.
Return ONLY the rewritten question, nothing else.

History:
{history_text}

Follow-up question: {question}

Standalone question:"""

    try:
        rewritten, _ = generate(prompt, model_choice=fast_model_choice())
        rewritten = rewritten.strip().strip('"')
        return rewritten if rewritten else question
    except Exception:
        return question
