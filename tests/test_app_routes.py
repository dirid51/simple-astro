"""
Unit tests for Flask routes and HTTP endpoints.

Tests cover:
- Route behavior with mocked services
- Request/response formats
- Error handling and status codes
- HTTP contract compliance
"""

import json
import uuid

import pytest


@pytest.fixture
def app():
    """Fixture to provide Flask test app."""
    from simpleastro.app import app
    app.config['TESTING'] = True
    return app


@pytest.fixture
def client(app):
    """Fixture to provide Flask test client."""
    return app.test_client()


class TestIndexRoute:
    """Test the main index route."""

    def test_index_returns_html(self, client):
        """Test that index returns HTML."""
        response = client.get('/')
        assert response.status_code == 200
        assert b'<!doctype' in response.data.lower() or b'<html' in response.data.lower()


class TestSubmitRoute:
    """Test the /submit POST endpoint."""

    def test_submit_with_valid_data(self, client):
        """Test submit with valid birth data."""
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

        response = client.post('/submit', data=form_data)

        assert response.status_code == 202  # Accepted
        data = json.loads(response.data)
        assert 'job_id' in data
        assert 'status_url' in data
        assert data['job_id']  # Should be non-empty

    def test_submit_with_invalid_year(self, client):
        """Test submit with invalid year."""
        form_data = {
            'name': 'John Doe',
            'year': 'invalid',
            'month': '5',
            'day': '15',
            'hour': '14',
            'minute': '30',
            'city': 'Boston',
            'country': 'USA'
        }

        response = client.post('/submit', data=form_data)

        assert response.status_code == 400  # Bad Request
        data = json.loads(response.data)
        assert 'error' in data

    def test_submit_with_missing_required_field(self, client):
        """Test submit with missing required field."""
        form_data = {
            'name': 'John Doe',
            'year': '1990',
            'month': '5',
            'day': '15',
            'hour': '14',
            'minute': '30',
            # Missing city
            'country': 'USA'
        }

        response = client.post('/submit', data=form_data)

        assert response.status_code == 400
        data = json.loads(response.data)
        assert 'error' in data


class TestStatusRoute:
    """Test the /status/<job_id> endpoint."""

    def test_status_page_returns_html(self, client):
        """Test that status page returns HTML."""
        job_id = uuid.uuid4().hex
        response = client.get(f'/status/{job_id}')

        assert response.status_code == 200
        assert b'<!doctype' in response.data.lower() or b'<html' in response.data.lower()


class TestApiStatusRoute:
    """Test the /api/status/<job_id> endpoint."""

    def test_api_status_for_nonexistent_job(self, client):
        """Test API status for non-existent job."""
        job_id = uuid.uuid4().hex
        response = client.get(f'/api/status/{job_id}')

        assert response.status_code == 404
        data = json.loads(response.data)
        assert 'error' in data

    def test_api_status_for_pending_job(self, client):
        """Test API status for pending job."""
        from simpleastro.app import job_store

        job_id = uuid.uuid4().hex
        job_store.add(job_id, status='pending', job_type='chart')

        response = client.get(f'/api/status/{job_id}')

        assert response.status_code == 200
        data = json.loads(response.data)
        assert data['status'] == 'pending'
        assert data['job_type'] == 'chart'

    def test_api_status_for_done_chart_job(self, client):
        """Test API status for completed chart job."""
        from simpleastro.app import job_store

        job_id = uuid.uuid4().hex
        job_store.add(job_id, status='done', job_type='chart')
        job_store.update(job_id, {
            'filename': 'John - Natal Chart - abc123.svg',
            'svg_path': '/path/to/chart.svg'
        })

        response = client.get(f'/api/status/{job_id}')

        assert response.status_code == 200
        data = json.loads(response.data)
        assert data['status'] == 'done'
        assert data['filename'] == 'John - Natal Chart - abc123.svg'
        assert data['svg_available'] is True

    def test_api_status_for_error_job(self, client):
        """Test API status for job with error."""
        from simpleastro.app import job_store

        job_id = uuid.uuid4().hex
        job_store.add(job_id, status='error', job_type='chart')
        job_store.update(job_id, {
            'error': 'Validation failed: invalid year'
        })

        response = client.get(f'/api/status/{job_id}')

        assert response.status_code == 200
        data = json.loads(response.data)
        assert data['status'] == 'error'
        assert 'error' in data


