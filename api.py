from __future__ import annotations

import shutil
import tempfile
from datetime import UTC, datetime
from enum import Enum, IntEnum
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import Body, FastAPI, HTTPException, Query, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator
from starlette.background import BackgroundTask

try:
    import eyed3
except ImportError:
    eyed3 = None

try:
    import yt_dlp
except ImportError:
    yt_dlp = None


BASE_DIR = Path(__file__).resolve().parent
AUDIO_DIR = BASE_DIR / "audio"
VIDEO_DIR = BASE_DIR / "videos"


class MediaType(str, Enum):
    VIDEO = "video"
    AUDIO = "audio"


class VideoQuality(str, Enum):
    HIGHEST = "highest"
    P2160 = "2160p"
    P1440 = "1440p"
    P1080 = "1080p"
    P720 = "720p"
    P480 = "480p"
    P360 = "360p"


class MaxFps(IntEnum):
    FPS_30 = 30
    FPS_60 = 60


class VideoCodec(str, Enum):
    H264 = "h264"
    AV1 = "av1"
    VP9 = "vp9"
    ANY = "any"


class VideoContainer(str, Enum):
    MP4 = "mp4"
    MKV = "mkv"
    WEBM = "webm"


class AudioOnlyFormat(str, Enum):
    MP3 = "mp3"
    M4A = "m4a"
    AAC = "aac"
    OPUS = "opus"
    FLAC = "flac"
    WAV = "wav"


class AudioQuality(str, Enum):
    BEST = "best"
    KBPS_320 = "320k"
    KBPS_256 = "256k"
    KBPS_192 = "192k"
    KBPS_128 = "128k"


class DownloadStatus(str, Enum):
    COMPLETED = "completed"


VIDEO_QUALITY_HEIGHTS = {
    VideoQuality.HIGHEST: None,
    VideoQuality.P2160: 2160,
    VideoQuality.P1440: 1440,
    VideoQuality.P1080: 1080,
    VideoQuality.P720: 720,
    VideoQuality.P480: 480,
    VideoQuality.P360: 360,
}

VIDEO_CODEC_FILTERS = {
    VideoCodec.H264: "[vcodec^=avc1]",
    VideoCodec.AV1: "[vcodec^=av01]",
    VideoCodec.VP9: "[vcodec^=vp9]",
    VideoCodec.ANY: "",
}


class AudioMetadata(BaseModel):
    title: str | None = None
    artist: str | None = None
    album: str | None = None
    track_number: int | None = Field(default=None, ge=1)
    genre: str | None = None

    def has_values(self) -> bool:
        return any(value is not None and value != "" for value in self.model_dump().values())


class DownloadRequest(BaseModel):
    url: str = Field(..., min_length=1)
    media_type: MediaType = MediaType.VIDEO
    video_quality: VideoQuality = VideoQuality.HIGHEST
    max_fps: MaxFps | None = None
    container: VideoContainer = VideoContainer.MP4
    video_codec: VideoCodec = VideoCodec.H264
    audio_only_format: AudioOnlyFormat = AudioOnlyFormat.MP3
    audio_quality: AudioQuality = AudioQuality.BEST
    audio_metadata: AudioMetadata | None = None
    playlist: bool = False
    overwrite: bool = False

    @field_validator("url")
    @classmethod
    def strip_url(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("URL cannot be blank.")
        return value

    @model_validator(mode="after")
    def validate_supported_options(self) -> DownloadRequest:
        if self.media_type == MediaType.VIDEO:
            if self.container == VideoContainer.WEBM and self.video_codec == VideoCodec.H264:
                raise ValueError("H.264 video is not supported in a WebM container. Use mp4 or mkv.")

        if (
            self.media_type == MediaType.AUDIO
            and self.audio_metadata
            and self.audio_metadata.has_values()
            and self.audio_only_format != AudioOnlyFormat.MP3
        ):
            raise ValueError("Audio metadata tagging currently supports MP3 output only.")

        return self


class DownloadOptions(BaseModel):
    media_type: MediaType | None = None
    video_quality: VideoQuality | None = None
    max_fps: MaxFps | None = None
    container: VideoContainer | None = None
    video_codec: VideoCodec | None = None
    audio_only_format: AudioOnlyFormat | None = None
    audio_quality: AudioQuality | None = None
    audio_metadata: AudioMetadata | None = None
    playlist: bool | None = None
    overwrite: bool | None = None


class DownloadResponse(BaseModel):
    status: DownloadStatus
    request: DownloadRequest
    file_path: str
    filename: str
    started_at: datetime | None = None
    finished_at: datetime | None = None


class OptionsResponse(BaseModel):
    defaults: dict[str, Any]
    media_types: list[str]
    video_qualities: list[str]
    max_fps: list[int]
    video_containers: list[str]
    video_codecs: list[str]
    audio_only_formats: list[str]
    audio_qualities: list[str]
    download_statuses: list[str]


app = FastAPI(
    title="YouTube Downloader API",
    version="1.0.0",
    description="Download video or audio with yt-dlp using a small, stable JSON API.",
)

@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "yt_dlp_available": yt_dlp is not None,
        "ffmpeg_available": shutil.which("ffmpeg") is not None,
        "eyed3_available": eyed3 is not None,
    }


