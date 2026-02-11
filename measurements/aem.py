"""AEM (AI Edge Miner) measurement collection.

Collects rich Olostep Browser activity metrics: task count, memory usage,
process count, and activity recency. Replaces the old boolean-only POI.
"""

import logging
from typing import Dict, Any, Optional

log = logging.getLogger("measurements.aem")


def collect_aem_measurement() -> Optional[Dict[str, Any]]:
    """Collect AEM Olostep Browser activity metrics.

    Returns:
        Dict with poi, tasks_completed, last_activity_age_s, mem_rss_mb, proc_count
        or None on total failure
    """
    try:
        from poi_monitor_aem import get_olostep_metrics
        return get_olostep_metrics()
    except ImportError:
        log.error("poi_monitor_aem not available")
        return None
    except Exception as e:
        log.error("AEM measurement failed: %s", e)
        return None
