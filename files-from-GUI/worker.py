"""Data sampling worker thread for live mining data collection."""

import os
import csv
from datetime import datetime, timezone
import time
from typing import Optional

from PySide6 import QtCore

from miner_GUI.utils.data import data_dir_gui, log_step

# Import hardware capability flags with fallbacks
try:
    from miner_GUI.config import HAVE_SD, HAVE_SERIAL
except ImportError:
    # Fallback: detect capabilities directly
    try:
        import sounddevice
        HAVE_SD = True
    except ImportError:
        HAVE_SD = False
    
    try:
        import serial
        HAVE_SERIAL = True
    except ImportError:
        HAVE_SERIAL = False

# Import encryption utilities for 10-minute uploads
# Note: Service now handles measurement writing autonomously
try:
    from utils.data import read_miner_key
    HAVE_ENCRYPTION = True
except ImportError:
    HAVE_ENCRYPTION = False

# Lightweight logging helper for diagnostics
try:
    from utils.data import log_step  # type: ignore
except Exception:
    def log_step(msg: str, data: Optional[dict] = None) -> None:  # type: ignore
        return


class Worker(QtCore.QThread):
    """Background worker thread for collecting live sensor data."""
    
    tick = QtCore.Signal(object)
    
    def __init__(self, group: str, selector: Optional[str], device_idx: Optional[int], baud: Optional[int]):
        super().__init__()
        self.group = group
        self.selector = selector
        self.device_idx = device_idx
        self.baud = baud
        self._stop = False
        # Initialize to -601 to trigger immediate first measurement write at startup
        self._last_encrypted_write = -601
        self._last_real_bw_result = None  # Store latest real bandwidth test result
    
    def stop(self) -> None:
        """Signal the worker to stop."""
        self._stop = True
    
    def run(self) -> None:
        """Main worker thread execution."""
        # For Decibel, keep a persistent audio stream to avoid device blink on each sample
        if self.group == "Decibel":
            try:
                self._run_decibel_stream(self.device_idx)
            except Exception:
                # Fall back to periodic sampling on error
                while not self._stop:
                    try:
                        data = self._sample_decibel(self.device_idx)
                    except Exception as ee:
                        data = {"err": str(ee)}
                    self.tick.emit(data)
                    self.msleep(2000)
            return
        
        # Standard periodic sampling for other sensor types
        # Initialize to -601 to trigger immediate first test and measurement at startup
        last_real_bw = -601
        tick_count = 0
        while not self._stop:
            try:
                if self.group in ("BM", "Bandwidth"):
                    data = self._sample_bandwidth(self.selector or "")
                elif self.group == "Satellite":
                    data = self._sample_gnss(self.selector or "", self.baud or 9600)
                elif self.group == "Radiation":
                    data = self._sample_geiger(self.selector or "", self.baud or 9600)
                elif self.group == "Decibel":
                    data = self._sample_decibel(self.device_idx)
                elif self.group == "AEM":
                    data = self._sample_aem()
                else:
                    data = {}
            except Exception as e:
                data = {"err": str(e)}

            # Real bandwidth test every 10 minutes for BM (bandwidth) group
            now_time = time.time()
            if self.group in ("BM", "Bandwidth") and now_time - last_real_bw > 600:
                bw_result = self._real_bandwidth_test()
                if 'err' not in bw_result:
                    # Store for encrypted measurement
                    self._last_real_bw_result = bw_result.copy()
                    # Log to daily CSV file
                    try:
                        now_dt = datetime.now()
                        log_dir = data_dir_gui() / "logs"
                        os.makedirs(log_dir, exist_ok=True)
                        date_str = now_dt.strftime('%Y%m%d')
                        dev = self.group.lower()
                        log_file = log_dir / f"{dev}_realtest_{date_str}.csv"
                        # Write header if file does not exist
                        write_header = not log_file.exists()
                        with open(log_file, 'a', newline='') as f:
                            writer = csv.writer(f)
                            if write_header:
                                writer.writerow(['timestamp'] + list(bw_result.keys()))
                            # Row: timestamp + all values
                            writer.writerow([now_dt.isoformat()] + [bw_result.get(k, '') for k in bw_result.keys()])
                    except Exception as exc:
                        try:
                            log_step(
                                "csv_log_write_failed_realtest",
                                {
                                    "group": self.group,
                                    "log_file": log_file if "log_file" in locals() else None,
                                    "error": str(exc),
                                },
                            )
                        except Exception:
                            pass
                last_real_bw = now_time

            tick_count += 1
            # Minimal heartbeat injection for diagnostics every 30 ticks
            if tick_count % 30 == 0:
                try:
                    data["_hb"] = str(tick_count)
                except Exception:
                    pass
            
            # Note: Measurement writing is now handled autonomously by the service.
            
            self.tick.emit(data)
            # Use a slower refresh for Geiger (Radiation) and Satellite to reduce CPU load
            # Break long sleeps into smaller chunks to check _stop flag more frequently
            if self.group == "Radiation" or self.group == "Satellite":
                # 10 seconds total, but check every 500ms
                for _ in range(20):
                    if self._stop:
                        break
                    self.msleep(500)
            else:
                # 2 seconds total, check every 500ms
                for _ in range(4):
                    if self._stop:
                        break
                    self.msleep(500)

    def _sample_aem(self) -> dict:
        """Read Proof of Installed (PoI) status for AEM from today's status JSON.

        Expects external service to update ProgramData/status/status-YYYYMMDD.json with
        a top-level key 'PoI' every ~10 minutes. Returns {'poi': value}.
        """
        try:
            from datetime import datetime as _dt
            import json as _json
            from pathlib import Path as _Path
            # Use same path logic as UI/utils
            try:
                from utils.data import date_to_cache_path  # type: ignore
            except Exception:
                # Fallback: reconstruct path
                from utils.data import data_dir_gui  # type: ignore
                today = _dt.now()
                p = data_dir_gui() / "status" / f"status-{today.strftime('%Y%m%d')}.json"
            else:
                p = date_to_cache_path(_dt.now())

            if not _Path(p).exists():
                return {"poi": None}
            with open(p, 'r', encoding='utf-8') as f:
                js = _json.load(f)
            # Accept 'PoI' in any case variants; prefer exact
            if 'PoI' in js:
                val = js['PoI']
            else:
                # Try lower/upper variants
                val = js.get('poi') if isinstance(js, dict) else None
                if val is None:
                    val = js.get('POI') if isinstance(js, dict) else None
            return {"poi": val}
        except Exception:
            return {"poi": None}

    def _sample_bandwidth(self, iface: str) -> dict:
        """Sample network bandwidth for given interface."""
        try:
            import psutil
            import time
            
            nic0 = psutil.net_io_counters(pernic=True).get(iface)
            if not nic0:
                return {"dl": 0.0, "ul": 0.0, "iface": iface}
            
            r0, t0 = nic0.bytes_recv, nic0.bytes_sent
            tstart = time.time()
            time.sleep(1.0)
            
            nic1 = psutil.net_io_counters(pernic=True).get(iface)
            if not nic1:
                return {"dl": 0.0, "ul": 0.0, "iface": iface}
            
            r1, t1 = nic1.bytes_recv, nic1.bytes_sent
            dt = max(1e-3, time.time() - tstart)
            
            dl_mbps = (max(0, r1 - r0) * 8.0 / 1e6) / dt
            ul_mbps = (max(0, t1 - t0) * 8.0 / 1e6) / dt
            
            return {"dl": round(dl_mbps, 2), "ul": round(ul_mbps, 2), "iface": iface}
        except ImportError:
            return {"err": "psutil not available"}

    def _sample_decibel(self, idx: Optional[int]) -> dict:
        """Sample audio decibel level from microphone."""
        if not HAVE_SD:
            return {"dbfs": None}
        
        try:
            import numpy as np
            import sounddevice as sd
            
            sr = 16000
            dur = 0.25
            
            audio = sd.rec(int(sr * dur), samplerate=sr, channels=1, dtype="float32", device=idx)
            sd.wait()
            
            rms = float(np.sqrt(np.mean(audio**2)) + 1e-12)
            dbfs = 20 * np.log10(rms)
            dbfs = max(-90.0, min(0.0, dbfs))
            
            return {"dbfs": round(dbfs, 1)}
        except Exception as e:
            return {"err": str(e)}

    def _run_decibel_stream(self, idx: Optional[int]) -> None:
        """Run continuous decibel monitoring with audio stream."""
        if not HAVE_SD:
            while not self._stop:
                self.tick.emit({"dbfs": None})
                self.msleep(1000)
            return
        
        try:
            import time as _t
            import numpy as np
            import sounddevice as sd
            
            sr = 16000
            win_sec = 0.25   # analysis window
            tick_sec = 1.0   # emit frequency
            chunk_sec = 0.05 # continuous read chunk
            win_frames = int(sr * win_sec)
            chunk_frames = max(1, int(sr * chunk_sec))
            
            with sd.InputStream(device=idx, channels=1, samplerate=sr, dtype="float32") as stream:
                # Rolling buffer of last window
                buf = np.zeros((win_frames, 1), dtype=np.float32)
                pos = 0
                filled = 0
                last_emit = _t.monotonic()
                
                while not self._stop:
                    try:
                        chunk, _ = stream.read(chunk_frames)
                        n = chunk.shape[0]
                        
                        # Write chunk into rolling buffer (circular)
                        if n <= win_frames:
                            end = pos + n
                            if end <= win_frames:
                                buf[pos:end, 0] = chunk[:, 0]
                            else:
                                first = win_frames - pos
                                buf[pos:win_frames, 0] = chunk[:first, 0]
                                buf[0:end - win_frames, 0] = chunk[first:, 0]
                            pos = end % win_frames
                            filled = min(win_frames, filled + n)
                        else:
                            # Oversized read; keep the last window
                            buf[:, 0] = chunk[-win_frames:, 0]
                            pos = 0
                            filled = win_frames

                        now = _t.monotonic()
                        if (now - last_emit) >= tick_sec and filled > 0:
                            # Compute RMS over the current window
                            if filled < win_frames:
                                window = buf[:filled, 0]
                            else:
                                window = np.concatenate((buf[pos:, 0], buf[:pos, 0]))
                            rms = float(np.sqrt(np.mean(window**2)) + 1e-12)
                            dbfs = 20 * np.log10(rms)
                            dbfs = max(-90.0, min(0.0, dbfs))
                            self.tick.emit({"dbfs": round(dbfs, 1)})
                            last_emit = now
                    except Exception as e:
                        self.tick.emit({"err": str(e)})
                        _t.sleep(0.05)
        except Exception:
            # Propagate to caller for fallback to non-stream method
            raise

    def _sample_gnss(self, port: str, baud: int) -> dict:
        """Sample GNSS data from serial port.
        Parses NMEA sentences (GGA, RMC) to extract satellites, fix quality, and coordinates.
        """
        if not HAVE_SERIAL:
            return {"err": "Serial support not available"}
        
        if not port or port == "":
            return {"err": "No port specified"}
        
        try:
            import serial
            import re
            
            # Open serial connection with timeout
            ser = serial.Serial(port, baud, timeout=1.0)
            
            # Read lines until we get a complete fix (try up to 20 lines)
            data = {}
            
            for _ in range(20):
                line = ser.readline()
                if not line:
                    break
                
                try:
                    line = line.decode('utf-8', errors='ignore').strip()
                except Exception:
                    continue
                
                if not line or not line.startswith('$'):
                    continue
                
                # Parse GGA sentence (satellites, fix quality, lat/lon, altitude)
                if line.startswith('$GPGGA') or line.startswith('$GNGGA'):
                    parts = line.split(',')
                    if len(parts) >= 9:
                        try:
                            # Fix quality: 0=invalid, 1=GPS, 2=DGPS, 3=PPS, etc.
                            fix_quality = int(parts[6]) if parts[6] else 0
                            fix_map = {0: 'NONE', 1: 'GPS', 2: 'DGPS', 3: 'PPS', 4: 'RTK', 5: 'FLOAT RTK'}
                            data['fix'] = fix_map.get(fix_quality, f'FIX{fix_quality}')
                            
                            # Number of satellites
                            num_sats = int(parts[7]) if parts[7] else None
                            data['sats'] = num_sats
                            
                            # Altitude
                            if len(parts) > 9 and parts[9]:
                                data['alt'] = float(parts[9])
                            
                            # Latitude
                            if len(parts) > 2 and parts[2] and parts[3]:
                                lat_str = parts[2]
                                lat_dir = parts[3]
                                if lat_str and len(lat_str) >= 5:
                                    lat_deg = float(lat_str[:2])
                                    lat_min = float(lat_str[2:])
                                    lat = lat_deg + lat_min / 60.0
                                    if lat_dir == 'S':
                                        lat = -lat
                                    data['lat'] = lat
                            
                            # Longitude
                            if len(parts) > 5 and parts[4] and parts[5]:
                                lon_str = parts[4]
                                lon_dir = parts[5]
                                if lon_str and len(lon_str) >= 6:
                                    lon_deg = float(lon_str[:3])
                                    lon_min = float(lon_str[3:])
                                    lon = lon_deg + lon_min / 60.0
                                    if lon_dir == 'W':
                                        lon = -lon
                                    data['lon'] = lon
                            
                            # HDOP
                            if len(parts) > 8 and parts[8]:
                                data['hdop'] = float(parts[8])
                        except (ValueError, IndexError):
                            pass
                
                # Parse RMC sentence (can also contain fix info)
                elif line.startswith('$GPRMC') or line.startswith('$GNRMC'):
                    parts = line.split(',')
                    if len(parts) >= 3:
                        try:
                            # Status: A=active, V=void
                            status = parts[2] if len(parts) > 2 else 'V'
                            if 'fix' not in data:
                                data['fix'] = 'GPS' if status == 'A' else 'NONE'
                        except Exception:
                            pass
            
            ser.close()
            
            # If we have at least sats or fix info, return it
            if data.get('sats') is not None or data.get('fix') is not None:
                return data
            else:
                return {"err": "No GNSS data received"}
        
        except ImportError:
            return {"err": "pyserial not installed"}
        except Exception as e:
            return {"err": f"GNSS read failed: {str(e)[:60]}"}

    def _sample_geiger(self, port: str, baud: int) -> dict:
        """Sample Geiger counter data from serial port.
        Supports:
        - GQ GMC-300S (binary protocol at 57600 baud)
        - Text-based Geiger counters (CPM/μSv/h formats)
        """
        if not HAVE_SERIAL:
            return {"err": "Serial support not available"}
        
        if not port or port == "":
            return {"err": "No port specified"}
        try:
            import serial, struct, re, time
        except ImportError:
            return {"err": "pyserial not installed"}

        # Defensive open with retries (driver instability can segfault abruptly)
        for attempt in range(2):
            try:
                ser = serial.Serial(port, baud, timeout=1.0)
                break
            except Exception as e:
                if attempt == 1:
                    return {"err": f"Open failed: {str(e)[:50]}"}
                time.sleep(0.15)
        else:
            return {"err": "Open loop failed"}

        data: dict = {}
        try:
            # Binary protocol branch
            if baud == 57600:
                try:
                    ser.reset_input_buffer()
                    ser.write(b"<GETCPM>>")
                    ser.flush()
                    time.sleep(0.08)
                    waiting = 0
                    try:
                        waiting = ser.in_waiting
                    except Exception:
                        waiting = 0
                    if waiting >= 2:
                        response = ser.read(2)
                        if len(response) == 2:
                            cpm = struct.unpack('>H', response)[0]
                            if 0 <= cpm <= 10000:
                                usv_hour = cpm * 0.0065
                                mr_hour = usv_hour * 0.1
                                data.update({
                                    'cpm': float(cpm),
                                    'usv': round(usv_hour, 4),
                                    'usv_hour': round(usv_hour, 4),
                                    'mr': round(mr_hour, 4)
                                })
                                # Optional CPS
                                try:
                                    ser.reset_input_buffer()
                                    ser.write(b"<GETCPS>>")
                                    ser.flush()
                                    time.sleep(0.12)
                                    if ser.in_waiting >= 2:
                                        cps_bytes = ser.read(2)
                                        if len(cps_bytes) == 2:
                                            cps = struct.unpack('>H', cps_bytes)[0]
                                            if 0 < cps < 200:
                                                data['cps'] = float(cps)
                                except Exception:
                                    pass
                                return data
                except Exception:
                    # Fallthrough to text parsing
                    pass

            # Text parsing branch
            try:
                ser.reset_input_buffer()
            except Exception:
                pass
            for _ in range(8):  # fewer lines for speed
                try:
                    line = ser.readline()
                except Exception:
                    break
                if not line:
                    continue
                try:
                    line_str = line.decode('utf-8', errors='ignore').strip()
                except Exception:
                    continue
                if not line_str:
                    continue
                # CPM formats
                cpm_match = re.search(r'(\d+\.?\d*)\s*(cpm|counts?)', line_str, re.IGNORECASE)
                if cpm_match:
                    try:
                        cpm_val = float(cpm_match.group(1))
                        data['cpm'] = cpm_val
                        data['usv'] = round(cpm_val / 333.0, 4)
                        data['usv_hour'] = data['usv']
                    except Exception:
                        pass
                # Dose formats
                usv_match = re.search(r'(\d+\.?\d*)\s*[uµ]s?v(/h)?', line_str, re.IGNORECASE)
                if usv_match:
                    try:
                        usv_val = float(usv_match.group(1))
                        data['usv'] = usv_val
                        data['usv_hour'] = usv_val
                        if 'cpm' not in data:
                            data['cpm'] = round(usv_val * 333.0, 1)
                    except Exception:
                        pass
            if data.get('cpm') is not None or data.get('usv') is not None:
                return data
            return {"err": "No Geiger data received"}
        except Exception as e:
            return {"err": f"Geiger read failed: {str(e)[:55]}"}
        finally:
            try:
                ser.close()
            except Exception:
                pass

    def _real_bandwidth_test(self):
        """Download and upload test files to measure real bandwidth (Mbps)."""
        import requests
        import time
        import io
        
        result = {}
        
        # Try multiple CDN sources for download test with larger files
        download_urls = [
            "https://proof.ovh.net/files/10Mb.dat",     # OVH CDN - 10MB
            "https://speed.hetzner.de/10MB.bin",        # Hetzner - 10MB
            "https://proof.ovh.net/files/1Mb.dat",      # OVH CDN - 1MB (fallback)
        ]
        
        # Download test - use larger chunk size for high-speed connections
        download_success = False
        for download_url in download_urls:
            try:
                start = time.time()
                resp = requests.get(download_url, stream=True, timeout=30)
                resp.raise_for_status()  # Raise error for bad status codes
                total_dl = 0
                # Use larger chunk size (128KB) for better throughput measurement
                for chunk in resp.iter_content(chunk_size=131072):
                    if chunk:
                        total_dl += len(chunk)
                elapsed_dl = time.time() - start
                dl_mbps = (total_dl * 8) / 1e6 / elapsed_dl if elapsed_dl > 0 else 0
                result["download_mbps"] = round(dl_mbps, 2)
                result["download_bytes"] = total_dl
                result["download_seconds"] = round(elapsed_dl, 2)
                result["download_url"] = download_url
                download_success = True
                break  # Success, exit loop
            except Exception as e:
                # Try next URL
                continue
        
        if not download_success:
            result["download_error"] = "All download URLs failed"
        
        # Try multiple endpoints for upload test with larger payload
        upload_urls = [
            "https://httpbin.org/post",              # httpbin (reliable)
            "https://postman-echo.com/post",         # Postman Echo
        ]
        
        # Upload test - use moderate payload for balanced accuracy
        upload_success = False
        for upload_url in upload_urls:
            try:
                # Generate 5MB of data in memory for accurate measurement without heavy load
                data_size = 5 * 1024 * 1024  # 5MB
                upload_data = b'0' * data_size
                
                start = time.time()
                resp = requests.post(upload_url, data=upload_data, timeout=30)
                resp.raise_for_status()  # Raise error for bad status codes
                elapsed_ul = time.time() - start
                ul_mbps = (data_size * 8) / 1e6 / elapsed_ul if elapsed_ul > 0 else 0
                result["upload_mbps"] = round(ul_mbps, 2)
                result["upload_bytes"] = data_size
                result["upload_seconds"] = round(elapsed_ul, 2)
                result["upload_url"] = upload_url
                upload_success = True
                break  # Success, exit loop
            except Exception as e:
                # Try next URL
                continue
        
        if not upload_success:
            result["upload_error"] = "All upload URLs failed"
        
        return result if result else {"err": "Bandwidth test failed"}
    
    # Measurement writing is now handled by the autonomous service.
    # GUI should read measurements from measurements/latest.json via measurement_reader.py