@app.get("/options", response_model=OptionsResponse)
def get_options() -> OptionsResponse:
    return OptionsResponse(
        defaults={
            "media_type": DownloadRequest.model_fields["media_type"].default.value,
            "video_quality": DownloadRequest.model_fields["video_quality"].default.value,
            "max_fps": DownloadRequest.model_fields["max_fps"].default,
            "container": DownloadRequest.model_fields["container"].default.value,
            "video_codec": DownloadRequest.model_fields["video_codec"].default.value,
            "audio_only_format": DownloadRequest.model_fields["audio_only_format"].default.value,
            "audio_quality": DownloadRequest.model_fields["audio_quality"].default.value,
            "playlist": DownloadRequest.model_fields["playlist"].default,
            "overwrite": DownloadRequest.model_fields["overwrite"].default,
        },
        media_types=enum_values(MediaType),
        video_qualities=enum_values(VideoQuality),
        max_fps=[int(value.value) for value in MaxFps],
        video_containers=enum_values(VideoContainer),
        video_codecs=enum_values(VideoCodec),
        audio_only_formats=enum_values(AudioOnlyFormat),
        audio_qualities=enum_values(AudioQuality),
        download_statuses=enum_values(DownloadStatus),
    )


@app.get("/", response_model=DownloadResponse)
def create_download_with_defaults(
    url: str = Query(..., min_length=1, description="Video or playlist URL to download."),
    audio_only: bool = Query(False, alias="audioOnly", description="Download highest-quality MP3 audio only."),
) -> DownloadResponse:
    download_request = build_download_request(url, audio_only=audio_only)
    return run_download(download_request)


@app.post("/", response_model=DownloadResponse)
def create_download(
    url: str = Query(..., min_length=1, description="Video or playlist URL to download."),
    audio_only: bool = Query(False, alias="audioOnly", description="Download highest-quality MP3 audio only."),
    options: DownloadOptions | None = Body(default=None),
) -> DownloadResponse:
    download_request = build_download_request(url, options, audio_only=audio_only)
    return run_download(download_request)


@app.get("/download")
def download_file_with_defaults(
    url: str = Query(..., min_length=1, description="Video or playlist URL to download."),
    audio_only: bool = Query(False, alias="audioOnly", description="Download highest-quality MP3 audio only."),
) -> FileResponse:
    download_request = build_download_request(url, audio_only=audio_only)
    return run_file_download(download_request)


@app.post("/download")
def download_file(
    url: str = Query(..., min_length=1, description="Video or playlist URL to download."),
    audio_only: bool = Query(False, alias="audioOnly", description="Download highest-quality MP3 audio only."),
    options: DownloadOptions | None = Body(default=None),
) -> FileResponse:
    download_request = build_download_request(url, options, audio_only=audio_only)
    return run_file_download(download_request)


