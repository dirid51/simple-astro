"""
Unit tests for chart generation service.

Tests cover:
- Successful chart generation with mocked subprocess
- Error handling (subprocess failures, missing files, size limits)
- Subprocess argument construction
- Path handling and safety
"""

import os
import subprocess
import tempfile
import uuid
from pathlib import Path
from unittest import mock

import pytest

from simpleastro.services.chart_service import (
    generate_chart,
    ChartGenerationError,
    ChartTooLargeError,
    ChartMissingError,
)


class TestGenerateChart:
    """Test chart generation service."""

    def test_generate_chart_success(self, tmp_path):
        """Test successful chart generation with mocked subprocess."""
        # Create a fake SVG file that would be generated
        job_id = "test_job_123"
        svg_filename = "John Doe - Natal Chart - test_job_123.svg"
        svg_path = tmp_path / svg_filename

        validated_data = {
            'name': 'John Doe',
            'year': 1990,
            'month': 5,
            'day': 15,
            'hour': 14,
            'minute': 30,
            'city': 'Boston',
            'country': 'USA'
        }

        # Mock subprocess to create the fake SVG file
        def mock_run(cmd, cwd=None, **kwargs):
            # The subprocess would have created this file
            svg_path.write_text('<svg>test</svg>')
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout='', stderr='')

        with mock.patch('subprocess.run', side_effect=mock_run):
            result = generate_chart(
                validated_data,
                output_dir=str(tmp_path),
                job_id=job_id
            )

        assert result['filename'] == svg_filename
        assert result['svg_path'] == str(svg_path)
        assert svg_path.exists()

    def test_generate_chart_success_with_auto_job_id(self, tmp_path):
        """Test successful generation with auto-generated job_id."""
        svg_filename_pattern = "John Doe - Natal Chart - *.svg"

        validated_data = {
            'name': 'John Doe',
            'year': 1990,
            'month': 5,
            'day': 15,
            'hour': 14,
            'minute': 30,
            'city': 'Boston',
            'country': 'USA'
        }

        def mock_run(cmd, cwd=None, **kwargs):
            # Create file with the name passed in the command
            filename = cmd[-1]  # Last argument is filename
            (Path(cwd) / filename).write_text('<svg>test</svg>')
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout='', stderr='')

        with mock.patch('subprocess.run', side_effect=mock_run):
            result = generate_chart(
                validated_data,
                output_dir=str(tmp_path)
                # Note: no job_id provided
            )

        assert result['filename'].startswith("John Doe - Natal Chart -")
        assert result['filename'].endswith(".svg")
        assert Path(result['svg_path']).exists()

    def test_generate_chart_subprocess_failure(self, tmp_path):
        """Test error handling when subprocess returns non-zero exit code."""
        validated_data = {
            'name': 'John Doe',
            'year': 1990,
            'month': 5,
            'day': 15,
            'hour': 14,
            'minute': 30,
            'city': 'Boston',
            'country': 'USA'
        }

        def mock_run(cmd, cwd=None, **kwargs):
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=1,
                stdout='',
                stderr='Kerykeion error: invalid coordinates'
            )

        with mock.patch('subprocess.run', side_effect=mock_run):
            with pytest.raises(ChartGenerationError, match="Chart generation failed"):
                generate_chart(
                    validated_data,
                    output_dir=str(tmp_path),
                    job_id="test_job"
                )

    def test_generate_chart_subprocess_timeout(self, tmp_path):
        """Test error handling when subprocess times out."""
        validated_data = {
            'name': 'John Doe',
            'year': 1990,
            'month': 5,
            'day': 15,
            'hour': 14,
            'minute': 30,
            'city': 'Boston',
            'country': 'USA'
        }

        def mock_run(cmd, cwd=None, **kwargs):
            raise subprocess.TimeoutExpired(cmd, timeout=60)

        with mock.patch('subprocess.run', side_effect=mock_run):
            with pytest.raises(ChartGenerationError, match="timed out"):
                generate_chart(
                    validated_data,
                    output_dir=str(tmp_path),
                    job_id="test_job"
                )

    def test_generate_chart_missing_file(self, tmp_path):
        """Test error when generated SVG file is not found."""
        validated_data = {
            'name': 'John Doe',
            'year': 1990,
            'month': 5,
            'day': 15,
            'hour': 14,
            'minute': 30,
            'city': 'Boston',
            'country': 'USA'
        }

        def mock_run(cmd, cwd=None, **kwargs):
            # Subprocess succeeds but doesn't create the file
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout='', stderr='')

        with mock.patch('subprocess.run', side_effect=mock_run):
            with pytest.raises(ChartMissingError, match="not found"):
                generate_chart(
                    validated_data,
                    output_dir=str(tmp_path),
                    job_id="test_job"
                )

    def test_generate_chart_size_limit_exceeded(self, tmp_path):
        """Test error when SVG exceeds size limit."""
        job_id = "test_job_123"
        svg_filename = "John Doe - Natal Chart - test_job_123.svg"
        svg_path = tmp_path / svg_filename

        validated_data = {
            'name': 'John Doe',
            'year': 1990,
            'month': 5,
            'day': 15,
            'hour': 14,
            'minute': 30,
            'city': 'Boston',
            'country': 'USA'
        }

        def mock_run(cmd, cwd=None, **kwargs):
            # Create an oversized file
            svg_path.write_text('<svg>' + 'x' * (11 * 1024 * 1024) + '</svg>')
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout='', stderr='')

        with mock.patch('subprocess.run', side_effect=mock_run):
            with pytest.raises(ChartTooLargeError, match="too large"):
                generate_chart(
                    validated_data,
                    output_dir=str(tmp_path),
                    job_id=job_id,
                    max_svg_size=10 * 1024 * 1024  # 10MB limit
                )

    def test_generate_chart_with_custom_size_limit(self, tmp_path):
        """Test that custom size limit is respected."""
        job_id = "test_job_123"
        svg_filename = "John Doe - Natal Chart - test_job_123.svg"
        svg_path = tmp_path / svg_filename

        validated_data = {
            'name': 'John Doe',
            'year': 1990,
            'month': 5,
            'day': 15,
            'hour': 14,
            'minute': 30,
            'city': 'Boston',
            'country': 'USA'
        }

        def mock_run(cmd, cwd=None, **kwargs):
            # Create a 2KB file
            svg_path.write_text('<svg>' + 'x' * 2000 + '</svg>')
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout='', stderr='')

        # Should fail with very small limit
        with mock.patch('subprocess.run', side_effect=mock_run):
            with pytest.raises(ChartTooLargeError):
                generate_chart(
                    validated_data,
                    output_dir=str(tmp_path),
                    job_id=job_id,
                    max_svg_size=1024  # Only 1KB allowed
                )

        # Should succeed with larger limit
        svg_path.unlink()
        with mock.patch('subprocess.run', side_effect=mock_run):
            result = generate_chart(
                validated_data,
                output_dir=str(tmp_path),
                job_id=job_id,
                max_svg_size=1024 * 1024  # 1MB allowed
            )
            assert result['svg_path'] == str(svg_path)

    def test_generate_chart_missing_output_dir(self, tmp_path):
        """Test error when output directory doesn't exist."""
        nonexistent_dir = str(tmp_path / "nonexistent" / "path")

        validated_data = {
            'name': 'John Doe',
            'year': 1990,
            'month': 5,
            'day': 15,
            'hour': 14,
            'minute': 30,
            'city': 'Boston',
            'country': 'USA'
        }

        with pytest.raises(FileNotFoundError, match="not found"):
            generate_chart(
                validated_data,
                output_dir=nonexistent_dir,
                job_id="test_job"
            )

    def test_generate_chart_special_characters_in_name(self, tmp_path):
        """Test that special characters in name are properly sanitized."""
        job_id = "test_job_123"

        validated_data = {
            'name': "John <O'Donnell> Smith",
            'year': 1990,
            'month': 5,
            'day': 15,
            'hour': 14,
            'minute': 30,
            'city': 'Boston',
            'country': 'USA'
        }

        def mock_run(cmd, cwd=None, **kwargs):
            # The filename will be sanitized
            filename = cmd[-1]
            (Path(cwd) / filename).write_text('<svg>test</svg>')
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout='', stderr='')

        with mock.patch('subprocess.run', side_effect=mock_run):
            result = generate_chart(
                validated_data,
                output_dir=str(tmp_path),
                job_id=job_id
            )

        # Special chars should be removed from filename
        assert '<' not in result['filename']
        assert '>' not in result['filename']
        assert "'" not in result['filename']
        assert result['filename'].endswith('.svg')

    def test_generate_chart_geonames_username_optional(self, tmp_path):
        """Test that geonames_username is optional."""
        job_id = "test_job_123"
        svg_filename = "John Doe - Natal Chart - test_job_123.svg"
        svg_path = tmp_path / svg_filename

        validated_data = {
            'name': 'John Doe',
            'year': 1990,
            'month': 5,
            'day': 15,
            'hour': 14,
            'minute': 30,
            'city': 'Boston',
            'country': 'USA'
        }

        def mock_run(cmd, cwd=None, **kwargs):
            svg_path.write_text('<svg>test</svg>')
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout='', stderr='')

        with mock.patch('subprocess.run', side_effect=mock_run):
            # Should work without geonames_username
            result = generate_chart(
                validated_data,
                output_dir=str(tmp_path),
                job_id=job_id
                # geonames_username not provided
            )

        assert result['filename'] == svg_filename

    def test_generate_chart_subprocess_exception(self, tmp_path):
        """Test error handling for unexpected subprocess exceptions."""
        validated_data = {
            'name': 'John Doe',
            'year': 1990,
            'month': 5,
            'day': 15,
            'hour': 14,
            'minute': 30,
            'city': 'Boston',
            'country': 'USA'
        }

        def mock_run(cmd, cwd=None, **kwargs):
            raise RuntimeError("Unexpected subprocess error")

        with mock.patch('subprocess.run', side_effect=mock_run):
            with pytest.raises(ChartGenerationError, match="Failed to execute"):
                generate_chart(
                    validated_data,
                    output_dir=str(tmp_path),
                    job_id="test_job"
                )

    def test_generate_chart_empty_stderr(self, tmp_path):
        """Test error handling when subprocess returns error with no stderr."""
        validated_data = {
            'name': 'John Doe',
            'year': 1990,
            'month': 5,
            'day': 15,
            'hour': 14,
            'minute': 30,
            'city': 'Boston',
            'country': 'USA'
        }

        def mock_run(cmd, cwd=None, **kwargs):
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=1,
                stdout='Some stdout message',
                stderr=''  # No stderr
            )

        with mock.patch('subprocess.run', side_effect=mock_run):
            with pytest.raises(ChartGenerationError):
                generate_chart(
                    validated_data,
                    output_dir=str(tmp_path),
                    job_id="test_job"
                )

    def test_generate_chart_command_construction(self, tmp_path):
        """Test that subprocess command is constructed correctly."""
        job_id = "test_job_123"
        svg_filename = "John Doe - Natal Chart - test_job_123.svg"
        svg_path = tmp_path / svg_filename

        validated_data = {
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

        captured_cmd = []

        def mock_run(cmd, cwd=None, **kwargs):
            captured_cmd.append(cmd)
            svg_path.write_text('<svg>test</svg>')
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout='', stderr='')

        with mock.patch('subprocess.run', side_effect=mock_run):
            generate_chart(
                validated_data,
                output_dir=str(tmp_path),
                job_id=job_id,
                geonames_username="test_user"
            )

        cmd = captured_cmd[0]
        # Command should be: [python, script, name, year, month, day, hour, minute, city, country, geonames, filename]
        assert cmd[0] == str(__import__('sys').executable)
        assert cmd[1].endswith('_generate_svg.py')
        assert 'John' in cmd[2]  # sanitized name
        assert cmd[3] == '1990'  # year
        assert cmd[4] == '5'     # month
        assert cmd[5] == '15'    # day
        assert cmd[6] == '14'    # hour
        assert cmd[7] == '30'    # minute
        assert cmd[8] == 'Boston'  # city
        assert cmd[9] == 'USA'     # country
        assert cmd[10] == 'test_user'  # geonames_username
        assert cmd[11] == svg_filename  # filename

