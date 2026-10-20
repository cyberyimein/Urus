"""Keep share-class dots distinct from Moomoo market prefixes."""

MARKET_PREFIXES = frozenset({"US", "HK", "SH", "SZ", "SG", "JP", "AU", "CA", "MY"})


def normalize_symbol(value: object) -> str:
    text = str(value or "").strip().upper()
    prefix, separator, ticker = text.partition(".")
    return ticker if separator and prefix in MARKET_PREFIXES else text


def normalize_quote_code(value: object) -> str:
    text = str(value or "").strip().upper()
    if not text:
        raise ValueError("行情代码不能为空")
    prefix, separator, _ticker = text.partition(".")
    return text if separator and prefix in MARKET_PREFIXES else f"US.{text}"
