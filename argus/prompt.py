"""`argus ⟨◇⟩ ▸ ` — the prompt_toolkit chat prompt.

Magenta brackets, gold diamond, magenta arrow — PRD §21.2.
"""

from prompt_toolkit.formatted_text import FormattedText

from argus.theme import CYAN, DIM, GOLD, MAGENTA


def chat_prompt_fragments() -> FormattedText:
    """Returns a FormattedText for prompt_toolkit's PromptSession `message`."""
    return FormattedText(
        [
            (f"fg:{DIM}", "argus "),
            (f"fg:{MAGENTA} bold", "⟨"),
            (f"fg:{GOLD} bold", "◇"),
            (f"fg:{MAGENTA} bold", "⟩ "),
            (f"fg:{MAGENTA} bold", "▸ "),
        ]
    )


def telegram_prompt_label() -> FormattedText:
    """Used in `gateway start` to mark Telegram-source lines."""
    return FormattedText([(f"fg:{CYAN}", "tg "), (f"fg:{MAGENTA} bold", "⟨◇⟩ ")])
