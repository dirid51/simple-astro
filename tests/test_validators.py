"""
Unit tests for input validators module.

Tests cover:
- Birth data validation (all required fields, bounds checking, date validity)
- Filename sanitization (path traversal prevention, special char handling, uniqueness)
- Edge cases (boundary values, empty strings, unicode, very long strings)
"""

import uuid
import pytest
from datetime import datetime

from simpleastro.validators import validate_birth_data, sanitize_filename


class TestSanitizeFilename:
    """Test filename sanitization and collision prevention."""

    def test_sanitize_filename_basic(self):
        """Test basic filename sanitization."""
        result = sanitize_filename("John Smith", "job123")
        assert result == "John Smith - Natal Chart - job123.svg"
        assert result.endswith(".svg")

    def test_sanitize_filename_removes_path_separators(self):
        """Test that path separators are removed from filenames."""
        unsafe_name = "John ../../../etc/passwd"
        job_id = uuid.uuid4().hex
        result = sanitize_filename(unsafe_name, job_id)

        assert "/" not in result
        assert "\\" not in result
        assert ".." not in result
        assert result.endswith(".svg")
        assert job_id in result

    def test_sanitize_filename_removes_special_chars(self):
        """Test that special characters are removed."""
        unsafe_name = "John<script>alert('xss')</script>"
        job_id = uuid.uuid4().hex
        result = sanitize_filename(unsafe_name, job_id)

        assert "<" not in result
        assert ">" not in result
        assert "'" not in result
        assert "(" not in result
        assert ")" not in result
        assert result.endswith(".svg")

    def test_sanitize_filename_limits_length(self):
        """Test that name is limited to reasonable length."""
        long_name = "A" * 200
        job_id = uuid.uuid4().hex
        result = sanitize_filename(long_name, job_id)

        # Overall length should be reasonable (name limited to 50 + " - Natal Chart - " + 32-char UUID + ".svg")
        # Total: 50 + 17 + 32 + 4 = 103 max
        assert len(result) <= 110
        assert result.endswith(".svg")

    def test_sanitize_filename_unique_with_job_id(self):
        """Test that different job IDs produce different filenames."""
        name = "John Smith"
        job_id_1 = uuid.uuid4().hex
        job_id_2 = uuid.uuid4().hex

        result1 = sanitize_filename(name, job_id_1)
        result2 = sanitize_filename(name, job_id_2)

        assert result1 != result2
        assert job_id_1 in result1
        assert job_id_2 in result2

    def test_sanitize_filename_preserves_alphanumeric(self):
        """Test that alphanumeric characters and spaces are preserved."""
        name = "John Smith 123"
        job_id = uuid.uuid4().hex
        result = sanitize_filename(name, job_id)

        assert "John" in result
        assert "Smith" in result
        assert "123" in result

    def test_sanitize_filename_handles_empty_name(self):
        """Test handling of empty or whitespace-only names."""
        result = sanitize_filename("", "job123")
        assert "Chart" in result
        assert result.endswith(".svg")

    def test_sanitize_filename_handles_whitespace_only(self):
        """Test handling of whitespace-only names."""
        result = sanitize_filename("   ", "job123")
        assert "Chart" in result
        assert result.endswith(".svg")

    def test_sanitize_filename_with_unicode(self):
        """Test handling of unicode characters (should be removed)."""
        name = "José García 中文"
        job_id = uuid.uuid4().hex
        result = sanitize_filename(name, job_id)

        # Unicode should be removed by the regex
        assert result.endswith(".svg")
        assert job_id in result
        # Original unicode chars should not be in result
        assert "中文" not in result
        assert "á" not in result

    def test_sanitize_filename_with_hyphens_and_underscores(self):
        """Test that hyphens and underscores are preserved."""
        name = "Mary-Jane_Smith"
        job_id = uuid.uuid4().hex
        result = sanitize_filename(name, job_id)

        assert "Mary-Jane_Smith" in result


