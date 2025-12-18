"""
Chart generation service module.

This module encapsulates all chart generation logic, including subprocess
execution, file handling, and error management. It provides a clean,
typed API for generating astrology charts.
"""

import logging
import re
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class ChartGenerationError(Exception):
    """Base exception for chart generation errors."""
    pass


class ChartTooLargeError(ChartGenerationError):
    """Raised when generated SVG exceeds size limit."""
    pass


class ChartMissingError(ChartGenerationError):
    """Raised when generated SVG file cannot be found."""
    pass


def generate_chart(
    validated_data: Dict[str, Any],
    *,
    output_dir: str,
    job_id: Optional[str] = None,
    geonames_username: Optional[str] = None,
    max_svg_size: int = 10 * 1024 * 1024
) -> Dict[str, str]:
    """
    Generate an astrological natal chart SVG.

    This is the main entry point for chart generation. It orchestrates the
    subprocess call to _generate_svg.py and handles file output.

    Args:
        validated_data: Dictionary with validated birth data from validators.
            Expected keys: name, year, month, day, hour, minute, city, country
        output_dir: Absolute path to directory where SVG should be written
        job_id: Optional unique identifier for filename generation. If not provided,
                a UUID hex will be generated.
        geonames_username: Optional username for geonames lookups (may be None)
        max_svg_size: Maximum allowed SVG file size in bytes (default: 10MB)

    Returns:
        Dictionary with keys:
            - 'filename': Safe SVG filename (e.g., "John Doe - Natal Chart - abc123.svg")
            - 'svg_path': Absolute path to generated SVG file

    Raises:
        ChartGenerationError: If chart generation subprocess fails
        ChartTooLargeError: If generated SVG exceeds max_svg_size
        ChartMissingError: If generated SVG file not found after subprocess
        FileNotFoundError: If helper script or output directory not found

    Example:
        >>> validated = {'name': 'John', 'year': 1990, ...}
        >>> result = generate_chart(
        ...     validated,
        ...     output_dir='/path/to/charts',
        ...     job_id='job_123'
        ... )
        >>> result['svg_path']
        '/path/to/charts/John - Natal Chart - job_123.svg'
    """
    from simpleastro.validators import sanitize_filename

    # Ensure output directory exists
    output_path = Path(output_dir)
    if not output_path.exists():
        raise FileNotFoundError(f"Output directory not found: {output_dir}")

    # Use provided job_id or generate a new UUID
    if not job_id:
        job_id = uuid.uuid4().hex

    # Create safe subject name (for subprocess argument)
    safe_subject_name = re.sub(r'[^A-Za-z0-9 _\-]', '', validated_data['name']).strip() or 'Chart'
    safe_subject_name = safe_subject_name[:50]

    # Sanitize and uniquify filename
    safe_filename = sanitize_filename(validated_data['name'], job_id)

    # Build subprocess command
    helper_script = Path(__file__).parent.parent / '_generate_svg.py'
    if not helper_script.exists():
        raise FileNotFoundError(f"Helper script not found: {helper_script}")

    cmd = [
        sys.executable,
        str(helper_script),
        safe_subject_name,
        str(validated_data['year']),
        str(validated_data['month']),
        str(validated_data['day']),
        str(validated_data['hour']),
        str(validated_data['minute']),
        validated_data.get('city') or '',
        validated_data.get('country') or '',
        geonames_username or '',
        safe_filename
    ]

    # Execute helper script with cwd set to output_dir
    # This ensures generated files go to the correct location
    logger.debug(f"Executing chart generation: {cmd}")
    try:
        proc = subprocess.run(
            cmd,
            cwd=output_dir,
            capture_output=True,
            text=True,
            timeout=60  # 60 second timeout
        )
    except subprocess.TimeoutExpired as e:
        logger.error(f"Chart generation timed out after 60 seconds")
        raise ChartGenerationError(f"Chart generation timed out") from e
    except Exception as e:
        logger.error(f"Chart generation subprocess error: {e}")
        raise ChartGenerationError(f"Failed to execute chart generation: {e}") from e

    # Check subprocess return code
    if proc.returncode != 0:
        error_msg = proc.stderr.strip() if proc.stderr else proc.stdout.strip()
        logger.error(f"Chart generation failed with code {proc.returncode}: {error_msg}")
        raise ChartGenerationError(f"Chart generation failed: {error_msg}")

    # Verify generated file exists
    safe_svg_path = output_path / safe_filename
    if not safe_svg_path.exists():
        logger.error(f"Generated SVG not found: {safe_svg_path}")
        raise ChartMissingError(f"Generated SVG not found at: {safe_svg_path}")

    # Check file size
    svg_size = safe_svg_path.stat().st_size
    if svg_size > max_svg_size:
        logger.warning(f"SVG size {svg_size} bytes exceeds limit {max_svg_size} bytes")
        raise ChartTooLargeError(
            f"SVG too large: {svg_size} bytes (max: {max_svg_size})"
        )

    logger.info(f"Chart generated successfully: {safe_filename} ({svg_size} bytes)")
    return {
        'filename': safe_filename,
        'svg_path': str(safe_svg_path)
    }

