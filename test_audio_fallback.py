from unittest.mock import Mock

from spotify_backend.main import _itunes_search


def test_itunes_search_returns_preview_audio():
    mock_response = Mock()
    mock_response.ok = True
    mock_response.json.return_value = {
        'results': [
            {
                'trackName': 'Yellow',
                'artistName': 'Coldplay',
                'collectionName': 'Parachutes',
                'trackId': 123,
                'previewUrl': 'https://example.com/yellow.m4a',
                'trackViewUrl': 'https://music.apple.com/us/album/yellow',
                'artworkUrl100': 'https://example.com/cover.jpg',
                'trackTimeMillis': 269208,
            }
        ]
    }

    import spotify_backend.main as main
    main.requests.get = Mock(return_value=mock_response)

    results = _itunes_search('coldplay', limit=1)

    assert len(results) == 1
    assert results[0]['name'] == 'Yellow'
    assert results[0]['audio_url'] == 'https://example.com/yellow.m4a'
    assert results[0]['preview_url'] == 'https://example.com/yellow.m4a'
