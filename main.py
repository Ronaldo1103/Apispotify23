from fastapi import FastAPI
from spotapi import Song

app = FastAPI(title="Spotify Public API", version="1.0.0")


def _safe_artist_names(data):
    artists = data.get("artists", {}).get("items", [])
    return [artist.get("profile", {}).get("name") for artist in artists if artist.get("profile", {}).get("name")]


def _safe_cover_urls(data):
    images = []
    cover_art = data.get("coverArt") or {}
    for source in cover_art.get("sources", []) or []:
        url = source.get("url")
        if url:
            images.append(url)

    if not images:
        album = data.get("albumOfTrack") or {}
        for source in album.get("coverArt", {}).get("sources", []) or []:
            url = source.get("url")
            if url:
                images.append(url)

    return images


def _safe_album_info(data):
    album = data.get("albumOfTrack") or data.get("album") or {}
    album_name = album.get("name")
    album_uri = album.get("uri")
    album_id = album.get("id")
    album_images = []
    for source in album.get("coverArt", {}).get("sources", []) or []:
        url = source.get("url")
        if url:
            album_images.append(url)
    return {
        "name": album_name,
        "uri": album_uri,
        "id": album_id,
        "images": album_images,
    }


def _safe_duration_ms(data):
    duration = data.get("duration") or {}
    if isinstance(duration, dict):
        total_ms = duration.get("totalMilliseconds")
        if total_ms is not None:
            return total_ms
    return data.get("duration_ms")


@app.get("/")
def home():
    return {"status": "ok", "message": "Spotify public backend ready"}


@app.get("/buscar")
def buscar(q: str, limit: int = 10):
    song = Song()
    result = song.query_songs(q, limit=limit)
    items = result["data"]["searchV2"]["tracksV2"]["items"]
    tracks = []

    for item in items:
        data = item["item"]["data"]
        track = {
            "name": data.get("name"),
            "id": data.get("id"),
            "uri": data.get("uri"),
            "type": data.get("type"),
            "playability": data.get("playability"),
            "duration_ms": _safe_duration_ms(data),
            "track_number": data.get("trackNumber"),
            "disc_number": data.get("discNumber"),
            "is_explicit": data.get("explicit"),
            "popularity": data.get("popularity"),
            "artists": _safe_artist_names(data),
            "artists_raw": data.get("artists", {}).get("items", []),
            "album": _safe_album_info(data),
            "images": _safe_cover_urls(data),
            "preview_url": data.get("previewUrl"),
            "external_urls": data.get("externalUrls"),
            "raw": data,
        }
        tracks.append(track)

    return {"results": tracks}


@app.get("/health")
def health():
    return {"status": "healthy"}
