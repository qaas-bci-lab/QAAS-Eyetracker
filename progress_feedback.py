"""Small deterministic helpers for honest long-running progress feedback."""
import math


def format_duration(seconds):
    """Round elapsed wall time for display without implying sub-second precision."""
    seconds = max(0, int(round(seconds)))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f'{hours}小时{minutes:02d}分{seconds:02d}秒'
    if minutes:
        return f'{minutes}分{seconds:02d}秒'
    return f'{seconds}秒'


def timing_feedback(completed, total, elapsed, *, minimum_observations=5):
    """Estimate remaining time only after actual work, never from zero counters.

    The estimate describes this stage only, not later export or report stages.
    """
    elapsed = max(0.0, float(elapsed))
    text = f'已耗时 {format_duration(elapsed)}'
    if completed >= total > 0:
        return text
    if (completed < minimum_observations or completed <= 0 or total <= completed
            or elapsed < .5 or not math.isfinite(elapsed)):
        return text + '  剩余时间估算中'
    remaining = elapsed * (total - completed) / completed
    return text + f'  本阶段预计剩余约 {format_duration(remaining)}'
