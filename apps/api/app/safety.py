import re
from typing import Any

ORDER_ID = re.compile(r"\b(?:PDD|OD)\d{8,}\b", re.I)
PHONE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
TRACKING = re.compile(r"\b[A-Z]{2,8}\d{8,20}\b", re.I)
ADDRESS = re.compile(r"(?:\u6536\u8d27\u5730\u5740|\u5730\u5740)[:\uff1a]?[^\n]{4,80}")
MODELS = ("Pocket 4 Pro", "Pocket 4", "Pocket 3", "Pocket 2", "Pocket")


def mask_sensitive(text: str) -> str:
    value = ORDER_ID.sub("[\u8ba2\u5355\u53f7\u5df2\u8131\u654f]", text)
    value = PHONE.sub("[\u624b\u673a\u53f7\u5df2\u8131\u654f]", value)
    value = TRACKING.sub("[\u7269\u6d41\u5355\u53f7\u5df2\u8131\u654f]", value)
    return ADDRESS.sub("[\u5730\u5740\u5df2\u8131\u654f]", value)


def safe_observation(data: dict[str, Any]) -> dict[str, Any]:
    allowed = {"status", "item", "amount", "carrier", "latest_trace", "stock", "name", "price", "action"}
    return {key: mask_sensitive(str(value)) for key, value in data.items() if key in allowed}


def validate_customer_answer(question: str, answer: str) -> str | None:
    if not answer.strip() or PHONE.search(answer) or ADDRESS.search(answer):
        return "\u5f53\u524d\u6682\u65f6\u65e0\u6cd5\u751f\u6210\u5b89\u5168\u7b54\u590d\uff0c\u8bf7\u7a0d\u540e\u518d\u8bd5\u3002"
    requested = next((model for model in MODELS if model.lower() in question.lower()), None)
    if requested:
        mentioned = [model for model in MODELS if model.lower() in answer.lower()]
        if any(model != requested for model in mentioned):
            return "\u5f53\u524d\u6682\u65f6\u65e0\u6cd5\u786e\u8ba4\u9002\u7528\u578b\u53f7\uff0c\u8bf7\u63d0\u4f9b\u66f4\u660e\u786e\u7684\u4ea7\u54c1\u578b\u53f7\u3002"
    return None


def plain_customer_text(text: str) -> str:
    value = text.replace("**", "").replace("__", "").replace("`", "")
    value = re.sub(r"(?m)^\s*(?:[-*?]+|\d+[.)])\s*", "", value)
    value = re.sub(r"(?m)^\s*#{1,6}\s*", "", value)
    value = re.sub(r"\s*\[\d+\]", "", value)
    value = re.sub(r"\s*\n\s*", " ", value)
    return re.sub(r"\s{2,}", " ", value).strip()
