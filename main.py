from typing import Any

import requests
import yt_dlp
from fastapi import FastAPI, HTTPException, Query
from spotapi import Artist, Song

app = FastAPI(title="Spotify Public API", version="1.1.0")
PIPED_API_HOSTS = [
    "https://pipedapi.kavin.rocks",
    "https://pipedapi.leptons.xyz",
]
PIPED_BASE_URL = PIPED_API_HOSTS[0]


def _piped_get(path: str, params: dict[str, Any] | None = None, timeout: int = 15) -> dict[str, Any] | list[Any]:
    last_error: Exception | None = None
    for base_url in PIPED_API_HOSTS:
        try:
            response = requests.get(f"{base_url}{path}", params=params, timeout=timeout)
            if response.ok:
                payload = response.json()
                if isinstance(payload, (dict, list)):
                    return payload
        except requests.RequestException as exc:
            last_error = exc
            continue
    if last_error is not None:
        raise requests.RequestException(str(last_error))
    return {}


def _piped_video_by_id(video_id: str) -> dict[str, Any]:
    if not video_id:
        return {}

    last_error: Exception | None = None
    for base_url in PIPED_API_HOSTS:
        for endpoint in (f"/api/v1/video/{video_id}", f"/api/v1/streams/{video_id}"):
            try:
                response = requests.get(f"{base_url}{endpoint}", timeout=15)
                if response.ok:
                    payload = response.json()
                    if isinstance(payload, dict):
                        return payload
            except requests.RequestException as exc:
                last_error = exc
                continue
    if last_error is not None:
        raise requests.RequestException(str(last_error))
    return {}


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


def _is_preview_audio_url(url: str | None) -> bool:
    if not isinstance(url, str):
        return False
    candidate = url.lower()
    return any(token in candidate for token in ("preview", "mzstatic", "itunes.apple.com", "audio-ssl.itunes.apple.com"))


def _itunes_search(query: str, limit: int = 10) -> list[dict[str, Any]]:
    try:
        response = requests.get(
            "https://itunes.apple.com/search",
            params={"term": query, "entity": "song", "limit": limit},
            timeout=15,
        )
        if not response.ok:
            return []
        payload = response.json()
        results = payload.get("results", []) if isinstance(payload, dict) else []
        if not isinstance(results, list):
            return []

        tracks: list[dict[str, Any]] = []
        for item in results:
            if not isinstance(item, dict):
                continue
            audio_url = item.get("previewUrl") or item.get("preview_url") or ""
            if not audio_url or _is_preview_audio_url(audio_url):
                continue
            tracks.append({
                "name": item.get("trackName") or item.get("name") or "Sin título",
                "id": str(item.get("trackId") or item.get("id") or ""),
                "uri": item.get("trackViewUrl") or item.get("collectionViewUrl") or "",
                "type": "track",
                "playability": "PLAYABLE",
                "duration_ms": item.get("trackTimeMillis"),
                "track_number": item.get("trackNumber"),
                "disc_number": item.get("discNumber"),
                "is_explicit": item.get("trackExplicitness") == "explicit",
                "popularity": 0,
                "artists": [item.get("artistName") or "Artista desconocido"],
                "album": {
                    "name": item.get("collectionName"),
                    "uri": item.get("collectionViewUrl") or "",
                    "id": item.get("collectionId"),
                    "images": [item.get("artworkUrl100") or item.get("artworkUrl60") or ""],
                },
                "images": [item.get("artworkUrl100") or item.get("artworkUrl60") or ""],
                "preview_url": audio_url,
                "audio_url": audio_url,
                "video_id": "",
                "external_urls": {"apple": item.get("trackViewUrl") or ""},
                "raw": item,
            })
        return tracks
    except requests.RequestException:
        return []


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


def _pick_audio_url(item: dict[str, Any]) -> str:
    if not isinstance(item, dict):
        return ""

    candidates: list[str] = []
    for stream_group in (item.get("audioStreams"), item.get("audio_streams"), item.get("streams")):
        if isinstance(stream_group, list):
            for stream in stream_group:
                if not isinstance(stream, dict):
                    continue
                url = stream.get("url") or stream.get("fileUrl") or stream.get("streamUrl")
                if url:
                    candidates.append(url)
        elif isinstance(stream_group, dict):
            url = stream_group.get("url") or stream_group.get("fileUrl") or stream_group.get("streamUrl")
            if url:
                candidates.append(url)

    if candidates:
        return candidates[0]

    if item.get("url") and isinstance(item.get("url"), str):
        return item["url"]

    return ""


