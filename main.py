from typing import Any

from fastapi import FastAPI, HTTPException, Query
from spotapi import Artist, Song

app = FastAPI(title="Spotify Public API", version="1.1.0")


def _item_data(item: Any) -> dict[str, Any]:
    if not isinstance(item, dict):
        return {}
    if "item" in item and isinstance(item["item"], dict):
        data = item["item"].get("data", {})
        if isinstance(data, dict):
            return data
    if "data" in item and isinstance(item["data"], dict):
        return item["data"]
    return item


def _safe_artist_names(data: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for artist in (data.get("artists", {}) or {}).get("items", []) or []:
        if isinstance(artist, dict):
            name = artist.get("profile", {}).get("name") or artist.get("name")
            if name:
                names.append(name)
    if not names:
        for artist in (data.get("artist", {}) or {}).get("items", []) or []:
            if isinstance(artist, dict):
                name = artist.get("profile", {}).get("name") or artist.get("name")
                if name:
                    names.append(name)
    return names


def _safe_cover_urls(data: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    for container in [data.get("coverArt"), data.get("cover_art"), data.get("album", {}).get("coverArt")]:
        if not isinstance(container, dict):
            continue
        for source in (container.get("sources") or []) or []:
            url = source.get("url") if isinstance(source, dict) else None
            if url:
                urls.append(url)
    if urls:
        return urls

    album = data.get("albumOfTrack") or data.get("album") or {}
    for source in (album.get("coverArt", {}).get("sources") or []) or []:
        url = source.get("url") if isinstance(source, dict) else None
        if url:
            urls.append(url)
    return urls


def _safe_album_info(data: dict[str, Any]) -> dict[str, Any]:
    album = data.get("albumOfTrack") or data.get("album") or {}
    if isinstance(album, dict):
        images = []
        for source in (album.get("coverArt", {}).get("sources") or []) or []:
            url = source.get("url") if isinstance(source, dict) else None
            if url:
                images.append(url)
        return {
            "name": album.get("name"),
            "uri": album.get("uri"),
            "id": album.get("id"),
            "images": images,
        }
    return {"name": None, "uri": None, "id": None, "images": []}


def _safe_duration_ms(data: dict[str, Any]) -> int | None:
    duration = data.get("duration") or {}
    if isinstance(duration, dict):
        total_ms = duration.get("totalMilliseconds")
        if total_ms is not None:
            return total_ms
    return data.get("duration_ms")


def _extract_search_items(raw_response: dict[str, Any], section: str) -> list[dict[str, Any]]:
    payload = raw_response.get("data", {}).get("searchV2", {})
    section_data = payload.get(section)
    if not isinstance(section_data, dict):
        return []
    items = section_data.get("items", []) or []
    return [_item_data(item) for item in items if isinstance(item, dict)]


def _extract_playlist_payload(item: dict[str, Any]) -> dict[str, Any]:
    owner = item.get("owner") or {}
    images = []
    for source in (item.get("coverArt", {}).get("sources") or []) or []:
        if isinstance(source, dict):
            url = source.get("url")
            if url:
                images.append(url)
    return {
        "name": item.get("name"),
        "id": item.get("id"),
        "uri": item.get("uri"),
        "type": item.get("type"),
        "owner": owner.get("name") or owner.get("displayName"),
        "description": item.get("description"),
        "total_tracks": item.get("totalLength") or item.get("totalTracks"),
        "images": images,
        "raw": item,
    }


def _extract_artist_payload(item: dict[str, Any]) -> dict[str, Any]:
    profile = item.get("profile") or {}
    visuals = item.get("visuals") or {}
    avatar = visuals.get("avatarImage", {}) or {}
    images = [source.get("url") for source in (avatar.get("sources") or []) if isinstance(source, dict) and source.get("url")]
    return {
        "name": profile.get("name") or item.get("name"),
        "id": item.get("id"),
        "uri": item.get("uri"),
        "type": item.get("type"),
        "genres": item.get("genres") or [],
        "images": images,
        "followers": item.get("followers"),
        "raw": item,
    }


def _extract_album_payload(item: dict[str, Any]) -> dict[str, Any]:
    images = []
    for source in (item.get("coverArt", {}).get("sources") or []) or []:
        if isinstance(source, dict):
            url = source.get("url")
            if url:
                images.append(url)
    return {
        "name": item.get("name"),
        "id": item.get("id"),
        "uri": item.get("uri"),
        "type": item.get("type"),
        "artists": _safe_artist_names(item),
        "release_date": item.get("releaseDate"),
        "total_tracks": item.get("trackCount") or item.get("totalTracks"),
        "images": images,
        "raw": item,
    }


def _extract_track_payload(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": item.get("name"),
        "id": item.get("id"),
        "uri": item.get("uri"),
        "type": item.get("type"),
        "playability": item.get("playability"),
        "duration_ms": _safe_duration_ms(item),
        "track_number": item.get("trackNumber"),
        "disc_number": item.get("discNumber"),
        "is_explicit": item.get("explicit"),
        "popularity": item.get("popularity"),
        "artists": _safe_artist_names(item),
        "artists_raw": item.get("artists", {}).get("items", []),
        "album": _safe_album_info(item),
        "images": _safe_cover_urls(item),
        "preview_url": item.get("previewUrl"),
        "external_urls": item.get("externalUrls"),
        "raw": item,
    }


@app.get("/")
def home():
    return {"status": "ok", "message": "Spotify public backend ready"}


@app.get("/health")
def health():
    return {"status": "healthy"}


@app.get("/buscar")
def buscar(q: str = Query(..., description="Texto a buscar"), limit: int = 10):
    q = q.strip()
    if not q:
        raise HTTPException(status_code=400, detail="q no puede ir vacío")

    search = Song().query_songs(q, limit=limit)
    tracks = [_extract_track_payload(item) for item in _extract_search_items(search, "tracksV2")]
    artists = [_extract_artist_payload(item) for item in _extract_search_items(search, "artists")]
    albums = [_extract_album_payload(item) for item in _extract_search_items(search, "albums")]
    playlists = [_extract_playlist_payload(item) for item in _extract_search_items(search, "playlists")]

    return {
        "query": q,
        "limit": limit,
        "results": {
            "tracks": tracks,
            "artists": artists,
            "albums": albums,
            "playlists": playlists,
        },
    }


@app.get("/buscar-canciones")
def buscar_canciones(q: str = Query(...), limit: int = 10):
    q = q.strip()
    if not q:
        raise HTTPException(status_code=400, detail="q no puede ir vacío")

    search = Song().query_songs(q, limit=limit)
    tracks = [_extract_track_payload(item) for item in _extract_search_items(search, "tracksV2")]
    return {"query": q, "limit": limit, "results": {"tracks": tracks}}


@app.get("/buscar-artistas")
def buscar_artistas(q: str = Query(...), limit: int = 10):
    q = q.strip()
    if not q:
        raise HTTPException(status_code=400, detail="q no puede ir vacío")

    search = Song().query_songs(q, limit=limit)
    artists = [_extract_artist_payload(item) for item in _extract_search_items(search, "artists")]
    return {"query": q, "limit": limit, "results": {"artists": artists}}


@app.get("/buscar-albumes")
def buscar_albumes(q: str = Query(...), limit: int = 10):
    q = q.strip()
    if not q:
        raise HTTPException(status_code=400, detail="q no puede ir vacío")

    search = Song().query_songs(q, limit=limit)
    albums = [_extract_album_payload(item) for item in _extract_search_items(search, "albums")]
    return {"query": q, "limit": limit, "results": {"albums": albums}}


@app.get("/buscar-playlists")
def buscar_playlists(q: str = Query(...), limit: int = 10):
    q = q.strip()
    if not q:
        raise HTTPException(status_code=400, detail="q no puede ir vacío")

    search = Song().query_songs(q, limit=limit)
    playlists = [_extract_playlist_payload(item) for item in _extract_search_items(search, "playlists")]
    return {"query": q, "limit": limit, "results": {"playlists": playlists}}


@app.get("/buscar-todo")
def buscar_todo(q: str = Query(...), limit: int = 10):
    q = q.strip()
    if not q:
        raise HTTPException(status_code=400, detail="q no puede ir vacío")

    search = Song().query_songs(q, limit=limit)
    tracks = [_extract_track_payload(item) for item in _extract_search_items(search, "tracksV2")]
    artists = [_extract_artist_payload(item) for item in _extract_search_items(search, "artists")]
    albums = [_extract_album_payload(item) for item in _extract_search_items(search, "albums")]
    playlists = [_extract_playlist_payload(item) for item in _extract_search_items(search, "playlists")]

    return {
        "query": q,
        "limit": limit,
        "results": {
            "tracks": tracks,
            "artists": artists,
            "albums": albums,
            "playlists": playlists,
        },
    }
