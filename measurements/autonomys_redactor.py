"""Data redaction for Autonomys Auto Drive public storage.

Applies privacy-preserving transformations to measurement data before upload.
Maintains data utility while protecting sensitive information.
"""

import logging
from typing import Any, Dict, Optional
from datetime import datetime
import pandas as pd

log = logging.getLogger("measurements.autonomys_redactor")

from .autonomys_writer import get_hex_resolution, get_parent_hex, H3_AVAILABLE


class DataRedactor:
    """Redacts sensitive information from measurement data for public storage."""

    def __init__(self, redaction_level: str = "standard"):
        """Initialize redactor with specified level.

        Args:
            redaction_level: 'minimal', 'standard', or 'full'
        """
        self.level = redaction_level

    def redact_location(self, hex_id: str, target_resolution: int = 5) -> str:
        """Reduce hex precision to protect exact location.

        Args:
            hex_id: Original H3 hex ID (e.g., res-7: ~5 km²)
            target_resolution: Target resolution (e.g., res-5: ~250 km²)

        Returns:
            Coarser hex ID that covers a larger area

        Example:
            871f90151ffffff (res-7, ~5 km²) → 851f901ffffffff (res-5, ~250 km²)
        """
        if not H3_AVAILABLE:
            return "REDACTED"

        try:
            current_res = get_hex_resolution(hex_id)

            # If already at or below target resolution, return as-is
            if current_res <= target_resolution:
                return hex_id

            # Get parent hex at target resolution (coarser)
            parent_hex = get_parent_hex(hex_id, target_resolution)
            return parent_hex
        except Exception as e:
            log.error("Failed to redact hex_id %s: %s", hex_id, e)
            return "REDACTED"

    def add_noise_to_value(self, value: float, noise_percent: float = 5.0) -> float:
        """Add random noise to a measurement value.

        Args:
            value: Original measurement value
            noise_percent: Percentage of noise to add (default 5%)

        Returns:
            Value with added noise
        """
        import random
        noise_factor = 1.0 + random.uniform(-noise_percent/100, noise_percent/100)
        return value * noise_factor

    def round_timestamp(self, timestamp: str, round_to_hours: int = 1) -> str:
        """Round timestamp to reduce temporal precision.

        Args:
            timestamp: ISO format timestamp
            round_to_hours: Round to nearest N hours

        Returns:
            Rounded timestamp
        """
        try:
            dt = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
            # Round down to nearest hour interval
            rounded_hour = (dt.hour // round_to_hours) * round_to_hours
            dt_rounded = dt.replace(hour=rounded_hour, minute=0, second=0, microsecond=0)
            return dt_rounded.isoformat()
        except Exception as e:
            log.error("Failed to round timestamp %s: %s", timestamp, e)
            return timestamp

    def redact_bandwidth_data(self, df: pd.DataFrame) -> pd.DataFrame:
        """Redact bandwidth measurement DataFrame.

        Redaction applied based on level:
        - minimal: Round values to 1 decimal place
        - standard: Add 5% noise, remove interface info
        - full: Add 10% noise, round to 5 Mbps buckets

        Args:
            df: DataFrame with bandwidth measurements

        Returns:
            Redacted DataFrame
        """
        df_redacted = df.copy()

        if self.level == 'minimal':
            # Just round values
            df_redacted['dl_avg_mbps'] = df_redacted['dl_avg_mbps'].round(1)
            df_redacted['ul_avg_mbps'] = df_redacted['ul_avg_mbps'].round(1)
            df_redacted['dl_min_mbps'] = df_redacted['dl_min_mbps'].round(1)
            df_redacted['dl_max_mbps'] = df_redacted['dl_max_mbps'].round(1)
            df_redacted['ul_min_mbps'] = df_redacted['ul_min_mbps'].round(1)
            df_redacted['ul_max_mbps'] = df_redacted['ul_max_mbps'].round(1)

        elif self.level == 'standard':
            # Add noise and remove interface
            for col in ['dl_avg_mbps', 'dl_min_mbps', 'dl_max_mbps',
                       'ul_avg_mbps', 'ul_min_mbps', 'ul_max_mbps']:
                df_redacted[col] = df_redacted[col].apply(
                    lambda x: self.add_noise_to_value(x, 5.0)
                )

            # Remove interface information
            if 'iface' in df_redacted.columns:
                df_redacted['iface'] = 'REDACTED'

        elif self.level == 'full':
            # Heavy noise and bucketing
            for col in ['dl_avg_mbps', 'dl_min_mbps', 'dl_max_mbps',
                       'ul_avg_mbps', 'ul_min_mbps', 'ul_max_mbps']:
                df_redacted[col] = df_redacted[col].apply(
                    lambda x: self.add_noise_to_value(x, 10.0)
                )
                # Round to 5 Mbps buckets
                df_redacted[col] = (df_redacted[col] / 5).round() * 5

            if 'iface' in df_redacted.columns:
                df_redacted['iface'] = 'REDACTED'

        return df_redacted

    def redact_satellite_data(self, df: pd.DataFrame) -> pd.DataFrame:
        """Redact satellite/GNSS measurement DataFrame.

        Removes exact lat/lon, keeps satellite counts.

        Args:
            df: DataFrame with satellite measurements

        Returns:
            Redacted DataFrame
        """
        df_redacted = df.copy()

        if self.level in ['standard', 'full']:
            # Remove exact coordinates
            if 'lat_avg' in df_redacted.columns:
                df_redacted['lat_avg'] = None
            if 'lon_avg' in df_redacted.columns:
                df_redacted['lon_avg'] = None

        # Keep satellite counts and quality metrics (less sensitive)
        # sats_avg, hdop_avg, fix_mode are fine

        return df_redacted

    def redact_radiation_data(self, df: pd.DataFrame) -> pd.DataFrame:
        """Redact radiation measurement DataFrame.

        Args:
            df: DataFrame with radiation measurements

        Returns:
            Redacted DataFrame
        """
        df_redacted = df.copy()

        if self.level == 'standard':
            # Add small noise to prevent exact readings
            for col in ['cpm_avg', 'cpm_min', 'cpm_max']:
                df_redacted[col] = df_redacted[col].apply(
                    lambda x: self.add_noise_to_value(x, 3.0)
                )

        elif self.level == 'full':
            # Heavier noise and rounding
            for col in ['cpm_avg', 'cpm_min', 'cpm_max']:
                df_redacted[col] = df_redacted[col].apply(
                    lambda x: self.add_noise_to_value(x, 10.0)
                )
                df_redacted[col] = df_redacted[col].round(0)

        return df_redacted

    def redact_decibel_data(self, df: pd.DataFrame) -> pd.DataFrame:
        """Redact decibel/noise measurement DataFrame.

        Args:
            df: DataFrame with decibel measurements

        Returns:
            Redacted DataFrame
        """
        df_redacted = df.copy()

        if self.level == 'standard':
            # Round to 1 decimal place
            for col in ['dbfs_avg', 'dbfs_min', 'dbfs_max']:
                df_redacted[col] = df_redacted[col].round(1)

        elif self.level == 'full':
            # Round to nearest 5 dB
            for col in ['dbfs_avg', 'dbfs_min', 'dbfs_max']:
                df_redacted[col] = (df_redacted[col] / 5).round() * 5

        return df_redacted

    def redact_aem_data(self, df: pd.DataFrame) -> pd.DataFrame:
        """Redact AEM measurement DataFrame.

        AEM (points of interest) data is less sensitive but can still
        reveal exact locations. Apply light redaction.

        Args:
            df: DataFrame with AEM measurements

        Returns:
            Redacted DataFrame
        """
        df_redacted = df.copy()

        if self.level == 'standard':
            # Round to 1 decimal place
            for col in ['poi_avg', 'poi_min', 'poi_max']:
                if col in df_redacted.columns:
                    df_redacted[col] = df_redacted[col].round(1)

        elif self.level == 'full':
            # Add small noise and round
            for col in ['poi_avg', 'poi_min', 'poi_max']:
                if col in df_redacted.columns:
                    df_redacted[col] = df_redacted[col].apply(
                        lambda x: self.add_noise_to_value(x, 5.0)
                    )
                    df_redacted[col] = df_redacted[col].round(0)

        return df_redacted

    def redact_manifest(self, manifest: Dict[str, Any], redacted_hex_id: str) -> Dict[str, Any]:
        """Redact manifest metadata.

        Removes or generalizes sensitive information in manifest files.

        Args:
            manifest: Original manifest dict
            redacted_hex_id: Redacted hex ID to use

        Returns:
            Redacted manifest dict
        """
        redacted = manifest.copy()

        # Replace hex_id with redacted version
        redacted['hex_id'] = redacted_hex_id

        # Remove exact center coordinates
        if self.level in ['standard', 'full']:
            if 'center' in redacted:
                # Round to ~10km precision (2 decimal places)
                if redacted['center'].get('lat'):
                    redacted['center']['lat'] = round(redacted['center']['lat'], 1)
                if redacted['center'].get('lon'):
                    redacted['center']['lon'] = round(redacted['center']['lon'], 1)

        # Remove install IDs (can identify specific miners)
        if 'install_ids' in redacted:
            redacted['install_ids'] = []  # Just show count instead
            redacted['install_count'] = len(manifest.get('install_ids', []))

        # Keep coverage dates and quality metrics (less sensitive)
        # coverage, data_quality, sample_rate are fine

        return redacted


# Redaction level presets
REDACTION_LEVELS = {
    'minimal': {
        'description': 'Light redaction - round values, keep structure',
        'hex_resolution': 7,  # ~5 km² (original precision)
        'add_noise': False,
        'round_values': True,
        'remove_identifiers': False
    },
    'standard': {
        'description': 'Standard redaction - noise + coarser location',
        'hex_resolution': 5,  # ~252 km² (50x larger area)
        'add_noise': True,
        'noise_percent': 5.0,
        'remove_identifiers': True
    },
    'full': {
        'description': 'Full redaction - heavy noise + very coarse location',
        'hex_resolution': 4,  # ~1,300 km² (260x larger area)
        'add_noise': True,
        'noise_percent': 10.0,
        'remove_identifiers': True,
        'bucket_values': True
    }
}


def get_redaction_level_info(level: str = 'standard') -> Dict[str, Any]:
    """Get information about a redaction level.

    Args:
        level: Redaction level name

    Returns:
        Dict with redaction level details
    """
    return REDACTION_LEVELS.get(level, REDACTION_LEVELS['standard'])
