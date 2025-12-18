"""
Unit tests for app.py fixes based on code review recommendations.

Tests cover:
- Job store transitions and state management
- Filename sanitization and collision prevention
- Analysis job metadata and chart_job_id reference
- API status response completeness
- Chart generation with file-based storage
"""

import os
import tempfile
import threading
import uuid
from datetime import datetime
from pathlib import Path
from unittest import mock

import pytest

# Setup path to import simpleastro
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from simpleastro.app import (
    JobStore,
    sanitize_filename,
    app,
    job_store,
    CHARTS_DIR,
)


class TestFileSanitization:
    """Test filename sanitization and collision prevention."""

    def test_sanitize_filename_removes_path_separators(self):
        """Test that path separators are removed from filenames."""
        unsafe_name = "John ../../../etc/passwd"
        job_id = uuid.uuid4().hex
        result = sanitize_filename(unsafe_name, job_id)
        assert "/" not in result
        assert "\\" not in result
        assert ".." not in result
        assert result.endswith(".svg")

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

    def test_sanitize_filename_limits_length(self):
        """Test that name is limited to reasonable length."""
        long_name = "A" * 200
        job_id = uuid.uuid4().hex
        result = sanitize_filename(long_name, job_id)
        # Check that the safe name part doesn't exceed ~50 chars before the job_id
        assert len(result) <= 100  # Reasonable overall limit

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


class TestJobStoreTransitions:
    """Test job state transitions in JobStore."""

    def test_job_store_chart_transitions(self):
        """Test chart job status transitions: pending -> running -> done."""
        store = JobStore(retention_minutes=60)
        job_id = uuid.uuid4().hex

        # Create chart job
        store.add(job_id, status='pending', job_type='chart')
        job = store.get(job_id)
        assert job['status'] == 'pending'
        assert job['substatus'] == 'chart_pending'
        assert job['job_type'] == 'chart'

        # Update to running
        store.update(job_id, {'status': 'running', 'substatus': 'chart_running'})
        job = store.get(job_id)
        assert job['status'] == 'running'
        assert job['substatus'] == 'chart_running'

        # Update to done
        store.update(job_id, {
            'status': 'done',
            'substatus': 'chart_done',
            'filename': 'test.svg',
            'svg_path': '/tmp/test.svg'
        })
        job = store.get(job_id)
        assert job['status'] == 'done'
        assert job['substatus'] == 'chart_done'
        assert job['filename'] == 'test.svg'
        assert job['svg_path'] == '/tmp/test.svg'

    def test_job_store_analysis_transitions(self):
        """Test analysis job status transitions with chart_job_id reference."""
        store = JobStore(retention_minutes=60)
        chart_job_id = uuid.uuid4().hex
        analysis_job_id = uuid.uuid4().hex

        # Create chart job first
        store.add(chart_job_id, status='pending', job_type='chart')
        store.update(chart_job_id, {
            'status': 'done',
            'substatus': 'chart_done',
            'filename': 'chart.svg',
            'svg_path': '/tmp/chart.svg'
        })

        # Create analysis job with chart_job_id reference
        store.add(
            analysis_job_id,
            status='pending',
            job_type='analysis',
            chart_job_id=chart_job_id
        )

        job = store.get(analysis_job_id)
        assert job['status'] == 'pending'
        assert job['substatus'] == 'analysis_pending'
        assert job['job_type'] == 'analysis'
        assert job['chart_job_id'] == chart_job_id

        # Update to running
        store.update(analysis_job_id, {
            'status': 'running',
            'substatus': 'analysis_running',
            'analysis_started_at': datetime.now()
        })
        job = store.get(analysis_job_id)
        assert job['status'] == 'running'
        assert job['substatus'] == 'analysis_running'

        # Update to done
        store.update(analysis_job_id, {
            'status': 'done',
            'substatus': 'analysis_done',
            'analysis_report': 'Test report',
            'analysis_format': 'markdown',
            'analysis_progress': 100,
            'analysis_completed_at': datetime.now()
        })
        job = store.get(analysis_job_id)
        assert job['status'] == 'done'
        assert job['analysis_report'] == 'Test report'

    def test_job_store_error_state(self):
        """Test job error state handling."""
        store = JobStore(retention_minutes=60)
        job_id = uuid.uuid4().hex

        store.add(job_id, status='pending', job_type='chart')
        store.update(job_id, {
            'status': 'error',
            'error': 'Invalid input data'
        })

        job = store.get(job_id)
        assert job['status'] == 'error'
        assert job['error'] == 'Invalid input data'