class TestJobSvgRoute:
    """Test the /job_svg/<job_id> endpoint."""

    def test_job_svg_for_nonexistent_job(self, client):
        """Test SVG endpoint for non-existent job."""
        job_id = uuid.uuid4().hex
        response = client.get(f'/job_svg/{job_id}')

        assert response.status_code == 404

    def test_job_svg_for_pending_job(self, client):
        """Test SVG endpoint for pending job."""
        from simpleastro.app import job_store

        job_id = uuid.uuid4().hex
        job_store.add(job_id, status='pending', job_type='chart')

        response = client.get(f'/job_svg/{job_id}')

        assert response.status_code == 404

    def test_job_svg_for_done_job_with_missing_file(self, client):
        """Test SVG endpoint for done job with missing file."""
        from simpleastro.app import job_store

        job_id = uuid.uuid4().hex
        job_store.add(job_id, status='done', job_type='chart')
        job_store.update(job_id, {
            'svg_path': '/nonexistent/path/chart.svg'
        })

        response = client.get(f'/job_svg/{job_id}')

        assert response.status_code == 404


class TestAnalyzeRoute:
    """Test the /analyze POST endpoint."""

    def test_analyze_without_chart_job_id(self, client):
        """Test analyze without providing chart job_id."""
        response = client.post('/analyze',
                              json={},
                              content_type='application/json')

        assert response.status_code == 400
        data = json.loads(response.data)
        assert 'error' in data

    def test_analyze_with_nonexistent_chart(self, client):
        """Test analyze with non-existent chart job."""
        nonexistent_id = uuid.uuid4().hex
        response = client.post('/analyze',
                              json={'job_id': nonexistent_id},
                              content_type='application/json')

        assert response.status_code == 404
        data = json.loads(response.data)
        assert 'error' in data

    def test_analyze_with_pending_chart(self, client):
        """Test analyze with pending chart job."""
        from simpleastro.app import job_store

        chart_job_id = uuid.uuid4().hex
        job_store.add(chart_job_id, status='pending', job_type='chart')

        response = client.post('/analyze',
                              json={'job_id': chart_job_id},
                              content_type='application/json')

        assert response.status_code == 400
        data = json.loads(response.data)
        assert 'error' in data

    def test_analyze_with_completed_chart(self, client):
        """Test analyze with completed chart job."""
        from simpleastro.app import job_store

        chart_job_id = uuid.uuid4().hex
        job_store.add(chart_job_id, status='done', job_type='chart')
        job_store.update(chart_job_id, {
            'svg_path': '/path/to/chart.svg',
            'filename': 'Test - Natal Chart - abc.svg'
        })

        response = client.post('/analyze',
                              json={'job_id': chart_job_id},
                              content_type='application/json')

        assert response.status_code == 202  # Accepted
        data = json.loads(response.data)
        assert 'job_id' in data
        assert 'status_url' in data


