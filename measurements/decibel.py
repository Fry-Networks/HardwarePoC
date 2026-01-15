"""Decibel (audio level) measurement collection for IDM/ODM.

Samples audio input device and calculates dBFS.
Transferred from GUI worker.py to autonomous service.
"""

import os
import json
import logging
import numpy as np
from typing import Dict, Any, Optional

try:
    import sounddevice as sd  # type: ignore
except ImportError:
    sd = None  # type: ignore

log = logging.getLogger("measurements.decibel")


def collect_decibel_measurement() -> Optional[Dict[str, Any]]:
    """Collect audio level measurement in dBFS.
    
    Returns:
        Dict with dbfs or None on failure
    """
    try:
        if not sd:
            log.warning("sounddevice not available for audio sampling")
            return None
        
        # Get audio device from config
        device_idx = _get_audio_device_config()
        
        if device_idx is None:
            log.debug("No audio device configured")
            return None
        
        # Sample audio
        dbfs = _sample_audio_dbfs(device_idx)
        
        if dbfs is None:
            return None
        
        return {
            "dbfs": round(dbfs, 2)
        }
        
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


def _sample_audio_dbfs(device_idx: int, duration: float = 1.0, samplerate: int = 44100) -> Optional[float]:
    """Sample audio and calculate dBFS (decibels relative to full scale).
    
    Args:
        device_idx: Audio input device index
        duration: Recording duration in seconds
        samplerate: Sample rate in Hz
    
    Returns:
        dBFS value or None on failure
    """
    if not sd:
        return None
    
    try:
        # Record audio
        recording = sd.rec(
            int(duration * samplerate),
            samplerate=samplerate,
            channels=1,
            device=device_idx,
            dtype='float32'
        )
        sd.wait()  # Wait for recording to complete
        
        # Calculate RMS (root mean square)
        rms = np.sqrt(np.mean(recording ** 2))
        
        if rms == 0:
            return -96.0  # Silence
        
        # Convert to dBFS
        # Full scale is 1.0, so dBFS = 20 * log10(rms / 1.0)
        dbfs = 20 * np.log10(rms)
        
        # Clamp to reasonable range
        dbfs = max(-96.0, min(0.0, dbfs))
        
        return float(dbfs)
        
    except Exception as e:
        log.warning("Audio sampling failed: %s", e)
        return None
