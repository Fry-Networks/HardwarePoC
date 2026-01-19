"""Decibel (audio level) measurement collection for IDM/ODM.

Samples audio input device and calculates dBFS.
Maintains persistent microphone stream to avoid tray icon blinking.
Transferred from GUI worker.py to autonomous service.
"""

import os
import json
import logging
import math
try:
    import numpy as np  # type: ignore[import-not-found]
except Exception:
    np = None  # type: ignore
import threading
import time
from typing import Dict, Any, Optional
from collections import deque

try:
    import sounddevice as sd  # type: ignore
except ImportError:
    sd = None  # type: ignore

log = logging.getLogger("measurements.decibel")

# Persistent audio stream and buffer
_audio_stream = None
_audio_buffer = deque(maxlen=44100)  # 1 second at 44.1kHz
_stream_lock = threading.Lock()
_stream_running = False
_last_dbfs = None
_stream_thread = None


def _audio_callback(indata, frames, time_info, status):
    """Callback for continuous audio stream."""
    global _last_dbfs
    try:
        if status:
            log.debug("Audio stream status: %s", status)
        
        # Add samples to circular buffer
        audio_data = indata[:, 0]  # Get first channel
        _audio_buffer.extend(audio_data)
        
        # Calculate rolling RMS if buffer is full
        if len(_audio_buffer) == _audio_buffer.maxlen:
            # Prefer numpy if available for performance; fall back to pure Python math
            try:
                if np is not None:
                    arr = np.array(_audio_buffer)
                    rms = float(np.sqrt(np.mean(arr ** 2)))
                else:
                    ssum = 0.0
                    for v in _audio_buffer:
                        ssum += v * v
                    rms = math.sqrt(ssum / len(_audio_buffer)) if len(_audio_buffer) else 0.0
            except Exception:
                rms = 0.0

            if rms > 0:
                _last_dbfs = max(-96.0, min(0.0, 20 * math.log10(rms)))
            else:
                _last_dbfs = -96.0
    except Exception as e:
        log.debug("Audio callback error: %s", e)


def _start_persistent_stream(device_idx: int, samplerate: int = 44100) -> bool:
    """Start a persistent audio stream in background thread.
    
    Keeps microphone open to avoid OS tray icon blinking.
    Uses callback to continuously update audio levels.
    """
    global _audio_stream, _stream_running, _stream_thread
    
    if not sd:
        return False
    
    with _stream_lock:
        # Stream already running
        if _stream_running and _audio_stream:
            return True
        
        try:
            _audio_stream = sd.InputStream(
                device=device_idx,
                samplerate=samplerate,
                channels=1,
                blocksize=2048,
                callback=_audio_callback,
                dtype='float32',
                latency='low'
            )
            _audio_stream.start()
            _stream_running = True
            log.info("Started persistent audio stream on device %d", device_idx)
            return True
        except Exception as e:
            log.error("Failed to start audio stream: %s", e)
            _audio_stream = None
            _stream_running = False
            return False


def _stop_persistent_stream() -> None:
    """Stop the persistent audio stream."""
    global _audio_stream, _stream_running
    
    with _stream_lock:
        if _audio_stream:
            try:
                _audio_stream.stop()
                _audio_stream.close()
                log.info("Stopped persistent audio stream")
            except Exception as e:
                log.debug("Error closing audio stream: %s", e)
            finally:
                _audio_stream = None
                _stream_running = False


def collect_decibel_measurement() -> Optional[Dict[str, Any]]:
    """Collect audio level measurement in dBFS.
    
    Uses persistent audio stream to avoid microphone tray icon blinking.
    
    Returns:
        Dict with dbfs or None on failure
    """
    global _last_dbfs
    
    try:
        if not sd:
            log.warning("sounddevice not available for audio sampling")
            return None
        
        # Get audio device from config
        device_idx = _get_audio_device_config()
        
        if device_idx is None:
            log.debug("No audio device configured")
            return None
        
        # Start persistent stream if not already running
        if not _start_persistent_stream(device_idx):
            return None
        
        # Return last measured dBFS value
        if _last_dbfs is not None:
            return {
                "dbfs": round(_last_dbfs, 2)
            }
        
        # Stream just started; no data yet
        return None
        
    except Exception as e:
        log.error("Decibel measurement failed: %s", e)
        return None


def _get_audio_device_config() -> Optional[int]:
    """Get audio device index from config file."""
    try:
        from pathlib import Path
        
        # Read from device_config.json (written by GUI)
        config_path = Path(os.environ.get("PROGRAMDATA", "C:\\ProgramData")) / "FryNetworks" / "config" / "device_config.json"
        
        if config_path.exists():
            with open(config_path, 'r') as f:
                config = json.load(f)
                device_idx = config.get("audio_device_idx")
                if device_idx is not None:
                    return int(device_idx)
        
        # Try to find default input device
        if sd:
            try:
                default_device = sd.default.device[0]  # input device
                return default_device
            except Exception:
                pass
        
        return None
        
    except Exception as e:
        log.warning("Failed to read audio device config: %s", e)
        return None
