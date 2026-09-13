from spotify_backend.main import _best_audio_from_ytdlp


def test_best_audio_from_ytdlp_prefers_streamable_audio():
    payload = {
        "title": "Yellow",
        "uploader": "Coldplay",
        "thumbnail": "https://example.com/thumb.jpg",
        "formats": [
            {"format_id": "audio-low", "url": "https://example.com/low.mp3", "vcodec": "none", "audio_ext": "mp3", "quality": 1},
            {"format_id": "audio-high", "url": "https://example.com/high.m4a", "vcodec": "none", "audio_ext": "m4a", "quality": 5},
        ],
    }

    result = _best_audio_from_ytdlp(payload)

    assert result == "https://example.com/high.m4a"
