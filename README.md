# ytdlp-api

This project is a simple FastAPI wrapper around `yt-dlp`. It exposes a small
HTTP API for downloading video or audio from URLs supported by `yt-dlp`.

## What It Does

- Downloads videos with configurable quality, container, codec, and FPS limits.
- Downloads audio-only files in formats such as MP3, M4A, AAC, OPUS, FLAC, and WAV.
- Supports MP3 metadata tagging for title, artist, album, track number, and genre.
- Can return either a JSON response with the saved file path or the downloaded file itself.
- Provides a health check for `yt-dlp`, FFmpeg, and eyeD3 availability.
- Provides an options endpoint so clients can discover supported formats and defaults.

Downloaded video files are saved to `videos/`, and downloaded audio files are
saved to `audio/`.

## Requirements

- Python 3.12+
- FFmpeg
- Python dependencies from `requirements.txt`

Install dependencies:

```bash
pip install -r requirements.txt
```

Run the API locally:

```bash
uvicorn api:app --host 0.0.0.0 --port 7000
```

Or run with Docker Compose:

```bash
docker compose up --build
```

The API listens on port `7000` by default. FastAPI also provides interactive
documentation at `/docs`.

## API Endpoints

All download endpoints require a `url` query parameter. This is true for both
`GET` and `POST` requests. For `POST` requests, the JSON body is only used for
optional download settings.

| Method | Endpoint | Purpose |
| --- | --- | --- |
| `GET` | `/health` | Checks API status and whether `yt-dlp`, FFmpeg, and eyeD3 are available. |
| `GET` | `/options` | Lists default download settings and supported media, quality, codec, container, and audio options. |
| `GET` | `/?url={download_url}` | Saves a download using default options and returns JSON metadata about the saved file. |
| `POST` | `/?url={download_url}` | Saves a download using custom options from the request body and returns JSON metadata about the saved file. |
| `GET` | `/download?url={download_url}` | Downloads with default options and returns the media file directly in the HTTP response. |
| `POST` | `/download?url={download_url}` | Downloads with custom options from the request body and returns the media file directly in the HTTP response. |

## `/` vs `/download`

The `/` endpoints are for clients that want the API to save the downloaded file
on the server and return details about it. They return a JSON response with the
download status, resolved request options, local file path, filename, and
timestamps. These endpoints save files into `videos/` or `audio/` and can be
used for playlist downloads.

The `/download` endpoints are for clients that want the downloaded media file as
the HTTP response. They download to a temporary directory, stream the file back
to the client, and clean up the temporary file afterward. Because the response
is a single file, `/download` does not support playlist downloads.

## Basic Usage

Download a video and return JSON metadata:

```bash
curl "http://localhost:7000/?url=https://example.com/video"
```

Download audio-only MP3 using the simple query option:

```bash
curl "http://localhost:7000/download?url=https://example.com/video&audioOnly=true" -o audio.mp3
```

Download with custom options:

```bash
curl -X POST "http://localhost:7000/?url=https://example.com/video" \
  -H "Content-Type: application/json" \
  -d '{
    "media_type": "video",
    "video_quality": "1080p",
    "container": "mp4",
    "video_codec": "h264",
    "max_fps": 60
  }'
```

For playlist URLs, set `"playlist": true` in the options body. The `/download`
endpoint returns a single file, so playlist downloads are only supported through
the JSON-response endpoints.
