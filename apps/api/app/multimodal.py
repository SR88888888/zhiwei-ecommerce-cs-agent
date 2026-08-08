import hashlib
import re
import uuid
from datetime import datetime, timedelta, timezone


class AttachmentService:
    allowed = {"image/png", "image/jpeg", "image/webp"}
    max_size = 10 * 1024 * 1024

    def __init__(self) -> None:
        self.items: dict[str, dict] = {}

    async def process(self, attachment_id: str, user_id: str, session_id: str, content_type: str, data: bytes) -> None:
        item = self.items[attachment_id]
        try:
            text = self._ocr(data)
            text = self._redact(text)[:12000]
            if not text.strip():
                raise ValueError("empty_ocr_text")
            item.update(status="ready", ocr_text=text, entities=self._entities(text))
        except Exception as exc:
            item.update(status="failed", error=type(exc).__name__)

    def create(self, user_id: str, session_id: str, content_type: str, data: bytes) -> str:
        if content_type not in self.allowed or len(data) > self.max_size:
            raise ValueError("invalid_attachment")
        attachment_id = str(uuid.uuid4())
        self.items[attachment_id] = {"attachment_id": attachment_id, "user_id": user_id, "session_id": session_id, "status": "processing", "ocr_text": "", "entities": {}, "expires_at": (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat(), "sha256": hashlib.sha256(data).hexdigest()}
        return attachment_id

    def get(self, attachment_id: str, user_id: str, session_id: str) -> dict | None:
        item = self.items.get(attachment_id)
        if not item or item["user_id"] != user_id or item["session_id"] != session_id:
            return None
        if datetime.fromisoformat(item["expires_at"]) <= datetime.now(timezone.utc):
            self.items.pop(attachment_id, None)
            return None
        return item

    @staticmethod
    def _ocr(data: bytes) -> str:
        try:
            from paddleocr import PaddleOCR
            from PIL import Image
            import io
            image = Image.open(io.BytesIO(data))
            result = PaddleOCR(use_angle_cls=True, lang="ch", show_log=False).ocr(image, cls=True)
            return "\n".join(line[1][0] for block in result for line in block)
        except ImportError as exc:
            raise RuntimeError("paddleocr_not_installed") from exc

    @staticmethod
    def _redact(text: str) -> str:
        text = re.sub(r"1[3-9]\d{9}", "[PHONE_REDACTED]", text)
        return re.sub(r"\d{15,18}[0-9Xx]", "[ID_REDACTED]", text)

    @staticmethod
    def _entities(text: str) -> dict[str, str]:
        result = {}
        if match := re.search(r"\b(?:PDD|OD)\d{8,}\b", text, re.I): result["order_id"] = match.group(0).upper()
        if match := re.search(r"\b(?:PDD-?G|P)\d{3,}\b", text, re.I): result["product_id"] = match.group(0).upper()
        return result


attachment_service = AttachmentService()