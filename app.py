"""로컬 TTS 웹앱 — edge-tts 기반.

실행:  ./venv/bin/python app.py
접속:  http://localhost:8765
"""

import io
import os
import re

import truststore

truststore.inject_into_ssl()  # 사내 SSL 검사 프록시 환경에서 시스템 인증서 사용

import edge_tts
from aiohttp import web

# 기본은 내 컴퓨터 전용. 같은 네트워크에 공유하려면 TTS_HOST=0.0.0.0 으로 실행
HOST = os.environ.get("TTS_HOST", "127.0.0.1")
PORT = int(os.environ.get("TTS_PORT", "8765"))

# 자주 쓰는 음성은 위쪽에 고정 노출 (Multilingual 음성은 한국어도 읽을 수 있음)
FEATURED = [
    ("ko-KR-SunHiNeural", "선히 (한국어, 여성)"),
    ("ko-KR-InJoonNeural", "인준 (한국어, 남성)"),
    ("ko-KR-HyunsuMultilingualNeural", "현수 (한국어, 남성)"),
    ("en-US-AvaMultilingualNeural", "Ava (다국어, 여성 — 한국어 가능)"),
    ("en-US-EmmaMultilingualNeural", "Emma (다국어, 여성 — 한국어 가능)"),
    ("en-US-AndrewMultilingualNeural", "Andrew (다국어, 남성 — 한국어 가능)"),
    ("en-US-BrianMultilingualNeural", "Brian (다국어, 남성 — 한국어 가능)"),
    ("de-DE-SeraphinaMultilingualNeural", "Seraphina (다국어, 여성 — 한국어 가능)"),
    ("en-US-JennyNeural", "Jenny (영어, 여성)"),
    ("en-US-GuyNeural", "Guy (영어, 남성)"),
    ("ja-JP-NanamiNeural", "Nanami (일본어, 여성)"),
]

_voices_cache: list[dict] | None = None


async def list_voices(request: web.Request) -> web.Response:
    global _voices_cache
    if _voices_cache is None:
        raw = await edge_tts.list_voices()
        _voices_cache = [
            {"name": v["ShortName"], "gender": v["Gender"], "locale": v["Locale"]}
            for v in sorted(raw, key=lambda v: v["ShortName"])
        ]
    return web.json_response({"featured": [{"name": n, "label": l} for n, l in FEATURED],
                              "all": _voices_cache})


def _fmt_signed_pct(value: int) -> str:
    return f"{value:+d}%"


def _parse_options(body: dict) -> tuple[str, int, int]:
    voice = body.get("voice") or "ko-KR-SunHiNeural"
    if not re.fullmatch(r"[A-Za-z]{2,3}-[A-Za-z]{2,4}-[A-Za-z0-9]+", voice):
        raise web.HTTPBadRequest(text="voice 형식이 올바르지 않습니다")
    rate = max(-50, min(100, int(body.get("rate", 0))))
    pitch = max(-50, min(50, int(body.get("pitch", 0))))
    return voice, rate, pitch


async def _synthesize(text: str, voice: str, rate: int, pitch: int) -> bytes:
    communicate = edge_tts.Communicate(
        text, voice,
        rate=_fmt_signed_pct(rate),
        pitch=f"{pitch:+d}Hz",
    )
    buf = io.BytesIO()
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            buf.write(chunk["data"])
    data = buf.getvalue()
    if not data:
        raise web.HTTPBadGateway(text="음성 서버에서 오디오를 받지 못했습니다")
    return data


def _audio_response(data: bytes) -> web.Response:
    return web.Response(
        body=data,
        content_type="audio/mpeg",
        headers={"Cache-Control": "no-store"},
    )


async def tts(request: web.Request) -> web.StreamResponse:
    try:
        body = await request.json()
    except Exception:
        raise web.HTTPBadRequest(text="JSON 본문이 필요합니다")

    text = (body.get("text") or "").strip()
    if not text:
        raise web.HTTPBadRequest(text="text가 비어 있습니다")
    if len(text) > 20000:
        raise web.HTTPBadRequest(text="텍스트가 너무 깁니다 (20,000자 제한)")

    voice, rate, pitch = _parse_options(body)
    return _audio_response(await _synthesize(text, voice, rate, pitch))


async def tts_batch(request: web.Request) -> web.StreamResponse:
    """여러 줄을 순서대로 합성해 하나의 MP3로 이어붙여 반환."""
    try:
        body = await request.json()
    except Exception:
        raise web.HTTPBadRequest(text="JSON 본문이 필요합니다")

    texts = [t.strip() for t in body.get("texts") or [] if isinstance(t, str) and t.strip()]
    if not texts:
        raise web.HTTPBadRequest(text="texts가 비어 있습니다")
    if len(texts) > 50:
        raise web.HTTPBadRequest(text="줄이 너무 많습니다 (50줄 제한)")
    if sum(len(t) for t in texts) > 20000:
        raise web.HTTPBadRequest(text="텍스트가 너무 깁니다 (합계 20,000자 제한)")

    voice, rate, pitch = _parse_options(body)

    # 같은 인코딩 설정의 MP3 프레임 스트림이라 바이트 단위 이어붙이기가 가능
    parts = [await _synthesize(t, voice, rate, pitch) for t in texts]
    return _audio_response(b"".join(parts))


BASE_DIR = os.path.dirname(os.path.abspath(__file__))


async def index(request: web.Request) -> web.FileResponse:
    return web.FileResponse(os.path.join(BASE_DIR, "index.html"))


def main() -> None:
    app = web.Application()
    app.router.add_get("/", index)
    app.router.add_get("/voices", list_voices)
    app.router.add_post("/tts", tts)
    app.router.add_post("/tts_batch", tts_batch)
    print(f"TTS 서버 시작: http://{HOST}:{PORT}")
    web.run_app(app, host=HOST, port=PORT, print=None)


if __name__ == "__main__":
    main()
