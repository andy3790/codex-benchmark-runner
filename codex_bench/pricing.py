"""Offline estimates using published Standard API text-token rates."""

API_COST_FIELD = "estimated_api_cost_usd"
API_COST_LABEL = "예상 API 비용 (USD)"
API_PRICING = {
    "source": "https://developers.openai.com/api/docs/pricing",
    "verified_on": "2026-11-05",
    "currency": "USD",
    "basis": "standard_short_context_text_tokens",
    "usd_per_million_tokens": {
        "gpt-6.1-sol": {"input": 2.00, "cached_input": 0.10, "output": 10.00},
        "gpt-6.1-terra": {"input": 1.00, "cached_input": 0.05, "output": 5.00},
        "gpt-6-luna": {"input": 0.10, "cached_input": 0.01, "output": 0.50},
    },
}


def estimate_api_cost_usd(row: dict, *, synthetic: bool = False) -> float | None:
    """Price the recorded token totals at the requested model's base rates."""
    version = str(row.get("codex_version") or "")
    if synthetic or version.startswith(("SIMULATED", "SYNTHETIC")):
        return None
    model = row.get("model")
    rates = API_PRICING["usd_per_million_tokens"].get(model) if isinstance(model, str) else None
    usage = row.get("usage") or {}
    if rates is None or not isinstance(usage, dict):
        return None
    input_tokens = usage.get("input_tokens")
    cached_tokens = usage.get("cached_input_tokens")
    output_tokens = usage.get("output_tokens")
    if any(type(value) is not int or value < 0 for value in (input_tokens, cached_tokens, output_tokens)):
        return None
    if cached_tokens > input_tokens:
        return None
    cost = ((input_tokens - cached_tokens) * rates["input"]
            + cached_tokens * rates["cached_input"]
            + output_tokens * rates["output"])
    return round(cost / 1_000_000, 8)


def format_api_cost(value: float | None) -> str:
    return "—" if value is None else f"{value:,.8f}".rstrip("0").rstrip(".")
