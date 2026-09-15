import base64
import difflib
import html
import json
import os
import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from pyDes import ECB, PAD_PKCS5, des
import requests
import yt_dlp
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from spotapi import Artist, Song

app = FastAPI(title="Spotify Public API", version="1.1.0")
if __package__:
    from .mp3_download import router as mp3_router
    from .youtube_config import youtube_options, youtube_error
else:
    from mp3_download import router as mp3_router
    from youtube_config import youtube_options, youtube_error
app.include_router(mp3_router)
PIPED_API_HOSTS = [
    "https://pipedapi.kavin.rocks",
    "https://pipedapi.leptons.xyz",
]
PIPED_BASE_URL = PIPED_API_HOSTS[0]
REQUEST_TIMEOUT_SECONDS = 8
HTTP_SESSION = requests.Session()
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY", "").strip()


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


def _youtube_duration_ms(value: str) -> int | None:
    match = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", value or "")
    if not match:
        return None
    hours, minutes, seconds = (int(part or 0) for part in match.groups())
    return (hours * 3600 + minutes * 60 + seconds) * 1000


def _youtube_music_score(
    query: str,
    title: str,
    channel: str,
    duration_ms: int | None,
) -> int:
    text = _normalized_match_text(f"{title} {channel}")
    channel_text = _normalized_match_text(channel)
    blocked_terms = {
        "review", "reaction", "reaccion", "resumen", "recap", "explicado",
        "explicacion", "analysis", "analisis", "entrevista", "podcast",
        "clip", "escena", "scene", "episodio", "episode", "trailer",
        "teaser", "short", "noticia", "news", "ranking", "top 10",
        "karaoke", "cover", "instrumental", "piano", "slowed", "reverb",
        "nightcore", "speed up", "sped up", "remix", "mashup", "fanmade",
        "amv", "fan edit", "fan video", "edit audio", "audio edit", "live",
        "concert", "acoustic", "version", "compilation", "playlist", "ost mix",
    }
    blocked_channel_prefixes = (
        "resum", "senpai", "miko", "noticias", "news", "review", "reaction",
        "anime clip", "anime moments",
    )
    if any(term in text for term in blocked_terms) or any(
        channel_text.startswith(prefix) for prefix in blocked_channel_prefixes
    ):
        return -100

    if duration_ms is not None and (duration_ms < 90_000 or duration_ms > 600_000):
        return -100

    score = 0
    query_tokens = {
        token for token in _normalized_match_text(query).split()
        if len(token) >= 3
    }
    searchable_tokens = set(text.split())
    score += min(len(query_tokens & searchable_tokens), 4) * 12
    if "topic" in channel_text or "official" in channel_text:
        score += 30
    if any(term in text for term in ("official audio", "official music video", "audio")):
        score += 20
    if duration_ms is not None and 90_000 <= duration_ms <= 600_000:
        score += 10
    return score


