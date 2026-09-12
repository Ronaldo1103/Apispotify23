from fastapi import FastAPI
from spotapi import Song

app = FastAPI(title="Spotify Public API", version="1.0.0")

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
            "name": data["name"],
            "uri": data.get("uri"),
            "artists": [
                artist["profile"]["name"]
                for artist in data.get("artists", {}).get("items", [])
            ],
        }
        tracks.append(track)

    return {"results": tracks}

@app.get("/health")
def health():
    return {"status": "healthy"}
