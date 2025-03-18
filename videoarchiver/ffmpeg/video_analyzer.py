"""Video analysis functionality for FFmpeg"""

import os
import subprocess
import logging
from pathlib import Path
from typing import Dict, Any, Optional
from contextlib import contextmanager
import tempfile
import shutil
import json
from security import safe_command

logger = logging.getLogger("VideoArchiver")

@contextmanager
def temp_path_context():
    """Context manager for temporary path creation and cleanup"""
    temp_dir = tempfile.mkdtemp(prefix="ffmpeg_")
    try:
        os.chmod(temp_dir, 0o777)
        yield temp_dir
    finally:
        try:
            shutil.rmtree(temp_dir, ignore_errors=True)
        except Exception as e:
            logger.error(f"Error cleaning up temp directory {temp_dir}: {e}")

class VideoAnalyzer:
    def __init__(self, ffmpeg_path: Path):
        """Initialize video analyzer with FFmpeg path
        
        Args:
            ffmpeg_path: Path to FFmpeg binary
        """
        self.ffmpeg_path = Path(ffmpeg_path)
        self.ffprobe_path = self.ffmpeg_path.parent / (
            "ffprobe.exe" if os.name == "nt" else "ffprobe"
        )
        
        # Verify paths exist
        if not self.ffmpeg_path.exists():
            raise FileNotFoundError(f"FFmpeg not found at {self.ffmpeg_path}")
        if not self.ffprobe_path.exists():
            raise FileNotFoundError(f"FFprobe not found at {self.ffprobe_path}")
            
        logger.info(f"Initialized VideoAnalyzer with FFmpeg: {self.ffmpeg_path}, FFprobe: {self.ffprobe_path}")

    def analyze_video(self, input_path: str) -> Dict[str, Any]:
        """Analyze video content for optimal encoding settings"""
        try:
            if not os.path.exists(input_path):
                logger.error(f"Input file not found: {input_path}")
                return {}

            # Use ffprobe to get video information
            probe_result = self._probe_video(input_path)
            if not probe_result:
                logger.error("Failed to probe video")
                return {}

            # Get video stream info
            video_info = next(
                (s for s in probe_result["streams"] if s["codec_type"] == "video"),
                None
            )
            if not video_info:
                logger.error("No video stream found")
                return {}

            # Get video properties with validation
            try:
                width = int(video_info.get("width", 0))
                height = int(video_info.get("height", 0))
                fps = self._parse_frame_rate(video_info.get("r_frame_rate", "30/1"))
                duration = float(probe_result["format"].get("duration", 0))
                bitrate = float(probe_result["format"].get("bit_rate", 0))
            except (ValueError, ZeroDivisionError) as e:
                logger.error(f"Error parsing video properties: {e}")
                return {}

            # Advanced analysis with progress logging
            logger.info("Starting motion detection analysis...")
            has_high_motion = self._detect_high_motion(video_info)
            
            logger.info("Starting dark scene analysis...")
            has_dark_scenes = self._analyze_dark_scenes(input_path)

            # Get audio properties
            audio_info = next(
                (s for s in probe_result["streams"] if s["codec_type"] == "audio"),
                None
            )
            audio_props = self._get_audio_properties(audio_info)

            result = {
                "width": width,
                "height": height,
                "fps": fps,
                "duration": duration,
                "bitrate": bitrate,
                "has_high_motion": has_high_motion,
                "has_dark_scenes": has_dark_scenes,
                "has_complex_scenes": self._detect_complex_scenes(video_info),
                **audio_props
            }
            
            logger.info(f"Video analysis complete: {result}")
            return result

        except Exception as e:
            logger.error(f"Error analyzing video: {str(e)}")
            return {}

    def _probe_video(self, input_path: str) -> Dict:
        """Use ffprobe to get video information"""
        try:
            cmd = [
                str(self.ffprobe_path),
                "-v", "quiet",
                "-print_format", "json",
                "-show_format",
                "-show_streams",
                "-show_frames",
                "-read_intervals", "%+#10",  # Only analyze first 10 frames for speed
                input_path
            ]
            
            logger.debug(f"Running ffprobe command: {' '.join(cmd)}")
            result = safe_command.run(subprocess.run, cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=30  # Add timeout
            )
            
            if result.returncode == 0:
                try:
                    return json.loads(result.stdout)
                except json.JSONDecodeError as e:
                    logger.error(f"Failed to parse ffprobe output: {e}")
            else:
                logger.error(f"FFprobe failed: {result.stderr}")
                
        except subprocess.TimeoutExpired:
            logger.error("FFprobe timed out")
        except Exception as e:
            logger.error(f"Error probing video: {str(e)}")
        return {}

    def _parse_frame_rate(self, rate_str: str) -> float:
        """Parse frame rate string to float"""
        try:
            if "/" in rate_str:
                num, den = map(float, rate_str.split("/"))
                return num / den if den != 0 else 0
            return float(rate_str)
        except (ValueError, ZeroDivisionError):
            return 30.0  # Default to 30fps

    def _detect_high_motion(self, video_info: Dict) -> bool:
        """Detect high motion content based on frame rate and codec parameters"""
        try:
            # Check frame rate variation
            if video_info.get("avg_frame_rate") and video_info.get("r_frame_rate"):
                avg_fps = self._parse_frame_rate(video_info["avg_frame_rate"])
                fps = self._parse_frame_rate(video_info["r_frame_rate"])
                if abs(avg_fps - fps) > 5:  # Significant frame rate variation
                    return True

            # Check codec parameters for motion indicators
            if "codec_tag_string" in video_info:
                high_motion_codecs = ["avc1", "h264", "hevc"]
                if any(codec in video_info["codec_tag_string"].lower() for codec in high_motion_codecs):
                    return True

        except Exception as e:
            logger.warning(f"Frame rate analysis failed: {str(e)}")
        return False

    def _analyze_dark_scenes(self, input_path: str) -> bool:
        """Analyze video for dark scenes using FFmpeg signalstats filter"""
        try:
            with temp_path_context() as temp_dir:
                sample_cmd = [
                    str(self.ffmpeg_path),
                    "-i", input_path,
                    "-vf", "select='eq(pict_type,I)',signalstats",
                    "-show_entries", "frame_tags=lavfi.signalstats.YAVG",
                    "-f", "null",
                    "-t", "30",  # Only analyze first 30 seconds
                    "-"
                ]
                
                logger.debug(f"Running dark scene analysis: {' '.join(sample_cmd)}")
                result = safe_command.run(subprocess.run, sample_cmd,
                    capture_output=True,
                    text=True,
                    timeout=60  # Add timeout
                )

                dark_frames = 0
                total_frames = 0
                for line in result.stderr.split("\n"):
                    if "YAVG" in line:
                        try:
                            avg_brightness = float(line.split("=")[1])
                            if avg_brightness < 40:  # Dark scene threshold
                                dark_frames += 1
                            total_frames += 1
                        except (ValueError, IndexError):
                            continue

                return total_frames > 0 and (dark_frames / total_frames) > 0.2

        except subprocess.TimeoutExpired:
            logger.warning("Dark scene analysis timed out")
        except Exception as e:
            logger.warning(f"Dark scene analysis failed: {str(e)}")
        return False

    def _detect_complex_scenes(self, video_info: Dict) -> bool:
        """Detect complex scenes based on codec parameters and bitrate"""
        try:
            # Check for high profile/level
            profile = video_info.get("profile", "").lower()
            level = video_info.get("level", -1)
            
            if "high" in profile or level >= 41:  # Level 4.1 or higher
                return True
                
            # Check for high bitrate
            if "bit_rate" in video_info:
                bitrate = int(video_info["bit_rate"])
                if bitrate > 4000000:  # Higher than 4Mbps
                    return True
                    
        except Exception as e:
            logger.warning(f"Complex scene detection failed: {str(e)}")
        return False

    def _get_audio_properties(self, audio_info: Optional[Dict]) -> Dict[str, Any]:
        """Extract audio properties from stream info"""
        if not audio_info:
            return {
                "audio_bitrate": 128000,  # Default to 128kbps
                "audio_channels": 2,
                "audio_sample_rate": 48000
            }

        try:
            return {
                "audio_bitrate": int(audio_info.get("bit_rate", 128000)),
                "audio_channels": int(audio_info.get("channels", 2)),
                "audio_sample_rate": int(audio_info.get("sample_rate", 48000))
            }
        except (ValueError, TypeError) as e:
            logger.warning(f"Error parsing audio properties: {e}")
            return {
                "audio_bitrate": 128000,
                "audio_channels": 2,
                "audio_sample_rate": 48000
            }