def _youtube_api_search(query: str, limit: int = 10) -> list[dict[str, Any]]:
    if not YOUTUBE_API_KEY:
        return []

    try:
        search_response = requests.get(
            "https://www.googleapis.com/youtube/v3/search",
            params={
                "key": YOUTUBE_API_KEY,
                "part": "snippet",
                "q": query,
                "type": "video",
                "videoCategoryId": "10",
                "order": "relevance",
                "maxResults": 25,
            },
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        search_response.raise_for_status()
        search_payload = search_response.json()
        items = search_payload.get("items", []) if isinstance(search_payload, dict) else []
    except (requests.RequestException, ValueError):
        return []

    video_ids = [
        item.get("id", {}).get("videoId")
        for item in items
        if isinstance(item, dict) and isinstance(item.get("id"), dict)
    ]
    video_ids = [video_id for video_id in video_ids if video_id]
    durations: dict[str, int | None] = {}
    if video_ids:
        try:
            details_response = requests.get(
                "https://www.googleapis.com/youtube/v3/videos",
                params={
                    "key": YOUTUBE_API_KEY,
                    "part": "contentDetails",
                    "id": ",".join(video_ids),
                },
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
            details_response.raise_for_status()
            details_payload = details_response.json()
            for item in details_payload.get("items", []) if isinstance(details_payload, dict) else []:
                if isinstance(item, dict):
                    durations[item.get("id", "")] = _youtube_duration_ms(
                        item.get("contentDetails", {}).get("duration", "")
                    )
        except (requests.RequestException, ValueError):
            pass

    tracks: list[tuple[int, dict[str, Any]]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        video_id = item.get("id", {}).get("videoId")
        snippet = item.get("snippet", {})
        if not video_id or not isinstance(snippet, dict):
            continue
        thumbnail = (snippet.get("thumbnails", {}).get("high") or snippet.get("thumbnails", {}).get("default") or {}).get("url", "")
        title = snippet.get("title") or "Sin título"
        channel = snippet.get("channelTitle") or "Canal desconocido"
        duration_ms = durations.get(video_id)
        score = _youtube_music_score(query, title, channel, duration_ms)
        if score < 0:
            continue
        tracks.append((score, {
            "name": title,
            "id": video_id,
            "uri": f"https://www.youtube.com/watch?v={video_id}",
            "type": "track",
            "playability": "PLAYABLE",
            "duration_ms": duration_ms,
            "artists": [channel],
            "album": {"name": "YouTube", "uri": "", "id": "", "images": [thumbnail] if thumbnail else []},
            "images": [thumbnail] if thumbnail else [],
            "preview_url": "",
            "audio_url": "",
            "video_id": video_id,
            "external_urls": {"youtube": f"https://www.youtube.com/watch?v={video_id}"},
            "raw": item,
        }))
    tracks.sort(key=lambda entry: entry[0], reverse=True)
    return [track for _, track in tracks[: max(1, min(limit, 25))]]


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


def _is_meaningful_search_query(query: str) -> bool:
    raw = re.sub(r"\s+", " ", (query or "").strip())
    if not raw:
        return False

    normalized = re.sub(r"[^a-z0-9\s-]", " ", raw.lower())
    tokens = [token for token in normalized.split() if token]
    if not tokens:
        return False

    if len(raw) < 3:
        return False

    stop_words = {
        "a", "an", "and", "are", "as", "at", "be", "by", "d", "de", "del", "do",
        "for", "from", "g", "in", "is", "it", "la", "of", "on", "or", "the", "to",
        "y", "ya", "un", "una", "vs", "video", "song", "music"
    }

    if len(tokens) == 1 and tokens[0] in stop_words:
        return False

    if all(token in stop_words for token in tokens):
        return False

    return True


def _youtube_search_variants(query: str, limit: int = 10) -> list[str]:
    raw = re.sub(r"\s+", " ", (query or "").strip())
    if not raw:
        return []

    if not _is_meaningful_search_query(raw):
        return []

    variants: list[str] = []
    seen: set[str] = set()

    def add_variant(value: str) -> None:
        cleaned = re.sub(r"\s+", " ", value).strip()
        if cleaned and cleaned.lower() not in seen:
            seen.add(cleaned.lower())
            variants.append(cleaned)

    add_variant(raw)
    add_variant(f'"{raw}"')
    add_variant(f'{raw} official audio')
    add_variant(f'{raw} lyrics')

    tokens = [token for token in raw.split() if len(token) > 2]
    if len(tokens) >= 2:
        add_variant(" ".join(tokens[:2]))
        add_variant(f'{tokens[0]} {tokens[-1]}')
        add_variant(f'{tokens[0]} {tokens[-1]} official')

    if " - " in raw:
        left, right = [part.strip() for part in raw.split(" - ", 1)]
        if left and right:
            add_variant(f'{left} {right}')

    return variants[: max(1, min(limit, 4))]


def _saavn_json_decode(text: str) -> Any:
    if not isinstance(text, str):
        return {}
    cleaned = text.strip()
    if not cleaned:
        return {}
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        cleaned = re.sub(r',\s*([}\]])', r'\1', cleaned)
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            return {}


def _decrypt_saavn_media_url(encrypted_url: str) -> str:
    if not isinstance(encrypted_url, str) or not encrypted_url.strip():
        return ""
    try:
        cipher = des(
            b"38346591",
            ECB,
            b"\0" * 8,
            pad=None,
            padmode=PAD_PKCS5,
        )
        decoded = cipher.decrypt(
            base64.b64decode(encrypted_url.strip()),
            padmode=PAD_PKCS5,
        ).decode("utf-8")
        return decoded.replace("_96.mp4", "_320.mp4")
    except (ValueError, TypeError, UnicodeDecodeError):
        return ""


def _saavn_search(query: str, limit: int = 5) -> list[dict[str, Any]]:
    q = (query or "").strip()
    if not q:
        return []

    try:
        response = requests.get(
            "https://www.jiosaavn.com/api.php",
            params={
                "__call": "autocomplete.get",
                "_format": "json",
                "_marker": "0",
                "cc": "in",
                "includeMetaTags": "1",
                "query": q,
            },
            timeout=8,
        )
        response.raise_for_status()
        payload = _saavn_json_decode(response.text)
    except requests.RequestException:
        return []

    if not isinstance(payload, dict):
        return []

    songs = payload.get("songs", {}).get("data", []) if isinstance(payload.get("songs"), dict) else []
    if not isinstance(songs, list):
        return []

    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in songs[: max(1, min(limit * 2, 6))]:
        if not isinstance(item, dict):
            continue
        song_id = item.get("id") or item.get("song_id") or ""
        if not song_id:
            continue

        title = str(item.get("song") or item.get("title") or "").strip()
        artist = str(item.get("primary_artists") or item.get("music") or "").strip()
        key = f"{title}|{artist}|{song_id}".lower()
        if key in seen:
            continue
        seen.add(key)

        try:
            details = requests.get(
                "https://www.jiosaavn.com/api.php",
                params={
                    "__call": "song.getDetails",
                    "cc": "in",
                    "_marker": "0",
                    "_format": "json",
                    "pids": song_id,
                },
                timeout=8,
            )
            details.raise_for_status()
            details_payload = _saavn_json_decode(details.text)
        except requests.RequestException:
            continue

        track_data = details_payload.get(song_id, {}) if isinstance(details_payload, dict) else {}
        if not isinstance(track_data, dict):
            continue

        media_url = track_data.get("media_url") or ""
        if not media_url:
            media_url = _decrypt_saavn_media_url(track_data.get("encrypted_media_url") or "")
        if not media_url:
            continue

        results.append({
            "id": song_id,
            "song": track_data.get("song") or title or "Sin título",
            "title": track_data.get("song") or title or "Sin título",
            "artists": [track_data.get("primary_artists") or artist or "Artista desconocido"],
            "album": track_data.get("album") or item.get("album") or "",
            "image": track_data.get("image") or item.get("image") or "",
            "perma_url": track_data.get("perma_url") or item.get("perma_url") or "",
            "media_url": media_url,
            "raw": track_data,
        })
        if len(results) >= limit:
            break

    return results


def _saavn_search_many(queries: list[str], limit: int = 5) -> list[dict[str, Any]]:
    query_list = [q.strip() for q in (queries or []) if isinstance(q, str) and q.strip()]
    if not query_list:
        return []

    merged: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _search_one(query: str) -> list[dict[str, Any]]:
        return _saavn_search(query, limit=max(1, min(2, limit)))

    with ThreadPoolExecutor(max_workers=min(4, len(query_list))) as executor:
        for batch in executor.map(_search_one, query_list[: min(4, len(query_list))]):
            for item in batch:
                key = (item.get("id") or item.get("title") or "").lower()
                if not key or key in seen:
                    continue
                seen.add(key)
                merged.append(item)
                if len(merged) >= limit:
                    return merged

    return merged[:limit]


def _normalized_match_text(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _saavn_matches_spotify_track(
    spotify_track: dict[str, Any], saavn_track: dict[str, Any]
) -> bool:
    spotify_title = _normalized_match_text(spotify_track.get("name"))
    saavn_title = _normalized_match_text(saavn_track.get("title"))
    if not spotify_title or not saavn_title:
        return False

    title_matches = (
        spotify_title == saavn_title
        or spotify_title in saavn_title
        or saavn_title in spotify_title
        or difflib.SequenceMatcher(None, spotify_title, saavn_title).ratio() >= 0.82
    )
    if not title_matches:
        return False

    spotify_artists = spotify_track.get("artists") or []
    saavn_artists = saavn_track.get("artists") or []
    if isinstance(spotify_artists, str):
        spotify_artists = [spotify_artists]
    if isinstance(saavn_artists, str):
        saavn_artists = [saavn_artists]

    spotify_artist_text = " ".join(
        _normalized_match_text(artist) for artist in spotify_artists
    ).strip()
    saavn_artist_text = " ".join(
        _normalized_match_text(artist) for artist in saavn_artists
    ).strip()
    if not spotify_artist_text or not saavn_artist_text:
        return False

    spotify_artist_tokens = set(spotify_artist_text.split())
    saavn_artist_tokens = set(saavn_artist_text.split())
    artist_overlap = spotify_artist_tokens & saavn_artist_tokens
    return bool(
        spotify_artist_text in saavn_artist_text
        or saavn_artist_text in spotify_artist_text
        or artist_overlap
    )


def _spotify_track_matches_query(track: dict[str, Any], query: str) -> bool:
    query_tokens = set(_normalized_match_text(query).split())
    if not query_tokens:
        return True

    searchable_values = [
        track.get("name"),
        *(track.get("artists") or []),
        (track.get("album") or {}).get("name"),
    ]
    searchable_text = _normalized_match_text(" ".join(str(value or "") for value in searchable_values))
    return any(token in searchable_text.split() for token in query_tokens)


def _saavn_queries_from_spotify_track(track: dict[str, Any]) -> list[str]:
    name = (track.get("name") or "").strip()
    artists = track.get("artists") or []
    if isinstance(artists, str):
        artists = [artists]

    candidates: list[str] = []
    seen: set[str] = set()

    def add(value: str) -> None:
        value = re.sub(r"\s+", " ", (value or "").strip())
        if value and value.lower() not in seen:
            seen.add(value.lower())
            candidates.append(value)

    for artist in artists:
        artist_name = (artist or "").strip()
        if not artist_name:
            continue
        add(f"{artist_name} {name}")
        add(f"{name} {artist_name}")
    add(name)

    if not candidates and name:
        add(name)

    return candidates[:2]


def _spotify_tracks_for_query(query: str, limit: int = 5) -> list[dict[str, Any]]:
    q = (query or "").strip()
    if not q:
        return []
    try:
        search = Song().query_songs(q, limit=limit)
    except Exception:
        return []
    tracks = [_extract_track_payload(item) for item in _extract_search_items(search, "tracksV2")]
    related_tracks = [track for track in tracks if _spotify_track_matches_query(track, q)]
    return (related_tracks or tracks)[:limit]


@app.get("/")
def home():
    return {"status": "ok", "message": "Spotify public backend ready"}


@app.get("/health")
def health():
    return {"status": "healthy"}


@app.get("/youtube/search")
def youtube_search(q: str = Query(..., description="Buscar metadatos de videos en YouTube"), limit: int = 10):
    q = q.strip()
    if not q:
        raise HTTPException(status_code=400, detail="q no puede ir vacío")
    if not YOUTUBE_API_KEY:
        raise HTTPException(status_code=503, detail="YOUTUBE_API_KEY no está configurada")
    return {"query": q, "limit": limit, "results": {"tracks": _youtube_api_search(q, limit)}}


@app.get("/youtube/url")
def youtube_url(title: str = Query(...), artist: str = Query("")):
    query = " ".join(part.strip() for part in (title, artist) if part.strip())
    if not query:
        raise HTTPException(status_code=400, detail="title no puede ir vacío")
    if not YOUTUBE_API_KEY:
        raise HTTPException(status_code=503, detail="YOUTUBE_API_KEY no está configurada")

    results = _youtube_api_search(query, limit=5)
    if not results:
        raise HTTPException(status_code=404, detail="No se encontró un video musical")

    selected = None
    for candidate in results:
        candidate_id = candidate.get("video_id") or ""
        if not re.fullmatch(r"[A-Za-z0-9_-]{11}", candidate_id):
            continue
        try:
            with yt_dlp.YoutubeDL({
                "format": "bestaudio/best",
                "noplaylist": True,
                "quiet": True,
                "skip_download": True,
                **youtube_options(),
            }) as ydl:
                info = ydl.extract_info(
                    f"https://www.youtube.com/watch?v={candidate_id}",
                    download=False,
                )
            if info and info.get("id"):
                selected = candidate
                break
        except Exception:
            continue

    if selected is None:
        raise HTTPException(status_code=404, detail="Ningún resultado se puede descargar")

    video_id = selected.get("video_id") or ""
    return {
        "title": selected.get("name"),
        "artist": (selected.get("artists") or [""])[0],
        "video_id": video_id,
        "video_url": f"https://www.youtube.com/watch?v={video_id}",
        "duration_ms": selected.get("duration_ms"),
    }


@app.get("/youtube/resolve/{video_id}")
def youtube_resolve(video_id: str):
    video_id = video_id.strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{6,20}", video_id):
        raise HTTPException(status_code=400, detail="video_id no válido")

    ydl_opts = {
        "format": "bestaudio/best",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
    }
    try:
        with yt_dlp.YoutubeDL({**ydl_opts, **youtube_options()}) as ydl:
            info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
    except Exception as exc:
        raise youtube_error(exc) from exc

    audio_url = _best_audio_from_ytdlp(info)
    if not audio_url or _is_preview_audio_url(audio_url):
        raise HTTPException(status_code=404, detail="No se encontró audio reproducible")

    return {
        "track": {
            "name": info.get("title") or "Sin título",
            "id": video_id,
            "uri": info.get("webpage_url") or f"https://www.youtube.com/watch?v={video_id}",
            "type": "track",
            "playability": "PLAYABLE",
            "duration_ms": int(info.get("duration", 0) * 1000) if isinstance(info.get("duration"), (int, float)) else None,
            "artists": [info.get("uploader") or "Canal desconocido"],
            "album": {"name": "YouTube", "uri": "", "id": "", "images": [info.get("thumbnail", "")]},
            "images": [info.get("thumbnail", "")],
            "preview_url": audio_url,
            "audio_url": audio_url,
            "video_id": video_id,
            "external_urls": {"youtube": info.get("webpage_url") or ""},
        }
    }


@app.get("/debug/youtube", response_class=HTMLResponse)
def youtube_debug(q: str = Query("The Promise Deaimon", description="Texto para probar YouTube Data API"), limit: int = 5):
    query = q.strip() or "The Promise Deaimon"
    results = _youtube_api_search(query, limit=max(1, min(limit, 10))) if YOUTUBE_API_KEY else []
    status = "Clave configurada" if YOUTUBE_API_KEY else "Falta configurar YOUTUBE_API_KEY"
    status_color = "#55d187" if YOUTUBE_API_KEY else "#ff8b8b"
    rows: list[str] = []
    for item in results:
        video_id = html.escape(str(item.get("video_id", "")))
        title = html.escape(str(item.get("name", "Sin título")))
        artist = html.escape(str((item.get("artists") or ["Canal desconocido"])[0]))
        duration = item.get("duration_ms")
        duration_text = f"{int(duration // 60000)}:{int((duration % 60000) // 1000):02d}" if isinstance(duration, int) else "duración no disponible"
        rows.append(
            f"<article><h2>{title}</h2><p><b>Canal:</b> {artist} | <b>Duración:</b> {duration_text}</p>"
            f"<a href='https://www.youtube.com/watch?v={video_id}' target='_blank'>Abrir video en YouTube</a>"
            f"<p class='id'>Video ID: {video_id}</p></article>"
        )
    if not rows and YOUTUBE_API_KEY:
        rows.append("<p>No se encontraron videos para esa búsqueda.</p>")
    if not YOUTUBE_API_KEY:
        rows.append("<p>Configura YOUTUBE_API_KEY en el entorno del backend y reinicia Uvicorn.</p>")

    return HTMLResponse(content=f"""
    <!doctype html>
    <html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
    <title>YouTube API debug</title>
    <style>
      body {{ font-family: system-ui, sans-serif; max-width: 860px; margin: 32px auto; padding: 0 18px; background: #101417; color: #f4f7f8; }}
      h1 {{ margin-bottom: 8px; }} form {{ display: flex; gap: 8px; margin: 24px 0; }}
      input {{ flex: 1; padding: 12px; border-radius: 8px; border: 1px solid #39464d; background: #1b2429; color: white; }}
      button {{ padding: 12px 18px; border: 0; border-radius: 8px; background: #ff0033; color: white; font-weight: 700; cursor: pointer; }}
      article {{ padding: 16px; margin: 12px 0; border: 1px solid #334047; border-radius: 10px; background: #182126; }}
      article h2 {{ font-size: 18px; margin: 0 0 8px; }} a {{ color: #8fd3ff; }} .id {{ color: #94a5ad; font-size: 12px; }}
      .status {{ color: {status_color}; font-weight: 700; }}
    </style></head><body>
      <h1>YouTube Data API</h1><p class="status">Estado: {status}</p>
      <form method="get" action="/debug/youtube"><input name="q" value="{html.escape(query)}" placeholder="Canción, artista o anime"><button>Buscar</button></form>
      <p>Consulta: <b>{html.escape(query)}</b></p>{''.join(rows)}
    </body></html>
    """)


@app.get("/saavn/search")
def saavn_search(q: str = Query(..., description="Buscar en JioSaavn usando metadatos de Spotify"), limit: int = 5):
    q = q.strip()
    if not q:
        raise HTTPException(status_code=400, detail="q no puede ir vacío")

    spotify_tracks = _spotify_tracks_for_query(q, limit=limit)
    matched_results: list[dict[str, Any]] = []
    seen_tracks: set[str] = set()

    for track in spotify_tracks[: max(1, min(limit, 3))]:
        candidate_queries = _saavn_queries_from_spotify_track(track)
        saavn_results = _saavn_search_many(candidate_queries, limit=2)
        for saavn_item in saavn_results:
            if not _saavn_matches_spotify_track(track, saavn_item):
                continue
            title = (saavn_item.get("title") or "").lower()
            if title and title in seen_tracks:
                continue
            matched_results.append({
                "spotify_query": q,
                "jiosaavn_query": candidate_queries[0] if candidate_queries else q,
                "spotify_track": track,
                "saavn_track": saavn_item,
            })
            if title:
                seen_tracks.add(title)
            if len(matched_results) >= limit:
                break
        if len(matched_results) >= limit:
            break

    return {"query": q, "limit": limit, "results": matched_results}


@app.get("/debug/saavn", response_class=HTMLResponse)
def saavn_debug(q: str = Query("coldplay", description="Texto para probar resultados de JioSaavn"), limit: int = 5):
    q = q.strip() or "coldplay"
    spotify_tracks = _spotify_tracks_for_query(q, limit=limit)

    rows: list[str] = []
    if not spotify_tracks:
        rows.append(f"<p>No se encontraron resultados de Spotify para '{q}'.</p>")

    for track in spotify_tracks[: max(1, min(limit, 3))]:
        candidates = _saavn_queries_from_spotify_track(track)
        rows.append(f"<div class='card'><h3>{track.get('name', 'Sin título')}</h3>")
        rows.append(f"<p><b>Spotify:</b> {track.get('artists', ['Artista desconocido'])[0]} | {track.get('album', {}).get('name', 'Sin álbum')}</p>")
        rows.append("<ul>")
        saavn_results = [
            item for item in _saavn_search_many(candidates, limit=2)
            if _saavn_matches_spotify_track(track, item)
        ]
        if not saavn_results:
            rows.append("<li>No se encontraron resultados reales en JioSaavn.</li>")
        for saavn_item in saavn_results:
            media_url = saavn_item.get('media_url') or ''
            rows.append(
                f"<li><b>{saavn_item.get('title')}</b> - {saavn_item.get('artists', ['Artista desconocido'])[0]}<br>"
                f"album: {saavn_item.get('album') or 'N/A'}<br>"
                f"media_url: <a href='{media_url}' target='_blank'>{media_url}</a></li>"
            )
        rows.append("</ul></div>")

    html = f"""
    <html>
      <head>
        <title>JioSaavn debug</title>
        <meta name="viewport" content="width=device-width, initial-scale=1" />
        <style>
          body {{ font-family: Arial, sans-serif; margin: 24px; background: #121212; color: #f5f5f5; }}
          a {{ color: #8ec5ff; }}
          .card {{ border: 1px solid #333; padding: 16px; border-radius: 12px; margin-bottom: 18px; background: #1b1b1b; }}
          input, button {{ padding: 10px 12px; border-radius: 8px; border: 1px solid #444; margin-right: 8px; }}
          button {{ background: #1db954; color: white; border: none; cursor: pointer; }}
          form {{ margin-bottom: 22px; }}
        </style>
      </head>
      <body>
        <h1>JioSaavn + Spotify metadata debug</h1>
        <form method="get" action="/debug/saavn">
          <input type="text" name="q" value="{q}" placeholder="Busca una canción o artista" />
          <button type="submit">Buscar</button>
        </form>
        <p>Consulta original: <b>{q}</b></p>
        {''.join(rows)}
      </body>
    </html>
    """
    return HTMLResponse(content=html)


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

    if not _is_meaningful_search_query(q):
        return {
            "query": q,
            "limit": limit,
            "results": {"tracks": []},
        }

    ydl_opts = {
        "format": "bestaudio/best",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "default_search": "auto",
        "extract_flat": False,
    }

    tracks: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    queries = _youtube_search_variants(q, limit=min(limit, 4))

    for query_variant in queries:
        try:
            with yt_dlp.YoutubeDL({**ydl_opts, **youtube_options()}) as ydl:
                payload = ydl.extract_info(f"ytsearch{min(limit, 5)}:{query_variant}", download=False)
        except Exception:
            continue

        entries = payload.get("entries", []) if isinstance(payload, dict) else []
        for entry in entries:
            if not isinstance(entry, dict):
                continue

            video_id = (entry.get("id") or "").strip()
            if not video_id or video_id in seen_ids:
                continue

            title = (entry.get("title") or "").strip()
            if not title or len(title) < 3:
                continue

            if entry.get("is_live") or entry.get("live_status") in {"is_live", "was_live", "live"}:
                continue

            if entry.get("availability") in {"unavailable", "subscriber_only", "private"}:
                continue

            audio_url = _best_audio_from_ytdlp(entry)
            if not audio_url or _is_preview_audio_url(audio_url):
                continue

            seen_ids.add(video_id)
            tracks.append({
                "name": title,
                "id": video_id,
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
                "video_id": video_id,
                "external_urls": {"youtube": entry.get("webpage_url") or ""},
                "raw": entry,
            })

            if len(tracks) >= limit:
                break

        if len(tracks) >= limit:
            break

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
