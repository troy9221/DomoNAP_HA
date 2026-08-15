"""Подготовка фото для CreateFace: iPhone отдаёт HEIC/.jpeg, API ждёт JPEG .jpg."""

from __future__ import annotations

import io
import json
import logging
from typing import Any

_LOGGER = logging.getLogger(__name__)

FACE_JPEG_MAX_EDGE = 1280
FACE_JPEG_QUALITY = 85
_JPEG_CONTENT_TYPES = {"image/jpeg", "image/jpg"}
_PNG_CONTENT_TYPES = {"image/png"}
_WEBP_CONTENT_TYPES = {"image/webp"}
_HEIC_CONTENT_TYPES = {"image/heic", "image/heif"}
_HEIC_BRANDS = (b"heic", b"heif", b"heix", b"hevc", b"mif1", b"msf1")


class FaceImageError(Exception):
    """Фото нельзя привести к JPEG, который примет DomoNAP."""


def sniff_image_type(data: bytes) -> str | None:
    """Определить тип по сигнатуре, а не по имени файла с iPhone."""
    if not data or len(data) < 12:
        return None
    if data[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if data[4:8] == b"ftyp":
        brand = data[8:16].lower()
        if any(token in brand for token in _HEIC_BRANDS):
            return "heic"
    return None


def normalize_face_meta(
    filename: str | None,
    content_type: str | None = None,
) -> tuple[str, str]:
    """Имя и Content-Type для multipart. .jpeg с iOS становится .jpg."""
    name = (filename or "face.jpg").split("?")[0].rsplit("/", 1)[-1] or "face.jpg"
    ctype = (content_type or "").split(";")[0].strip().lower()
    lower = name.lower()
    sniffed_from_name: str | None = None
    if lower.endswith(".png"):
        sniffed_from_name = "png"
    elif lower.endswith(".webp"):
        sniffed_from_name = "webp"
    elif lower.endswith(".heic") or lower.endswith(".heif"):
        sniffed_from_name = "heic"
    elif lower.endswith((".jpg", ".jpeg", ".jpe")):
        sniffed_from_name = "jpeg"

    if ctype in _JPEG_CONTENT_TYPES or sniffed_from_name == "jpeg":
        if not lower.endswith(".jpg"):
            stem = name.rsplit(".", 1)[0] if "." in name else name
            name = f"{stem or 'face'}.jpg"
        return name, "image/jpeg"
    if ctype in _PNG_CONTENT_TYPES or sniffed_from_name == "png":
        if not lower.endswith(".png"):
            name = f"{name}.png" if "." not in name else name
        return name, "image/png"
    if ctype in _WEBP_CONTENT_TYPES or sniffed_from_name == "webp":
        if not lower.endswith(".webp"):
            name = f"{name}.webp" if "." not in name else name
        return name, "image/webp"
    if ctype in _HEIC_CONTENT_TYPES or sniffed_from_name == "heic":
        if not (lower.endswith(".heic") or lower.endswith(".heif")):
            name = f"{name}.heic" if "." not in name else name
        return name, "image/heic"
    if "." not in name:
        name = f"{name}.jpg"
    return name, "image/jpeg"


def face_name_from_filename(filename: str | None) -> str:
    """Подпись лица для API: IMG_2940.jpeg → IMG_2940."""
    name = (filename or "Avatar").split("?")[0].rsplit("/", 1)[-1]
    if "." in name:
        name = name.rsplit(".", 1)[0]
    name = (name or "").strip() or "Avatar"
    if name.lower() in ("face", "image", "img", "photo", "avatar"):
        return "Avatar"
    return name[:64]


def prepare_face_jpeg(
    image_bytes: bytes,
    filename: str | None = None,
    content_type: str | None = None,
) -> tuple[bytes, str, str]:
    """Вернуть baseline JPEG, имя .jpg и image/jpeg — как ждёт CreateFace."""
    if not image_bytes:
        raise FaceImageError("Пустой файл фото")

    kind = sniff_image_type(image_bytes)
    name, ctype = normalize_face_meta(filename, content_type)
    if kind == "heic" or ctype == "image/heic":
        converted = _pillow_to_jpeg(image_bytes, allow_heic=True)
        if converted is None:
            raise FaceImageError(
                "Формат HEIC с iPhone сервер DomoNAP не принимает. "
                "Сделайте снимок кнопкой «Сфотографировать» или сохраните фото как JPEG."
            )
        return converted, _jpg_name(name), "image/jpeg"

    converted = _pillow_to_jpeg(image_bytes, allow_heic=False)
    if converted is not None:
        return converted, _jpg_name(name), "image/jpeg"

    if kind == "jpeg" or (kind is None and ctype == "image/jpeg"):
        return image_bytes, _jpg_name(name), "image/jpeg"

    if kind in ("png", "webp"):
        raise FaceImageError(
            "Не удалось перекодировать фото в JPEG. Сохраните снимок как JPG и повторите."
        )
    raise FaceImageError(
        "Некорректный файл фото. Нужен JPEG (не HEIC и не Live Photo)."
    )


def format_face_upload_error(filename: str, payload: Any) -> str:
    """Человекочитаемая ошибка загрузки вместо сырого dict."""
    text = _error_text(payload)
    lower = text.lower()
    if (
        "некорректный запрос" in lower
        or "bad_request" in lower
        or "bad request" in lower
        or "http 400" in lower
    ):
        return (
            f"{filename}: сервер отклонил фото (нужен обычный JPEG, не HEIC с iPhone). "
            "Попробуйте «Сфотографировать» или другой снимок."
        )
    return f"{filename}: {text}" if text else f"{filename}: ошибка загрузки"


def _jpg_name(filename: str) -> str:
    stem = (filename or "face").rsplit(".", 1)[0] or "face"
    return f"{stem}.jpg"


def _pillow_to_jpeg(image_bytes: bytes, *, allow_heic: bool) -> bytes | None:
    try:
        from PIL import Image, ImageOps
    except ImportError:
        _LOGGER.debug("Pillow is not available, skip face JPEG re-encode")
        return None

    if allow_heic:
        try:
            import pillow_heif

            pillow_heif.register_heif_opener()
        except Exception:
            _LOGGER.debug("pillow-heif is not available, cannot decode HEIC")

    try:
        image = Image.open(io.BytesIO(image_bytes))
        image = ImageOps.exif_transpose(image) or image
        if image.mode in ("RGBA", "LA", "P"):
            rgba = image.convert("RGBA")
            background = Image.new("RGB", rgba.size, (255, 255, 255))
            background.paste(rgba, mask=rgba.split()[-1])
            image = background
        elif image.mode != "RGB":
            image = image.convert("RGB")
        width, height = image.size
        max_edge = max(width, height)
        if max_edge > FACE_JPEG_MAX_EDGE:
            scale = FACE_JPEG_MAX_EDGE / float(max_edge)
            image = image.resize(
                (max(1, int(width * scale)), max(1, int(height * scale))),
                getattr(getattr(Image, "Resampling", Image), "LANCZOS", Image.LANCZOS),
            )
        out = io.BytesIO()
        image.save(
            out,
            format="JPEG",
            quality=FACE_JPEG_QUALITY,
            optimize=True,
            progressive=False,
        )
        data = out.getvalue()
        return data or None
    except Exception:
        _LOGGER.debug("Failed to re-encode face image as JPEG", exc_info=True)
        return None


def _error_text(payload: Any) -> str:
    if isinstance(payload, str):
        return payload
    if not isinstance(payload, dict):
        return str(payload or "")
    body = payload.get("body")
    parsed: Any = None
    if isinstance(body, str) and body:
        try:
            parsed = json.loads(body)
        except Exception:
            parsed = None
    for source in (parsed, payload):
        if not isinstance(source, dict):
            continue
        for key in ("errorText", "message", "error", "statusText"):
            value = source.get(key)
            if value not in (None, "", False) and not isinstance(value, dict):
                text = str(value).strip()
                if text and text.lower() not in ("http 400", "bad_request"):
                    return text
    if isinstance(body, str) and body.strip():
        return body.strip()[:300]
    return str(payload)[:300]