class TestAPIStatusResponse:
    """Test /api/status endpoint response structure."""

    def test_api_status_chart_job_response_structure(self):
        """Test that API returns job_type, substatus, and chart fields."""
        with app.test_client() as client:
            # Create a chart job manually
            job_id = uuid.uuid4().hex
            job_store.add(job_id, status='pending', job_type='chart')
            job_store.update(job_id, {
                'status': 'done',
                'substatus': 'chart_done',
                'filename': 'test - Natal Chart - abc123.svg',
                'svg_path': '/tmp/test.svg'
            })

            # Call API
            response = client.get(f'/api/status/{job_id}')
            data = response.get_json()

            # Verify response structure
            assert data['status'] == 'done'
            assert data['job_type'] == 'chart'
            assert data['substatus'] == 'chart_done'
            assert 'created_at' in data
            assert data['filename'] == 'test - Natal Chart - abc123.svg'
            assert data['svg_available'] is True

    def test_api_status_analysis_job_response_structure(self):
        """Test that API returns analysis-specific fields for analysis jobs."""
        with app.test_client() as client:
            chart_job_id = uuid.uuid4().hex
            analysis_job_id = uuid.uuid4().hex

            # Setup chart job
            job_store.add(chart_job_id, status='pending', job_type='chart')
            job_store.update(chart_job_id, {
                'status': 'done',
                'substatus': 'chart_done',
                'svg_path': '/tmp/chart.svg'
            })

            # Setup analysis job
            job_store.add(
                analysis_job_id,
                status='pending',
                job_type='analysis',
                chart_job_id=chart_job_id
            )
            job_store.update(analysis_job_id, {
                'status': 'done',
                'substatus': 'analysis_done',
                'analysis_progress': 100,
                'analysis_format': 'markdown',
                'analysis_report': 'Test analysis report',
                'analysis_started_at': datetime.now(),
                'analysis_completed_at': datetime.now()
            })

            # Call API
            response = client.get(f'/api/status/{analysis_job_id}')
            data = response.get_json()

            # Verify analysis-specific fields
            assert data['status'] == 'done'
            assert data['job_type'] == 'analysis'
            assert data['substatus'] == 'analysis_done'
            assert data['chart_job_id'] == chart_job_id
            assert data['analysis_progress'] == 100
            assert data['analysis_format'] == 'markdown'
            assert 'analysis_started_at' in data
            assert 'analysis_completed_at' in data
            assert 'analysis_report_snippet' in data

    def test_api_status_nonexistent_job(self):
        """Test API response for nonexistent job."""
        with app.test_client() as client:
            response = client.get(f'/api/status/nonexistent-{uuid.uuid4().hex}')
            assert response.status_code == 404
            data = response.get_json()
            assert data['status'] == 'unknown'
            assert 'error' in data


class TestSVGPathStorage:
    """Test that SVG paths are stored instead of full content."""

    def test_job_store_stores_svg_path_not_content(self):
        """Test that job store stores svg_path instead of svg content."""
        store = JobStore(retention_minutes=60)
        job_id = uuid.uuid4().hex
        test_path = '/tmp/test.svg'

        store.add(job_id, status='pending', job_type='chart')
        store.update(job_id, {
            'status': 'done',
            'svg_path': test_path
        })

        job = store.get(job_id)
        assert job['svg_path'] == test_path
        # Verify 'svg' field doesn't exist or is None
        assert job.get('svg') is None

    def test_charts_dir_uses_project_local_directory(self):
        """Test that CHARTS_DIR uses project-local generated_charts, not home directory."""
        # CHARTS_DIR should not be the user's home directory
        home_dir = os.path.expanduser('~')
        assert CHARTS_DIR != home_dir
        # CHARTS_DIR should contain 'generated_charts'
        assert 'generated_charts' in CHARTS_DIR


class TestAnalysisJobChartReference:
    """Test analysis job chart_job_id handling."""

    def test_analysis_job_stores_chart_job_id(self):
        """Test that analysis job stores and retrieves chart_job_id."""
        store = JobStore(retention_minutes=60)
        chart_job_id = uuid.uuid4().hex
        analysis_job_id = uuid.uuid4().hex

        store.add(
            analysis_job_id,
            status='pending',
            job_type='analysis',
            chart_job_id=chart_job_id
        )

        job = store.get(analysis_job_id)
        assert job['chart_job_id'] == chart_job_id

    def test_analysis_job_chart_job_id_in_api_response(self):
        """Test that chart_job_id appears in API response."""
        with app.test_client() as client:
            chart_job_id = uuid.uuid4().hex
            analysis_job_id = uuid.uuid4().hex

            job_store.add(
                analysis_job_id,
                status='pending',
                job_type='analysis',
                chart_job_id=chart_job_id
            )

            response = client.get(f'/api/status/{analysis_job_id}')
            data = response.get_json()

            assert 'chart_job_id' in data
            assert data['chart_job_id'] == chart_job_id


class TestMemorySafety:
    """Test memory-safe storage practices."""

    def test_job_store_does_not_store_svg_content_in_memory(self):
        """Test that SVG content is not stored in memory."""
        store = JobStore(retention_minutes=60)
        job_id = uuid.uuid4().hex

        # Create job with path, not content
        store.add(job_id, status='pending', job_type='chart')

        # The 'svg' field should not exist in job store
        job = store.get(job_id)
        assert 'svg' not in job or job.get('svg') is None

    def test_job_store_stores_only_path_metadata(self):
        """Test that only path and metadata are stored, not file content."""
        store = JobStore(retention_minutes=60)
        job_id = uuid.uuid4().hex

        store.add(job_id, status='pending', job_type='chart')
        store.update(job_id, {
            'status': 'done',
            'filename': 'test.svg',
            'svg_path': '/tmp/test.svg'
        })

        job = store.get(job_id)
        # Only filename and path should be present
        assert 'filename' in job
        assert 'svg_path' in job
        # Content should not be loaded into memory
        assert job['svg_path'] == '/tmp/test.svg'


if __name__ == '__main__':
    pytest.main([__file__, '-v'])