class TestValidateBirthData:
    """Test birth data validation."""

    def test_validate_birth_data_valid_input(self):
        """Test validation with valid input."""
        form_data = {
            'name': 'John Doe',
            'year': 1990,
            'month': 5,
            'day': 15,
            'hour': 14,
            'minute': 30,
            'city': 'Boston',
            'region': 'MA',
            'country': 'USA'
        }
        result = validate_birth_data(form_data)

        assert result['name'] == 'John Doe'
        assert result['year'] == 1990
        assert result['month'] == 5
        assert result['day'] == 15
        assert result['hour'] == 14
        assert result['minute'] == 30
        assert result['city'] == 'Boston'
        assert result['country'] == 'USA'

    def test_validate_birth_data_with_country_name_alt_field(self):
        """Test that country_name is accepted as alternative to country."""
        form_data = {
            'name': 'John Doe',
            'year': 1990,
            'month': 5,
            'day': 15,
            'hour': 14,
            'minute': 30,
            'city': 'Boston',
            'country_name': 'USA'
        }
        result = validate_birth_data(form_data)
        assert result['country'] == 'USA'

    def test_validate_birth_data_strips_whitespace(self):
        """Test that whitespace is stripped from string fields."""
        form_data = {
            'name': '  John Doe  ',
            'year': 1990,
            'month': 5,
            'day': 15,
            'hour': 14,
            'minute': 30,
            'city': '  Boston  ',
            'country': '  USA  '
        }
        result = validate_birth_data(form_data)

        assert result['name'] == 'John Doe'
        assert result['city'] == 'Boston'
        assert result['country'] == 'USA'

    # Name validation tests
    def test_validate_birth_data_missing_name(self):
        """Test validation fails when name is missing."""
        form_data = {
            'name': '',
            'year': 1990,
            'month': 5,
            'day': 15,
            'hour': 14,
            'minute': 30,
            'city': 'Boston',
            'country': 'USA'
        }
        with pytest.raises(ValueError, match="Name must be"):
            validate_birth_data(form_data)

    def test_validate_birth_data_name_too_long(self):
        """Test validation fails when name exceeds 100 characters."""
        form_data = {
            'name': 'A' * 101,
            'year': 1990,
            'month': 5,
            'day': 15,
            'hour': 14,
            'minute': 30,
            'city': 'Boston',
            'country': 'USA'
        }
        with pytest.raises(ValueError, match="Name must be"):
            validate_birth_data(form_data)

    def test_validate_birth_data_name_exactly_100_chars(self):
        """Test validation passes when name is exactly 100 characters."""
        form_data = {
            'name': 'A' * 100,
            'year': 1990,
            'month': 5,
            'day': 15,
            'hour': 14,
            'minute': 30,
            'city': 'Boston',
            'country': 'USA'
        }
        result = validate_birth_data(form_data)
        assert len(result['name']) == 100

    # City validation tests
    def test_validate_birth_data_missing_city(self):
        """Test validation fails when city is missing."""
        form_data = {
            'name': 'John Doe',
            'year': 1990,
            'month': 5,
            'day': 15,
            'hour': 14,
            'minute': 30,
            'city': '',
            'country': 'USA'
        }
        with pytest.raises(ValueError, match="City must be"):
            validate_birth_data(form_data)

    def test_validate_birth_data_city_too_long(self):
        """Test validation fails when city exceeds 100 characters."""
        form_data = {
            'name': 'John Doe',
            'year': 1990,
            'month': 5,
            'day': 15,
            'hour': 14,
            'minute': 30,
            'city': 'A' * 101,
            'country': 'USA'
        }
        with pytest.raises(ValueError, match="City must be"):
            validate_birth_data(form_data)

    # Region validation tests
    def test_validate_birth_data_region_optional(self):
        """Test that region is optional."""
        form_data = {
            'name': 'John Doe',
            'year': 1990,
            'month': 5,
            'day': 15,
            'hour': 14,
            'minute': 30,
            'city': 'Boston',
            'region': '',
            'country': 'USA'
        }
        result = validate_birth_data(form_data)
        assert result['region'] == ''

    def test_validate_birth_data_region_too_long(self):
        """Test validation fails when region exceeds 100 characters."""
        form_data = {
            'name': 'John Doe',
            'year': 1990,
            'month': 5,
            'day': 15,
            'hour': 14,
            'minute': 30,
            'city': 'Boston',
            'region': 'A' * 101,
            'country': 'USA'
        }
        with pytest.raises(ValueError, match="Region must be"):
            validate_birth_data(form_data)

    # Country validation tests
    def test_validate_birth_data_missing_country(self):
        """Test validation fails when country is missing."""
        form_data = {
            'name': 'John Doe',
            'year': 1990,
            'month': 5,
            'day': 15,
            'hour': 14,
            'minute': 30,
            'city': 'Boston',
            'country': ''
        }
        with pytest.raises(ValueError, match="Country is required"):
            validate_birth_data(form_data)

    def test_validate_birth_data_country_too_long(self):
        """Test validation fails when country exceeds 100 characters."""
        form_data = {
            'name': 'John Doe',
            'year': 1990,
            'month': 5,
            'day': 15,
            'hour': 14,
            'minute': 30,
            'city': 'Boston',
            'country': 'A' * 101
        }
        with pytest.raises(ValueError, match="Country must be"):
            validate_birth_data(form_data)

    # Year validation tests
    def test_validate_birth_data_missing_year(self):
        """Test validation fails when year is missing."""
        form_data = {
            'name': 'John Doe',
            'year': '',
            'month': 5,
            'day': 15,
            'hour': 14,
            'minute': 30,
            'city': 'Boston',
            'country': 'USA'
        }
        with pytest.raises(ValueError, match="Year must be"):
            validate_birth_data(form_data)

    def test_validate_birth_data_year_not_integer(self):
        """Test validation fails when year is not an integer."""
        form_data = {
            'name': 'John Doe',
            'year': 'nineteen ninety',
            'month': 5,
            'day': 15,
            'hour': 14,
            'minute': 30,
            'city': 'Boston',
            'country': 'USA'
        }
        with pytest.raises(ValueError, match="Year must be"):
            validate_birth_data(form_data)

    def test_validate_birth_data_year_too_old(self):
        """Test validation fails when year is before 1900."""
        form_data = {
            'name': 'John Doe',
            'year': 1899,
            'month': 5,
            'day': 15,
            'hour': 14,
            'minute': 30,
            'city': 'Boston',
            'country': 'USA'
        }
        with pytest.raises(ValueError, match="Year must be between"):
            validate_birth_data(form_data)

    def test_validate_birth_data_year_in_future(self):
        """Test validation fails when year is in the future."""
        current_year = datetime.now().year
        form_data = {
            'name': 'John Doe',
            'year': current_year + 1,
            'month': 5,
            'day': 15,
            'hour': 14,
            'minute': 30,
            'city': 'Boston',
            'country': 'USA'
        }
        with pytest.raises(ValueError, match="Year must be between"):
            validate_birth_data(form_data)

    def test_validate_birth_data_year_boundary_1900(self):
        """Test validation passes for year 1900."""
        form_data = {
            'name': 'John Doe',
            'year': 1900,
            'month': 5,
            'day': 15,
            'hour': 14,
            'minute': 30,
            'city': 'Boston',
            'country': 'USA'
        }
        result = validate_birth_data(form_data)
        assert result['year'] == 1900

    def test_validate_birth_data_year_boundary_current(self):
        """Test validation passes for current year."""
        current_year = datetime.now().year
        form_data = {
            'name': 'John Doe',
            'year': current_year,
            'month': 5,
            'day': 15,
            'hour': 14,
            'minute': 30,
            'city': 'Boston',
            'country': 'USA'
        }
        result = validate_birth_data(form_data)
        assert result['year'] == current_year

    # Month validation tests
    def test_validate_birth_data_invalid_month(self):
        """Test validation fails for invalid month."""
        form_data = {
            'name': 'John Doe',
            'year': 1990,
            'month': 13,
            'day': 15,
            'hour': 14,
            'minute': 30,
            'city': 'Boston',
            'country': 'USA'
        }
        with pytest.raises(ValueError, match="Month must be between"):
            validate_birth_data(form_data)

    def test_validate_birth_data_month_zero(self):
        """Test validation fails for month 0."""
        form_data = {
            'name': 'John Doe',
            'year': 1990,
            'month': 0,
            'day': 15,
            'hour': 14,
            'minute': 30,
            'city': 'Boston',
            'country': 'USA'
        }
        with pytest.raises(ValueError, match="Month must be between"):
            validate_birth_data(form_data)

    def test_validate_birth_data_month_boundary_1(self):
        """Test validation passes for month 1."""
        form_data = {
            'name': 'John Doe',
            'year': 1990,
            'month': 1,
            'day': 15,
            'hour': 14,
            'minute': 30,
            'city': 'Boston',
            'country': 'USA'
        }
        result = validate_birth_data(form_data)
        assert result['month'] == 1

    def test_validate_birth_data_month_boundary_12(self):
        """Test validation passes for month 12."""
        form_data = {
            'name': 'John Doe',
            'year': 1990,
            'month': 12,
            'day': 15,
            'hour': 14,
            'minute': 30,
            'city': 'Boston',
            'country': 'USA'
        }
        result = validate_birth_data(form_data)
        assert result['month'] == 12

    # Day validation tests
    def test_validate_birth_data_invalid_day(self):
        """Test validation fails for invalid day."""
        form_data = {
            'name': 'John Doe',
            'year': 1990,
            'month': 5,
            'day': 32,
            'hour': 14,
            'minute': 30,
            'city': 'Boston',
            'country': 'USA'
        }
        with pytest.raises(ValueError, match="Day must be between"):
            validate_birth_data(form_data)

    def test_validate_birth_data_day_zero(self):
        """Test validation fails for day 0."""
        form_data = {
            'name': 'John Doe',
            'year': 1990,
            'month': 5,
            'day': 0,
            'hour': 14,
            'minute': 30,
            'city': 'Boston',
            'country': 'USA'
        }
        with pytest.raises(ValueError, match="Day must be between"):
            validate_birth_data(form_data)

    def test_validate_birth_data_day_boundary_1(self):
        """Test validation passes for day 1."""
        form_data = {
            'name': 'John Doe',
            'year': 1990,
            'month': 5,
            'day': 1,
            'hour': 14,
            'minute': 30,
            'city': 'Boston',
            'country': 'USA'
        }
        result = validate_birth_data(form_data)
        assert result['day'] == 1

    def test_validate_birth_data_day_boundary_31(self):
        """Test validation passes for day 31 (even for months with fewer days)."""
        # Note: We don't validate day-month combinations separately,
        # only in datetime() call, so day 31 passes basic check
        form_data = {
            'name': 'John Doe',
            'year': 1990,
            'month': 5,
            'day': 31,
            'hour': 14,
            'minute': 30,
            'city': 'Boston',
            'country': 'USA'
        }
        result = validate_birth_data(form_data)
        assert result['day'] == 31

    # Hour validation tests
    def test_validate_birth_data_invalid_hour(self):
        """Test validation fails for invalid hour."""
        form_data = {
            'name': 'John Doe',
            'year': 1990,
            'month': 5,
            'day': 15,
            'hour': 24,
            'minute': 30,
            'city': 'Boston',
            'country': 'USA'
        }
        with pytest.raises(ValueError, match="Hour must be between"):
            validate_birth_data(form_data)

    def test_validate_birth_data_hour_boundary_0(self):
        """Test validation passes for hour 0."""
        form_data = {
            'name': 'John Doe',
            'year': 1990,
            'month': 5,
            'day': 15,
            'hour': 0,
            'minute': 30,
            'city': 'Boston',
            'country': 'USA'
        }
        result = validate_birth_data(form_data)
        assert result['hour'] == 0

    def test_validate_birth_data_hour_boundary_23(self):
        """Test validation passes for hour 23."""
        form_data = {
            'name': 'John Doe',
            'year': 1990,
            'month': 5,
            'day': 15,
            'hour': 23,
            'minute': 30,
            'city': 'Boston',
            'country': 'USA'
        }
        result = validate_birth_data(form_data)
        assert result['hour'] == 23

    # Minute validation tests
    def test_validate_birth_data_invalid_minute(self):
        """Test validation fails for invalid minute."""
        form_data = {
            'name': 'John Doe',
            'year': 1990,
            'month': 5,
            'day': 15,
            'hour': 14,
            'minute': 60,
            'city': 'Boston',
            'country': 'USA'
        }
        with pytest.raises(ValueError, match="Minute must be between"):
            validate_birth_data(form_data)

    def test_validate_birth_data_minute_boundary_0(self):
        """Test validation passes for minute 0."""
        form_data = {
            'name': 'John Doe',
            'year': 1990,
            'month': 5,
            'day': 15,
            'hour': 14,
            'minute': 0,
            'city': 'Boston',
            'country': 'USA'
        }
        result = validate_birth_data(form_data)
        assert result['minute'] == 0

    def test_validate_birth_data_minute_boundary_59(self):
        """Test validation passes for minute 59."""
        form_data = {
            'name': 'John Doe',
            'year': 1990,
            'month': 5,
            'day': 15,
            'hour': 14,
            'minute': 59,
            'city': 'Boston',
            'country': 'USA'
        }
        result = validate_birth_data(form_data)
        assert result['minute'] == 59

    # Date/time combination validation tests
    def test_validate_birth_data_invalid_date_leap_year(self):
        """Test validation fails for invalid date like Feb 30."""
        form_data = {
            'name': 'John Doe',
            'year': 1990,
            'month': 2,
            'day': 30,
            'hour': 14,
            'minute': 30,
            'city': 'Boston',
            'country': 'USA'
        }
        with pytest.raises(ValueError, match="Invalid date/time"):
            validate_birth_data(form_data)

    def test_validate_birth_data_valid_leap_year_feb_29(self):
        """Test validation passes for Feb 29 in leap year."""
        form_data = {
            'name': 'John Doe',
            'year': 2000,  # Leap year
            'month': 2,
            'day': 29,
            'hour': 14,
            'minute': 30,
            'city': 'Boston',
            'country': 'USA'
        }
        result = validate_birth_data(form_data)
        assert result['day'] == 29
        assert result['month'] == 2

    def test_validate_birth_data_invalid_date_feb_29_non_leap(self):
        """Test validation fails for Feb 29 in non-leap year."""
        form_data = {
            'name': 'John Doe',
            'year': 1990,  # Not a leap year
            'month': 2,
            'day': 29,
            'hour': 14,
            'minute': 30,
            'city': 'Boston',
            'country': 'USA'
        }
        with pytest.raises(ValueError, match="Invalid date/time"):
            validate_birth_data(form_data)

    # Type coercion tests
    def test_validate_birth_data_string_numbers(self):
        """Test validation with numeric fields as strings."""
        form_data = {
            'name': 'John Doe',
            'year': '1990',
            'month': '5',
            'day': '15',
            'hour': '14',
            'minute': '30',
            'city': 'Boston',
            'country': 'USA'
        }
        result = validate_birth_data(form_data)

        assert result['year'] == 1990
        assert result['month'] == 5
        assert result['day'] == 15
        assert result['hour'] == 14
        assert result['minute'] == 30

    def test_validate_birth_data_all_fields_present_and_valid(self):
        """Test that all returned fields are present."""
        form_data = {
            'name': 'Jane Doe',
            'year': 1985,
            'month': 12,
            'day': 25,
            'hour': 9,
            'minute': 45,
            'city': 'New York',
            'region': 'NY',
            'country': 'USA'
        }
        result = validate_birth_data(form_data)

        expected_keys = {'name', 'year', 'month', 'day', 'hour', 'minute', 'city', 'region', 'country'}
        assert set(result.keys()) == expected_keys

