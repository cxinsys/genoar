"""Pure parsing helpers for the GENOAR crawler.

Kept free of Selenium and other heavy imports so the logic stays unit-testable
in isolation.
"""


def parse_grep_count(output: str) -> int:
    r"""Parse a match count from ``grep -c`` output.

    ``grep -c`` exits non-zero on zero matches, and the keyword command used to
    append ``|| echo 0``, which produced ``"0\n0"`` and broke ``int()``. Taking
    the first integer token tolerates multi-line or empty output.

    Args:
        output: Raw stdout captured from the grep pipeline.

    Returns:
        The first integer found, or ``0`` when there is none.
    """
    for token in output.split():
        if token.isdigit():
            return int(token)
    return 0