class TestApiAnalysisStatusRoute:
    """Test the /api/analysis/<job_id> endpoint."""

    def test_api_analysis_status_for_nonexistent_job(self, client):
        """Test analysis status for non-existent job."""
        job_id = uuid.uuid4().hex
        response = client.get(f'/api/analysis/{job_id}')

        assert response.status_code == 404
        data = json.loads(response.data)
        assert 'error' in data

    def test_api_analysis_status_for_non_analysis_job(self, client):
        """Test analysis status endpoint with chart job."""
        from simpleastro.app import job_store

        job_id = uuid.uuid4().hex
        job_store.add(job_id, status='done', job_type='chart')

        response = client.get(f'/api/analysis/{job_id}')

        assert response.status_code == 400
        data = json.loads(response.data)
        assert 'error' in data

    def test_api_analysis_status_for_pending_analysis(self, client):
        """Test analysis status for pending analysis job."""
        from simpleastro.app import job_store

        chart_job_id = uuid.uuid4().hex
        analysis_job_id = uuid.uuid4().hex

        job_store.add(chart_job_id, status='done', job_type='chart')
        job_store.add(analysis_job_id, status='pending', job_type='analysis', chart_job_id=chart_job_id)

        response = client.get(f'/api/analysis/{analysis_job_id}')

        assert response.status_code == 200
        data = json.loads(response.data)
        assert data['status'] == 'pending'
        assert data['chart_job_id'] == chart_job_id

    def test_api_analysis_status_for_completed_analysis(self, client):
        """Test analysis status for completed analysis job."""
        from simpleastro.app import job_store
        from datetime import datetime

        chart_job_id = uuid.uuid4().hex
        analysis_job_id = uuid.uuid4().hex

        job_store.add(chart_job_id, status='done', job_type='chart')
        job_store.add(analysis_job_id, status='done', job_type='analysis', chart_job_id=chart_job_id)
        job_store.update(analysis_job_id, {
            'analysis_report': 'This is a test analysis report',
            'analysis_format': 'markdown',
            'analysis_progress': 100,
            'analysis_completed_at': datetime.now()
        })

        response = client.get(f'/api/analysis/{analysis_job_id}')

        assert response.status_code == 200
        data = json.loads(response.data)
        assert data['status'] == 'done'
        assert 'report' in data
        assert data['analysis_progress'] == 100


class TestAnalysisPageRoute:
    """Test the /analysis/<job_id> endpoint."""

    def test_analysis_page_for_nonexistent_job(self, client):
        """Test analysis page for non-existent job."""
        job_id = uuid.uuid4().hex
        response = client.get(f'/analysis/{job_id}')

        assert response.status_code == 200
        assert b'html' in response.data.lower()  # Should still return HTML with error message

    def test_analysis_page_for_non_analysis_job(self, client):
        """Test analysis page with chart job."""
        from simpleastro.app import job_store

        job_id = uuid.uuid4().hex
        job_store.add(job_id, status='done', job_type='chart')

        response = client.get(f'/analysis/{job_id}')

        assert response.status_code == 200
        assert b'html' in response.data.lower()


class TestSyncGenerateRoute:
    """Test the /sync-generate POST endpoint."""

    def test_sync_generate_with_valid_data(self, client):
        """Test sync generate with valid birth data."""
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

        response = client.post('/sync-generate', data=form_data)

        assert response.status_code == 200
        # Should return HTML (index.html template)
        assert b'html' in response.data.lower()

    def test_sync_generate_with_invalid_data(self, client):
        """Test sync generate with invalid birth data."""
        form_data = {
            'name': 'John Doe',
            'year': 'invalid',
            'month': '5',
            'day': '15',
            'hour': '14',
            'minute': '30',
            'city': 'Boston',
            'country': 'USA'
        }

        response = client.post('/sync-generate', data=form_data)

        assert response.status_code == 200
        # Should return error message in HTML
        assert b'error' in response.data.lower() or b'html' in response.data.lower()


class TestRouteExistence:
    """Test that all required routes exist."""

    def test_all_required_routes_exist(self, app):
        """Test that all required routes are registered."""
        routes = {str(rule) for rule in app.url_map.iter_rules()}

        required_routes = {
            '/',
            '/submit',
            '/status/<job_id>',
            '/api/status/<job_id>',
            '/job_svg/<job_id>',
            '/analyze',
            '/api/analysis/<job_id>',
            '/analysis/<job_id>',
            '/sync-generate'
        }

        missing = required_routes - routes
        assert not missing, f"Missing routes: {missing}"

