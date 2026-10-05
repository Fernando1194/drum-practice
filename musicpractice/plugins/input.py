"""Get audio from a URL with yt-dlp (YouTube, SoundCloud, Bandcamp, Vimeo, ...).

Not supported: Spotify, Apple Music, Deezer, Tidal, Amazon Music. Their streams are
DRM-protected, so no downloader can get the audio. We fail fast with a clear message.

When a YouTube download suddenly breaks, the fix is almost always:
    pip install -U yt-dlp
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from urllib.parse import urlparse

from ..core.models import Song
from ..core.workspace import Workspace

DRM_HOSTS = ("spotify.com", "music.apple.com", "deezer.com", "tidal.com", "music.amazon.")
MAX_DURATION_S = 20 * 60  # refuse 2-hour mixes and livestreams


class DownloadError(RuntimeError):
    pass


def _check_url(url: str, allow_file_urls: bool) -> None:
    parsed = urlparse(url.strip())
    if parsed.scheme == "file" and allow_file_urls:
        return
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise DownloadError(f"Not a valid web URL: {url!r}")
    host = parsed.netloc.lower()
    if any(h in host for h in DRM_HOSTS):
        raise DownloadError(
            f"{host} streams DRM-protected audio, which can't be downloaded. "
            "Search the song on YouTube or SoundCloud and paste that link, or upload a file.")


def import_url(ws: Workspace, url: str, allow_file_urls: bool = False) -> Song:
    """Download the audio behind `url` as WAV and register it as a Song (cached by video id)."""
    try:
        from yt_dlp import YoutubeDL
        from yt_dlp.utils import DownloadError as YtDlpError
    except ImportError as e:
        raise DownloadError("yt-dlp is not installed: pip install yt-dlp") from e

    _check_url(url, allow_file_urls)
    base_opts = {"quiet": True, "no_warnings": True, "noprogress": True, "noplaylist": True,
                 "enable_file_urls": allow_file_urls}

    try:
        with YoutubeDL(base_opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except YtDlpError as e:
        raise DownloadError(f"Could not read that link. If it is YouTube, try "
                            f"'pip install -U yt-dlp'.\n{e}") from e
    if info.get("_type") == "playlist":
        raise DownloadError("That link is a playlist. Paste the link of a single song.")
    if info.get("is_live"):
        raise DownloadError("Live streams are not supported.")
    if (info.get("duration") or 0) > MAX_DURATION_S:
        raise DownloadError(f"Audio is {info['duration'] / 60:.0f} min long; limit is "
                            f"{MAX_DURATION_S // 60} min.")

    key = f"{info.get('extractor_key', 'web')}:{info.get('id', url)}"
    song_id = hashlib.sha256(key.encode()).hexdigest()[:16]
    existing = ws.get(song_id)
    if existing is not None and existing.has("audio:mix"):
        return existing

    song_dir = ws.song_dir(song_id)
    song_dir.mkdir(parents=True, exist_ok=True)
    opts = {**base_opts,
            "format": "bestaudio/best",
            "outtmpl": str(song_dir / "original.%(ext)s"),
            "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "wav"}]}
    try:
        with YoutubeDL(opts) as ydl:
            ydl.download([url])
    except YtDlpError as e:
        raise DownloadError(f"Download failed (is ffmpeg installed?).\n{e}") from e

    wav = song_dir / "original.wav"
    if not wav.exists():
        raise DownloadError("Download finished but no audio file was produced.")
    if existing is not None:                  # audio was cleared to save space: it's back
        from ..core.storage import restored
        return restored(existing, wav)
    title = info.get("track") or info.get("title") or "Untitled"
    artist = info.get("artist") or info.get("uploader") or ""
    return ws.create_song(song_id, f"{artist} - {title}" if artist else title, wav,
                          extra={"source_url": url})
