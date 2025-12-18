"""
Input validation and sanitization utilities for birth data and filenames.

This module provides pure validation functions that are used across the app
for ensuring data integrity and security.
"""

import re
from datetime import datetime
from typing import Any, Dict, Mapping


def sanitize_filename(name: str, job_id: str) -> str:
    """
    Sanitize and uniquify a filename to prevent path traversal and collisions.

    Removes unsafe characters and appends job_id for uniqueness. The resulting
    filename is safe for use in filesystem operations.

    Args:
        name: Original name from user input (e.g., person's name)
        job_id: Unique job identifier for uniqueness

    Returns:
        Safe filename with .svg extension (e.g., "John Smith - Natal Chart - abc123.svg")

    Example:
        >>> sanitize_filename("John Smith", "job_abc123")
        'John Smith - Natal Chart - job_abc123.svg'

        >>> sanitize_filename("../../../etc/passwd", "job_def456")
        'etcpasswd - Natal Chart - job_def456.svg'
    """
    # Remove path separators and control characters; keep alphanumeric, spaces, hyphens, underscores
    safe_name = re.sub(r'[^A-Za-z0-9 _\-]', '', name).strip()

    # Limit to 50 characters to keep overall filename reasonable
    safe_name = safe_name[:50] if safe_name else 'Chart'

    # Create unique filename with job_id to prevent collisions
    unique_filename = f"{safe_name} - Natal Chart - {job_id}.svg"

    return unique_filename


def validate_birth_data(form_data: Mapping[str, Any]) -> Dict[str, Any]:
    """
    Validate and sanitize birth data from form input.

    Validates all required fields (name, city, country, date/time) with
    reasonable bounds checking. Returns a normalized dictionary suitable
    for chart generation.

    Args:
        form_data: Mapping (dict-like) containing form fields with keys:
            - name: Person's name (required, 1-100 chars)
            - city: Birth city (required, 1-100 chars)
            - region: Birth region/state (optional, 1-100 chars)
            - country: Birth country (required, 1-100 chars)
            - year: Birth year (required, 1900-current year)
            - month: Birth month (required, 1-12)
            - day: Birth day (required, 1-31)
            - hour: Birth hour (required, 0-23)
            - minute: Birth minute (required, 0-59)

    Returns:
        Dictionary with validated and typed birth data:
        {
            'name': str,
            'year': int,
            'month': int,
            'day': int,
            'hour': int,
            'minute': int,
            'city': str,
            'region': str,
            'country': str
        }

    Raises:
        ValueError: If any field is invalid, missing, out of range, or if the
                   date/time combination is impossible.

    Example:
        >>> data = validate_birth_data({
        ...     'name': 'John Doe',
        ...     'year': 1990, 'month': 5, 'day': 15,
        ...     'hour': 14, 'minute': 30,
        ...     'city': 'Boston', 'region': 'MA',
        ...     'country': 'USA'
        ... })
        >>> data['year']
        1990

        >>> validate_birth_data({'name': 'John', 'year': 1900})  # Missing required fields
        Traceback (most recent call last):
            ...
        ValueError: City is required
    """
    try:
        # String validations
        name = form_data.get('name', '').strip()
        if not name or len(name) > 100:
            raise ValueError("Name must be 1-100 characters")

        city = form_data.get('city', '').strip()
        if not city or len(city) > 100:
            raise ValueError("City must be 1-100 characters")

        region = form_data.get('region', '').strip()
        if region and len(region) > 100:
            raise ValueError("Region must be 1-100 characters")

        country = form_data.get('country') or form_data.get('country_name')
        if not country:
            raise ValueError("Country is required")

        country = str(country).strip()
        if len(country) > 100:
            raise ValueError("Country must be 1-100 characters")

        # Numeric validations with bounds
        try:
            year = int(form_data.get('year', 0))
        except (ValueError, TypeError):
            raise ValueError("Year must be a valid integer")

        if not (1900 <= year <= datetime.now().year):
            raise ValueError(f"Year must be between 1900 and {datetime.now().year}")

        try:
            month = int(form_data.get('month', 0))
        except (ValueError, TypeError):
            raise ValueError("Month must be a valid integer")

        if not (1 <= month <= 12):
            raise ValueError("Month must be between 1 and 12")

        try:
            day = int(form_data.get('day', 0))
        except (ValueError, TypeError):
            raise ValueError("Day must be a valid integer")

        if not (1 <= day <= 31):
            raise ValueError("Day must be between 1 and 31")

        try:
            hour = int(form_data.get('hour', 0))
        except (ValueError, TypeError):
            raise ValueError("Hour must be a valid integer")

        if not (0 <= hour <= 23):
            raise ValueError("Hour must be between 0 and 23")

        try:
            minute = int(form_data.get('minute', 0))
        except (ValueError, TypeError):
            raise ValueError("Minute must be a valid integer")

        if not (0 <= minute <= 59):
            raise ValueError("Minute must be between 0 and 59")

        # Validate actual date/time is possible
        try:
            datetime(year, month, day, hour, minute)
        except ValueError as e:
            raise ValueError(f"Invalid date/time: {e}")

        return {
            'name': name,
            'year': year,
            'month': month,
            'day': day,
            'hour': hour,
            'minute': minute,
            'city': city,
            'region': region,
            'country': country
        }
    except (ValueError, TypeError) as e:
        raise ValueError(f"Invalid input: {str(e)}")

