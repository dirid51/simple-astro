"""
Unit tests for job handlers module.

Tests cover:
- Chart job generation with success and error cases
- Analysis job with success and error cases
- Job store updates and status tracking
- Error handling and logging
"""

import uuid
from datetime import datetime
from unittest import mock

import pytest


class TestGenerateChartJob:
    """Test chart generation job handler."""

    def test_generate_chart_job_success(self):
        """Test successful chart generation job."""
        from simpleastro.services import job_handlers

        job_id = "test_job_123"
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

        # Mock dependencies
        mock_validate = mock.Mock(return_value={
            'name': 'John Doe',
            'year': 1990,
            'month': 5,
            'day': 15,
            'hour': 14,
            'minute': 30,
            'city': 'Boston',
            'region': '',
            'country': 'USA'
        })

        mock_chart_fn = mock.Mock(return_value={
            'filename': 'John Doe - Natal Chart - test_job_123.svg',
            'svg_path': '/path/to/chart.svg'
        })

        mock_job_store = mock.Mock()

        # Execute
        job_handlers.generate_chart_job(
            job_id,
            form_data,
            validate_fn=mock_validate,
            chart_fn=mock_chart_fn,
            job_store=mock_job_store
        )

        # Verify validation was called
        mock_validate.assert_called_once_with(form_data)

        # Verify chart generation was called with job_id
        mock_chart_fn.assert_called_once()
        call_args = mock_chart_fn.call_args
        assert call_args[1]['job_id'] == job_id

        # Verify job store updates
        assert mock_job_store.update.call_count >= 2  # running, then done

        # Verify final status is done
        final_call = mock_job_store.update.call_args_list[-1]
        assert final_call[0][0] == job_id
        assert final_call[0][1]['status'] == 'done'
        assert final_call[0][1]['substatus'] == 'chart_done'

    def test_generate_chart_job_validation_error(self):
        """Test chart job handles validation errors."""
        from simpleastro.services import job_handlers

        job_id = "test_job_123"
        form_data = {'name': 'John', 'year': 'invalid'}

        # Mock dependencies
        mock_validate = mock.Mock(side_effect=ValueError("Year must be a valid integer"))
        mock_chart_fn = mock.Mock()
        mock_job_store = mock.Mock()

        # Execute
        job_handlers.generate_chart_job(
            job_id,
            form_data,
            validate_fn=mock_validate,
            chart_fn=mock_chart_fn,
            job_store=mock_job_store
        )

        # Verify chart generation was NOT called
        mock_chart_fn.assert_not_called()

        # Verify job store has error status
        assert mock_job_store.update.call_count >= 2
        final_call = mock_job_store.update.call_args_list[-1]
        assert final_call[0][1]['status'] == 'error'
        assert 'Year must be a valid integer' in final_call[0][1]['error']

    def test_generate_chart_job_generation_error(self):
        """Test chart job handles generation errors."""
        from simpleastro.services import job_handlers

        job_id = "test_job_123"
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

        # Mock dependencies
        mock_validate = mock.Mock(return_value={
            'name': 'John Doe',
            'year': 1990,
            'month': 5,
            'day': 15,
            'hour': 14,
            'minute': 30,
            'city': 'Boston',
            'region': '',
            'country': 'USA'
        })

        mock_chart_fn = mock.Mock(side_effect=FileNotFoundError("SVG not found"))
        mock_job_store = mock.Mock()

        # Execute
        job_handlers.generate_chart_job(
            job_id,
            form_data,
            validate_fn=mock_validate,
            chart_fn=mock_chart_fn,
            job_store=mock_job_store
        )

        # Verify job store has error status
        assert mock_job_store.update.call_count >= 2
        final_call = mock_job_store.update.call_args_list[-1]
        assert final_call[0][1]['status'] == 'error'
        assert 'Chart generation failed' in final_call[0][1]['error']

    def test_generate_chart_job_unexpected_error(self):
        """Test chart job handles unexpected errors."""
        from simpleastro.services import job_handlers

        job_id = "test_job_123"
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

        # Mock dependencies
        mock_validate = mock.Mock(return_value={
            'name': 'John Doe',
            'year': 1990,
            'month': 5,
            'day': 15,
            'hour': 14,
            'minute': 30,
            'city': 'Boston',
            'region': '',
            'country': 'USA'
        })

        mock_chart_fn = mock.Mock(side_effect=RuntimeError("Unexpected error"))
        mock_job_store = mock.Mock()

        # Execute
        job_handlers.generate_chart_job(
            job_id,
            form_data,
            validate_fn=mock_validate,
            chart_fn=mock_chart_fn,
            job_store=mock_job_store
        )

        # Verify job store has error status
        assert mock_job_store.update.call_count >= 2
        final_call = mock_job_store.update.call_args_list[-1]
        assert final_call[0][1]['status'] == 'error'
        assert 'Unexpected error' in final_call[0][1]['error']


