"""FFmpeg module initialization"""

import logging
import sys
import os
from pathlib import Path
from typing import Dict, Any, Optional
from security import safe_command

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)

logger = logging.getLogger("VideoArchiver")

# Import components after logging is configured
#try:
    # Try relative imports first
from ffmpeg_manager import FFmpegManager
from video_analyzer import VideoAnalyzer
from gpu_detector import GPUDetector
from encoder_params import EncoderParams
from ffmpeg_downloader import FFmpegDownloader
from exceptions import (
    FFmpegError,
    DownloadError,
    VerificationError,
    EncodingError,
    AnalysisError,
    GPUError,
    HardwareAccelerationError,
    FFmpegNotFoundError,
    FFprobeError,
    CompressionError,
    FormatError,
    PermissionError,
    TimeoutError,
    ResourceError,
    QualityError,
    AudioError,
    BitrateError,
)
#except ImportError:
    # Fall back to absolute imports if relative imports fail
    # from videoarchiver.ffmpeg.ffmpeg_manager import FFmpegManager
    # from videoarchiver.ffmpeg.video_analyzer import VideoAnalyzer
    # from videoarchiver.ffmpeg.gpu_detector import GPUDetector
    # from videoarchiver.ffmpeg.encoder_params import EncoderParams
    # from videoarchiver.ffmpeg.ffmpeg_downloader import FFmpegDownloader
    # from videoarchiver.ffmpeg.exceptions import (
    #     FFmpegError,
    #     DownloadError,
    #     VerificationError,
    #     EncodingError,
    #     AnalysisError,
    #     GPUError,
    #     HardwareAccelerationError,
    #     FFmpegNotFoundError,
    #     FFprobeError,
    #     CompressionError,
    #     FormatError,
    #     PermissionError,
    #     TimeoutError,
    #     ResourceError,
    #     QualityError,
    #     AudioError,
    #     BitrateError,
    # )


class FFmpeg:
    """Main FFmpeg interface"""

    _instance = None

    def __new__(cls, base_dir: Optional[Path] = None):
        """Singleton pattern to ensure only one FFmpeg instance"""
        if cls._instance is None:
            cls._instance = super(FFmpeg, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self, base_dir: Optional[Path] = None):
        """Initialize FFmpeg interface
        
        Args:
            base_dir: Optional base directory for FFmpeg files. If not provided,
                     will use the default directory in the module.
        """
        # Skip initialization if already done
        if self._initialized:
            return

        try:
            self._manager = FFmpegManager()
            logger.info(f"FFmpeg initialized at {self._manager.get_ffmpeg_path()}")
            logger.info(f"GPU support: {self._manager.gpu_info}")
            self._initialized = True
        except Exception as e:
            logger.error(f"Failed to initialize FFmpeg interface: {e}")
            raise FFmpegError(f"FFmpeg initialization failed: {e}")

    @property
    def ffmpeg_path(self) -> Path:
        """Get path to FFmpeg binary"""
        return Path(self._manager.get_ffmpeg_path())

    @property
    def gpu_info(self) -> Dict[str, bool]:
        """Get GPU information"""
        return self._manager.gpu_info

    def analyze_video(self, input_path: str) -> Dict[str, Any]:
        """Analyze video content
        
        Args:
            input_path: Path to input video file
            
        Returns:
            Dict containing video analysis results
        """
        try:
            if not os.path.exists(input_path):
                raise FileNotFoundError(f"Input file not found: {input_path}")
            return self._manager.analyze_video(input_path)
        except Exception as e:
            logger.error(f"Video analysis failed: {e}")
            raise AnalysisError(f"Failed to analyze video: {e}")

    def get_compression_params(self, input_path: str, target_size_mb: int) -> Dict[str, str]:
        """Get optimal compression parameters
        
        Args:
            input_path: Path to input video file
            target_size_mb: Target file size in megabytes
            
        Returns:
            Dict containing FFmpeg encoding parameters
        """
        try:
            if not os.path.exists(input_path):
                raise FileNotFoundError(f"Input file not found: {input_path}")
            return self._manager.get_compression_params(input_path, target_size_mb)
        except Exception as e:
            logger.error(f"Failed to get compression parameters: {e}")
            raise EncodingError(f"Failed to get compression parameters: {e}")

    def force_download(self) -> bool:
        """Force re-download of FFmpeg binary
        
        Returns:
            bool: True if download successful, False otherwise
        """
        try:
            return self._manager.force_download()
        except Exception as e:
            logger.error(f"Force download failed: {e}")
            raise DownloadError(f"Failed to force download FFmpeg: {e}")

    @property
    def version(self) -> str:
        """Get FFmpeg version"""
        try:
            import subprocess
            result = safe_command.run(subprocess.run, [str(self.ffmpeg_path), "-version"],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0:
                return result.stdout.split()[2]
            raise FFmpegError(f"FFmpeg version check failed: {result.stderr}")
        except Exception as e:
            logger.error(f"Failed to get FFmpeg version: {e}")
            raise FFmpegError(f"Failed to get FFmpeg version: {e}")


# Initialize default instance
try:
    ffmpeg = FFmpeg()
    logger.info(f"Default FFmpeg instance initialized (version {ffmpeg.version})")
except Exception as e:
    logger.error(f"Failed to initialize default FFmpeg instance: {e}")
    ffmpeg = None


__all__ = [
    'FFmpeg',
    'ffmpeg',
    'FFmpegManager',
    'VideoAnalyzer',
    'GPUDetector',
    'EncoderParams',
    'FFmpegDownloader',
    'FFmpegError',
    'DownloadError',
    'VerificationError',
    'EncodingError',
    'AnalysisError',
    'GPUError',
    'HardwareAccelerationError',
    'FFmpegNotFoundError',
    'FFprobeError',
    'CompressionError',
    'FormatError',
    'PermissionError',
    'TimeoutError',
    'ResourceError',
    'QualityError',
    'AudioError',
    'BitrateError',
]
