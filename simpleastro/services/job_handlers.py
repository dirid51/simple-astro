"""
Job handlers module for background task execution.

This module contains the orchestration logic for chart generation and analysis
jobs. Handlers are designed to be executed in a thread pool and update the job
store with progress and results.
"""

import logging
from datetime import datetime
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def generate_chart_job(
    job_id: str,
    form_data: Dict[str, Any],
    *,
    validate_fn,
    chart_fn,
    job_store
) -> None:
    """
    Background worker to generate chart asynchronously.

    This function is executed in a thread pool and updates the job store
    with the generated chart or any errors that occur.

    Args:
        job_id: Unique identifier for this job
        form_data: Form data dictionary to validate and process
        validate_fn: Validation function (validators.validate_birth_data)
        chart_fn: Chart generation function (app.generate_chart)
        job_store: JobStore instance

    Returns:
        None (updates are written to job_store)

    Raises:
        No exceptions are raised; all errors are caught and stored in job_store.
    """
    logger.info(f"Job {job_id}: Background processing started")

    try:
        # Update status to running within atomic operation
        job_store.update(job_id, {'status': 'running', 'substatus': 'chart_running'})

        # Validate input data
        validated = validate_fn(form_data)
        logger.info(f"Job {job_id}: Input validation successful")

        # Generate chart using the provided function
        result = chart_fn(validated, job_id=job_id)
        logger.info(f"Job {job_id}: Chart generation successful")

        # Update job with completion status and results (store path, not content)
        job_store.update(job_id, {
            'status': 'done',
            'substatus': 'chart_done',
            'filename': result['filename'],
            'svg_path': result['svg_path'],
            'error': None
        })
        logger.info(f"Job {job_id}: Completed successfully")

    except ValueError as e:
        # Input validation error - expected and handled
        logger.warning(f"Job {job_id}: Validation error: {e}")
        job_store.update(job_id, {
            'status': 'error',
            'filename': None,
            'svg_path': None,
            'error': str(e)
        })
    except FileNotFoundError as e:
        # Generated file not found - likely chart generation failed
        logger.warning(f"Job {job_id}: File not found: {e}")
        job_store.update(job_id, {
            'status': 'error',
            'filename': None,
            'svg_path': None,
            'error': f"Chart generation failed: {str(e)}"
        })
    except Exception as e:
        # Unexpected error - log full traceback for debugging
        logger.exception(f"Job {job_id}: Unexpected error during chart generation")
        job_store.update(job_id, {
            'status': 'error',
            'filename': None,
            'svg_path': None,
            'error': f"Unexpected error: {str(e)}"
        })


def generate_analysis_job(
    job_id: str,
    *,
    chart_job_id: Optional[str] = None,
    analysis_options: Optional[Dict[str, Any]] = None,
    llm_analyzer,
    job_store
) -> None:
    """
    Background worker to perform analysis for an existing chart using LLM.

    This function is executed in a thread pool and updates the job store
    with analysis progress and results.

    Args:
        job_id: Unique identifier for the analysis job
        chart_job_id: Optional job id of chart to analyze (or stored in job_store)
        analysis_options: Optional dict of analysis preferences (e.g., {'focus': 'relationships'})
        llm_analyzer: LLM analyzer module
        job_store: JobStore instance

    Returns:
        None (updates are written to job_store)

    Raises:
        No exceptions are raised; all errors are caught and stored in job_store.
    """
    logger.info(f"Analysis Job {job_id}: Background analysis started")

    try:
        # Mark analysis job as running
        job_store.update(job_id, {
            'status': 'running',
            'substatus': 'analysis_running',
            'analysis_started_at': datetime.now(),
            'analysis_progress': 0
        })

        # Retrieve the analysis job to get chart_job_id
        analysis_job = job_store.get(job_id)
        if not analysis_job:
            raise ValueError('Analysis job not found')

        stored_chart_job_id = analysis_job.get('chart_job_id') or chart_job_id
        if not stored_chart_job_id:
            raise ValueError('chart_job_id not provided and not found in job metadata')

        # Locate and validate chart job
        chart_job = job_store.get(stored_chart_job_id)
        if not chart_job:
            raise ValueError(f'Referenced chart job not found or expired: {stored_chart_job_id}')

        if chart_job.get('status') != 'done' or not chart_job.get('svg_path'):
            raise ValueError('Referenced chart is not available (chart must be done)')

        # Update progress
        job_store.update(job_id, {'analysis_progress': 10})

        # Load birth data from chart generation metadata if available.
        metadata = chart_job.get('metadata') or {}
        if metadata:
            person_name = metadata.get('name') or chart_job.get('filename', '').replace(' - Natal Chart', '').split(' - ')[0]
            birth_data = {
                'name': metadata.get('name'),
                'year': metadata.get('year'),
                'month': metadata.get('month'),
                'day': metadata.get('day'),
                'hour': metadata.get('hour'),
                'minute': metadata.get('minute'),
                'city': metadata.get('city'),
                'region': metadata.get('region'),
                'country': metadata.get('country')
            }
        else:
            person_name = chart_job.get('filename', '').replace(' - Natal Chart', '').split(' - ')[0]
            birth_data = {}

        chart_data = {
            'person_name': person_name,
            'birth_data': birth_data,
            'planets': {},
            'houses': {},
            'angles': {},
            'aspects': [],
            'elemental_distribution': {},
            'modality_distribution': {},
            'aspect_patterns': []
        }

        logger.info(f"Analysis Job {job_id}: Attempting LLM analysis")
        job_store.update(job_id, {'analysis_progress': 20})

        # Call LLM analyzer
        report = llm_analyzer.analyze_chart(chart_data, analysis_options)

        logger.info(f"Analysis Job {job_id}: LLM analysis completed")
        job_store.update(job_id, {'analysis_progress': 90})

        # Update job with analysis results
        job_store.update(job_id, {
            'analysis_report': report,
            'analysis_format': 'markdown',
            'analysis_progress': 100,
            'analysis_completed_at': datetime.now(),
            'substatus': 'analysis_done',
            'status': 'done'
        })

        logger.info(f"Analysis Job {job_id}: Completed successfully")

    except Exception as e:
        logger.exception(f"Analysis Job {job_id}: Error during analysis")
        job_store.update(job_id, {
            'status': 'error',
            'substatus': 'analysis_error',
            'error': str(e),
            'analysis_completed_at': datetime.now()
        })