class TestGenerateAnalysisJob:
    """Test analysis job handler."""

    def test_generate_analysis_job_success(self):
        """Test successful analysis job."""
        from simpleastro.services import job_handlers

        job_id = "analysis_job_123"
        chart_job_id = "chart_job_456"

        # Create mock chart job
        mock_chart_job = {
            'status': 'done',
            'svg_path': '/path/to/chart.svg',
            'filename': 'John Doe - Natal Chart - chart_job_456.svg',
            'metadata': {
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
        }

        # Create mock analysis job
        mock_analysis_job = {
            'chart_job_id': chart_job_id
        }

        # Mock dependencies
        mock_job_store = mock.Mock()
        mock_job_store.get = mock.Mock(side_effect=lambda jid: {
            job_id: mock_analysis_job,
            chart_job_id: mock_chart_job
        }.get(jid))

        mock_llm_analyzer = mock.Mock()
        mock_llm_analyzer.analyze_chart = mock.Mock(return_value="Analysis report")

        # Execute
        job_handlers.generate_analysis_job(
            job_id,
            chart_job_id=chart_job_id,
            analysis_options=None,
            llm_analyzer=mock_llm_analyzer,
            job_store=mock_job_store
        )

        # Verify job store was updated to done
        final_call = mock_job_store.update.call_args_list[-1]
        assert final_call[0][1]['status'] == 'done'
        assert final_call[0][1]['analysis_report'] == "Analysis report"
        assert final_call[0][1]['analysis_progress'] == 100

    def test_generate_analysis_job_missing_chart(self):
        """Test analysis job when chart job is not found."""
        from simpleastro.services import job_handlers

        job_id = "analysis_job_123"
        chart_job_id = "nonexistent_chart"

        # Create mock analysis job
        mock_analysis_job = {
            'chart_job_id': chart_job_id
        }

        # Mock dependencies
        mock_job_store = mock.Mock()
        mock_job_store.get = mock.Mock(side_effect=lambda jid: {
            job_id: mock_analysis_job,
            chart_job_id: None  # Chart job not found
        }.get(jid))

        mock_llm_analyzer = mock.Mock()

        # Execute
        job_handlers.generate_analysis_job(
            job_id,
            chart_job_id=chart_job_id,
            analysis_options=None,
            llm_analyzer=mock_llm_analyzer,
            job_store=mock_job_store
        )

        # Verify job store has error status
        final_call = mock_job_store.update.call_args_list[-1]
        assert final_call[0][1]['status'] == 'error'
        assert 'not found or expired' in final_call[0][1]['error']

    def test_generate_analysis_job_chart_not_done(self):
        """Test analysis job when chart is still running."""
        from simpleastro.services import job_handlers

        job_id = "analysis_job_123"
        chart_job_id = "chart_job_456"

        # Create mock chart job that's still running
        mock_chart_job = {
            'status': 'running',
            'svg_path': None
        }

        # Create mock analysis job
        mock_analysis_job = {
            'chart_job_id': chart_job_id
        }

        # Mock dependencies
        mock_job_store = mock.Mock()
        mock_job_store.get = mock.Mock(side_effect=lambda jid: {
            job_id: mock_analysis_job,
            chart_job_id: mock_chart_job
        }.get(jid))

        mock_llm_analyzer = mock.Mock()

        # Execute
        job_handlers.generate_analysis_job(
            job_id,
            chart_job_id=chart_job_id,
            analysis_options=None,
            llm_analyzer=mock_llm_analyzer,
            job_store=mock_job_store
        )

        # Verify job store has error status
        final_call = mock_job_store.update.call_args_list[-1]
        assert final_call[0][1]['status'] == 'error'
        assert 'must be done' in final_call[0][1]['error']

    def test_generate_analysis_job_llm_error(self):
        """Test analysis job handles LLM errors."""
        from simpleastro.services import job_handlers

        job_id = "analysis_job_123"
        chart_job_id = "chart_job_456"

        # Create mock chart job
        mock_chart_job = {
            'status': 'done',
            'svg_path': '/path/to/chart.svg',
            'filename': 'John Doe - Natal Chart - chart_job_456.svg',
            'metadata': {
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
        }

        # Create mock analysis job
        mock_analysis_job = {
            'chart_job_id': chart_job_id
        }

        # Mock dependencies
        mock_job_store = mock.Mock()
        mock_job_store.get = mock.Mock(side_effect=lambda jid: {
            job_id: mock_analysis_job,
            chart_job_id: mock_chart_job
        }.get(jid))

        mock_llm_analyzer = mock.Mock()
        mock_llm_analyzer.analyze_chart = mock.Mock(side_effect=ConnectionError("LLM unavailable"))

        # Execute
        job_handlers.generate_analysis_job(
            job_id,
            chart_job_id=chart_job_id,
            analysis_options=None,
            llm_analyzer=mock_llm_analyzer,
            job_store=mock_job_store
        )

        # Verify job store has error status
        final_call = mock_job_store.update.call_args_list[-1]
        assert final_call[0][1]['status'] == 'error'
        assert 'LLM unavailable' in final_call[0][1]['error']

    def test_generate_analysis_job_progress_updates(self):
        """Test that analysis job updates progress."""
        from simpleastro.services import job_handlers

        job_id = "analysis_job_123"
        chart_job_id = "chart_job_456"

        # Create mock chart job
        mock_chart_job = {
            'status': 'done',
            'svg_path': '/path/to/chart.svg',
            'filename': 'John Doe - Natal Chart - chart_job_456.svg',
            'metadata': {
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
        }

        # Create mock analysis job
        mock_analysis_job = {
            'chart_job_id': chart_job_id
        }

        # Mock dependencies
        mock_job_store = mock.Mock()
        mock_job_store.get = mock.Mock(side_effect=lambda jid: {
            job_id: mock_analysis_job,
            chart_job_id: mock_chart_job
        }.get(jid))

        mock_llm_analyzer = mock.Mock()
        mock_llm_analyzer.analyze_chart = mock.Mock(return_value="Analysis report")

        # Execute
        job_handlers.generate_analysis_job(
            job_id,
            chart_job_id=chart_job_id,
            analysis_options=None,
            llm_analyzer=mock_llm_analyzer,
            job_store=mock_job_store
        )

        # Verify progress was updated
        update_calls = mock_job_store.update.call_args_list
        progress_values = [
            call[0][1].get('analysis_progress')
            for call in update_calls
            if 'analysis_progress' in call[0][1]
        ]

        # Should have progress: 0, 10, 20, 90, 100
        assert 0 in progress_values
        assert 10 in progress_values
        assert 20 in progress_values
        assert 100 in progress_values

