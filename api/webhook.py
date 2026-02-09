import hashlib
import hmac

from api.config import WHOOP_CLIENT_SECRET


def verify_signature(body: bytes, signature: str, timestamp: str) -> bool:
    """Verify WHOOP webhook HMAC-SHA256 signature."""
    expected = hmac.new(
        WHOOP_CLIENT_SECRET.encode(),
        timestamp.encode() + body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)