def run_download(download_request: DownloadRequest) -> DownloadResponse:
    validate_dependencies(download_request)
    started_at = utc_now()

    try:
        file_path = download_from_request(download_request)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc

    return DownloadResponse(
        status=DownloadStatus.COMPLETED,
        request=download_request,
        file_path=str(file_path),
        filename=file_path.name,
        started_at=started_at,
        finished_at=utc_now(),
    )


def run_file_download(download_request: DownloadRequest) -> FileResponse:
    if download_request.playlist:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="/download returns a single file, so playlist downloads are not supported.",
        )

    validate_dependencies(download_request)
    temp_dir = Path(tempfile.mkdtemp(prefix="youtube-download-"))

    try:
        file_path = download_from_request(download_request, output_dir=temp_dir)
    except HTTPException:
        cleanup_temp_dir(temp_dir)
        raise
    except Exception as exc:
        cleanup_temp_dir(temp_dir)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc

    return FileResponse(
        file_path,
        filename=file_path.name,
        background=BackgroundTask(cleanup_temp_dir, temp_dir),
    )


def cleanup_temp_dir(temp_dir: Path) -> None:
    shutil.rmtree(temp_dir, ignore_errors=True)


def enum_values(enum_type: type[Enum]) -> list[Any]:
    return [value.value for value in enum_type]


def build_download_request(
    url: str,
    options: DownloadOptions | None = None,
    audio_only: bool = False,
) -> DownloadRequest:
    option_values = options.model_dump(exclude_unset=True, exclude_none=True) if options else {}
    if audio_only:
        option_values.update(
            {
                "media_type": MediaType.AUDIO,
                "audio_only_format": AudioOnlyFormat.MP3,
                "audio_quality": AudioQuality.BEST,
            }
        )

    try:
        return DownloadRequest(url=url, **option_values)
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=exc.errors(include_context=False),
        ) from exc


def utc_now() -> datetime:
    return datetime.now(UTC)


def validate_dependencies(download_request: DownloadRequest) -> None:
    if yt_dlp is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="yt-dlp is not installed. Install it with: python3 -m pip install yt-dlp",
        )

    if shutil.which("ffmpeg") is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="FFmpeg is required for MP3 conversion and MP4 merging.",
        )

    if (
        download_request.media_type == MediaType.AUDIO
        and download_request.audio_metadata
        and download_request.audio_metadata.has_values()
        and download_request.audio_only_format == AudioOnlyFormat.MP3
        and eyed3 is None
    ):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="eyed3 is not installed. Install it with: python3 -m pip install eyed3",
        )


def download_from_request(download_request: DownloadRequest, output_dir: Path | None = None) -> Path:
    if download_request.media_type == MediaType.AUDIO:
        return download_audio(download_request, output_dir=output_dir)
    return download_video(download_request, output_dir=output_dir)


def download_audio(download_request: DownloadRequest, output_dir: Path | None = None) -> Path:
    output_dir = output_dir or AUDIO_DIR
    output_dir.mkdir(exist_ok=True)
    audio_format = download_request.audio_only_format.value
    ydl_options = {
        "format": "bestaudio/best",
        "outtmpl": str(output_dir / "%(title)s.%(ext)s"),
        "noplaylist": not download_request.playlist,
        "quiet": True,
        "no_warnings": True,
        "overwrites": download_request.overwrite,
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": audio_format,
                "preferredquality": get_audio_quality_value(download_request.audio_quality),
            }
        ],
    }

    file_path = run_yt_dlp(download_request.url, ydl_options, final_extension=audio_format)
    if download_request.audio_only_format == AudioOnlyFormat.MP3:
        apply_audio_metadata(file_path, download_request.audio_metadata)
    return file_path


def download_video(download_request: DownloadRequest, output_dir: Path | None = None) -> Path:
    output_dir = output_dir or VIDEO_DIR
    output_dir.mkdir(exist_ok=True)
    ydl_options = {
        "format": build_video_format(download_request),
        "outtmpl": str(output_dir / "%(title)s.%(ext)s"),
        "merge_output_format": download_request.container.value,
        "noplaylist": not download_request.playlist,
        "quiet": True,
        "no_warnings": True,
        "overwrites": download_request.overwrite,
    }

    return run_yt_dlp(download_request.url, ydl_options)