def _piped_track_payload(item: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(item, dict):
        return {}

    title = item.get("title") or item.get("name") or "Sin título"
    video_id = item.get("videoId") or item.get("video_id") or item.get("id") or ""
    uploader = item.get("uploader") or item.get("artist") or "Artista desconocido"
    duration = item.get("duration")
    duration_ms = None
    if isinstance(duration, (int, float)):
        duration_ms = int(duration * 1000)
    elif isinstance(duration, str) and duration.isdigit():
        duration_ms = int(int(duration) * 1000)

    image = item.get("thumbnail") or item.get("thumbnailUrl") or ""
    audio_url = _pick_audio_url(item)

    return {
        "name": title,
        "id": video_id,
        "uri": f"piped://{video_id}" if video_id else "",
        "type": "track",
        "playability": "PLAYABLE",
        "duration_ms": duration_ms,
        "track_number": 1,
        "disc_number": 1,
        "is_explicit": False,
        "popularity": 0,
        "artists": [uploader],
        "album": {"name": uploader, "uri": "", "id": "", "images": [image] if image else []},
        "images": [image] if image else [],
        "preview_url": audio_url,
        "audio_url": audio_url,
        "video_id": video_id,
        "external_urls": {"piped": f"{PIPED_BASE_URL}/watch?v={video_id}" if video_id else ""},
        "raw": item,
    }


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


def _best_audio_from_ytdlp(info: dict[str, Any]) -> str:
    if not isinstance(info, dict):
        return ""

    for key in ("url", "direct_url", "audio_url", "stream_url"):
        value = info.get(key)
        if isinstance(value, str) and value:
            return value

    best_url = ""
    best_score = -1
    for fmt in info.get("formats", []) or []:
        if not isinstance(fmt, dict):
            continue
        url = fmt.get("url") or fmt.get("manifest_url") or fmt.get("fragment_base_url") or ""
        if not url:
            continue
        if fmt.get("vcodec") not in (None, "none", ""):
            continue

        score = 0
        if fmt.get("audio_ext"):
            score += 10
        if fmt.get("abr") is not None:
            score += int(fmt["abr"])
        if fmt.get("tbr") is not None:
            score += int(fmt["tbr"])
        if score > best_score:
            best_url = url
            best_score = score

    return best_url


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
    if not tracks:
        tracks = _itunes_search(q, limit=limit)

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
    if not tracks:
        tracks = _itunes_search(q, limit=limit)
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
    if not tracks:
        tracks = _itunes_search(q, limit=limit)

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


@app.get("/yt/search")
def yt_search(q: str = Query(..., description="Texto a buscar con YouTube"), limit: int = 10):
    q = q.strip()
    if not q:
        raise HTTPException(status_code=400, detail="q no puede ir vacío")

    ydl_opts = {
        "format": "bestaudio/best",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "default_search": "auto",
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            payload = ydl.extract_info(f"ytsearch{limit}:{q}", download=False)
    except Exception as exc:  # pragma: no cover - wrapper for external dependency failure
        raise HTTPException(status_code=502, detail=f"Error consultando YouTube: {exc}") from exc

    entries = payload.get("entries", []) if isinstance(payload, dict) else []
    tracks: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        audio_url = _best_audio_from_ytdlp(entry)
        if not audio_url:
            continue
        tracks.append({
            "name": entry.get("title") or "Sin título",
            "id": entry.get("id") or "",
            "uri": entry.get("webpage_url") or entry.get("url") or "",
            "type": "track",
            "playability": "PLAYABLE",
            "duration_ms": int((entry.get("duration") or 0) * 1000) if isinstance(entry.get("duration"), (int, float)) else None,
            "track_number": 1,
            "disc_number": 1,
            "is_explicit": False,
            "popularity": 0,
            "artists": [entry.get("uploader") or "Artista desconocido"],
            "album": {
                "name": "",
                "uri": "",
                "id": "",
                "images": [entry.get("thumbnail") or ""] if entry.get("thumbnail") else [],
            },
            "images": [entry.get("thumbnail") or ""] if entry.get("thumbnail") else [],
            "preview_url": audio_url,
            "audio_url": audio_url,
            "video_id": entry.get("id") or "",
            "external_urls": {"youtube": entry.get("webpage_url") or ""},
            "raw": entry,
        })

    return {
        "query": q,
        "limit": limit,
        "results": {"tracks": tracks},
    }


@app.get("/piped/search")
def piped_search(q: str = Query(..., description="Texto a buscar en Piped"), limit: int = 10):
    q = q.strip()
    if not q:
        raise HTTPException(status_code=400, detail="q no puede ir vacío")

    try:
        payload = _piped_get("/api/v1/search", params={"q": q, "filter": "all"}, timeout=15)
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"Error consultando Piped: {exc}") from exc

    raw_items: list[Any] = []
    if isinstance(payload, list):
        raw_items = payload
    elif isinstance(payload, dict):
        raw_items = payload.get("items", []) or payload.get("results", []) or []

    items: list[dict[str, Any]] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue

        track = _piped_track_payload(item)
        video_id = track.get("video_id")
        if not video_id:
            continue

        try:
            detailed = _piped_video_by_id(video_id)
            enriched_track = _piped_track_payload(detailed)
            if enriched_track.get("audio_url"):
                track = enriched_track
            elif track.get("audio_url"):
                track = track
            else:
                track["audio_url"] = ""
        except requests.RequestException:
            track["audio_url"] = track.get("audio_url", "")

        if not track.get("audio_url"):
            track["audio_url"] = ""

        if track.get("name"):
            items.append(track)
        if len(items) >= limit:
            break

    if not items:
        items = _itunes_search(q, limit=limit)

    return {
        "query": q,
        "limit": limit,
        "results": {"tracks": items},
    }


@app.get("/piped/track/{video_id}")
def piped_track(video_id: str):
    video_id = video_id.strip()
    if not video_id:
        raise HTTPException(status_code=400, detail="video_id no puede ir vacío")

    try:
        payload = _piped_video_by_id(video_id)
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"Error consultando Piped: {exc}") from exc

    if not payload:
        raise HTTPException(status_code=404, detail="No se encontró información del video")

    track = _piped_track_payload(payload)
    if not track.get("audio_url"):
        raise HTTPException(status_code=404, detail="No se encontró una URL de audio para este video")

    return {"track": track}