def build_video_format(download_request: DownloadRequest) -> str:
    video_filters = build_video_filters(download_request)
    audio_selector = build_video_audio_selector(download_request.container)

    return f"bestvideo{video_filters}+{audio_selector}/best{video_filters}"


def build_video_filters(download_request: DownloadRequest) -> str:
    filters = []
    video_ext = get_video_stream_extension(download_request.container, download_request.video_codec)
    if video_ext:
        filters.append(f"ext={video_ext}")

    codec_filter = VIDEO_CODEC_FILTERS[download_request.video_codec]
    if codec_filter:
        filters.append(codec_filter.strip("[]"))

    max_height = VIDEO_QUALITY_HEIGHTS[download_request.video_quality]
    if max_height is not None:
        filters.append(f"height<={max_height}")

    if download_request.max_fps is not None:
        filters.append(f"fps<={int(download_request.max_fps)}")

    return "".join(f"[{filter_value}]" for filter_value in filters)


def get_video_stream_extension(container: VideoContainer, video_codec: VideoCodec) -> str | None:
    if container == VideoContainer.MP4:
        return "mp4"

    if container == VideoContainer.WEBM:
        return "webm"

    if video_codec == VideoCodec.H264:
        return "mp4"

    if video_codec in {VideoCodec.AV1, VideoCodec.VP9}:
        return "webm"

    return None


def build_video_audio_selector(container: VideoContainer) -> str:
    if container == VideoContainer.WEBM:
        return "bestaudio[ext=webm]/bestaudio"

    if container == VideoContainer.MP4:
        return "bestaudio[ext=m4a]"

    return "bestaudio"


def get_audio_quality_value(audio_quality: AudioQuality) -> str:
    if audio_quality == AudioQuality.BEST:
        return "0"
    return audio_quality.value.removesuffix("k")


def run_yt_dlp(url: str, ydl_options: dict[str, Any], final_extension: str | None = None) -> Path:
    if yt_dlp is None:
        raise RuntimeError("yt-dlp is not installed. Install it with: python3 -m pip install yt-dlp")

    with yt_dlp.YoutubeDL(ydl_options) as ydl:
        info = ydl.extract_info(url, download=True)
        if info is None:
            raise RuntimeError("yt-dlp did not return any download information.")

        if "entries" in info:
            entries = [entry for entry in info["entries"] if entry]
            if not entries:
                raise RuntimeError("No downloadable videos were found at that URL.")
            info = entries[0]

        requested_downloads = info.get("requested_downloads") or []
        file_path = None
        if requested_downloads:
            file_path = requested_downloads[-1].get("filepath") or requested_downloads[-1].get("filename")

        if file_path is None:
            file_path = ydl.prepare_filename(info)

    file_path = Path(file_path)
    if final_extension:
        file_path = file_path.with_suffix(f".{final_extension}")
    return file_path


def apply_audio_metadata(file_path: Path, metadata: AudioMetadata | None) -> None:
    if metadata is None or not metadata.has_values():
        return

    if eyed3 is None:
        raise RuntimeError("eyed3 is not installed. Install it with: python3 -m pip install eyed3")

    audio_file = eyed3.load(str(file_path))
    if audio_file is None:
        raise RuntimeError(f"Failed to load MP3 file: {file_path}")

    if audio_file.tag is None:
        audio_file.initTag()

    if metadata.title:
        audio_file.tag.title = metadata.title
    if metadata.artist:
        audio_file.tag.artist = metadata.artist
    if metadata.album:
        audio_file.tag.album = metadata.album
    if metadata.track_number is not None:
        audio_file.tag.track_num = metadata.track_number
    if metadata.genre:
        audio_file.tag.genre = metadata.genre

    audio_file.tag.save()


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=7000)
