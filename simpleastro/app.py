import atexit
import logging
import os
import re
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, render_template, request, jsonify, url_for, send_file
from markupsafe import Markup

from simpleastro import llm_analyzer
from simpleastro.validators import validate_birth_data
from simpleastro.services import chart_service

# Optional imports for server-side markdown rendering and sanitization.
# Keep these optional so tests or environments with the packages can still import the module.
try:
    import markdown
except Exception:
    markdown = None

try:
    import bleach
except Exception:
    bleach = None

# Load environment variables from .env file
load_dotenv()

# Get GeoNames username from environment variable
# Warn if not set but don't raise at import time (defer validation to runtime)
GEONAMES_USERNAME = os.getenv('GEONAMES_USERNAME')
if not GEONAMES_USERNAME:
    # Will be logged after Flask app is initialized with logging handler
    pass

# Define SVG output directory (safe path traversal prevention)
SVG_OUTPUT_DIR = Path(__file__).parent / 'generated_charts'
SVG_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Ensure directory is writable
try:
    test_file = SVG_OUTPUT_DIR / '.write_test'
    test_file.touch()
    test_file.unlink()
except IOError as e:
    raise IOError(f"SVG output directory not writable: {e}")

# Configuration constants
MAX_SVG_SIZE = int(os.getenv('MAX_SVG_SIZE', 10 * 1024 * 1024))  # 10MB default
CHARTS_DIR = str(SVG_OUTPUT_DIR.resolve())  # Use project-local charts directory
JOB_RETENTION_MINUTES = int(os.getenv('JOB_RETENTION_MINUTES', 60))
JOB_TIMEOUT_SECONDS = int(os.getenv('JOB_TIMEOUT_SECONDS', 300))

app = Flask(__name__)

# Get debug mode from environment (default to False for safety)
FLASK_DEBUG = os.getenv('FLASK_DEBUG', 'False').lower() == 'true'
app.debug = FLASK_DEBUG

# Ensure INFO-level logs are emitted so app.logger.info calls appear in the console
app.logger.setLevel(logging.INFO)
# Attach a StreamHandler to stdout so logs appear in the terminal reliably
if not app.logger.handlers:
    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setLevel(logging.INFO)
    formatter = logging.Formatter('%(asctime)s %(levelname)s: %(message)s')
    handler.setFormatter(formatter)
    app.logger.addHandler(handler)

# Log warning if GEONAMES_USERNAME not configured
if not GEONAMES_USERNAME:
    app.logger.warning(
        "GEONAMES_USERNAME not set; online geonames lookups will fail. "
        "Set GEONAMES_USERNAME environment variable for full functionality."
    )


class JobStore:
    """Thread-safe in-memory job store with automatic expiration (TTL).

    Extended to support job types ('chart', 'analysis'),
    substatuses, and analysis result fields.
    """

    def __init__(self, retention_minutes=60):
        """
        Initialize job store.

        Args:
            retention_minutes: How long to keep completed jobs in memory
        """
        self.jobs = {}
        self.retention_seconds = retention_minutes * 60
        self.lock = threading.Lock()
        app.logger.info(f"JobStore initialized with {retention_minutes} minute retention")

    def add(self, job_id, status='pending', job_type='chart', chart_job_id=None, metadata=None):
        """
        Add a new job to the store.

        Args:
            job_id: Unique job identifier
            status: Initial status (default: 'pending')
            job_type: One of 'chart', 'analysis' (default: 'chart')
            chart_job_id: For analysis jobs, the id of the referenced chart job
            metadata: Optional dict of metadata to persist with the job
        """
        # Determine initial substatus based on job_type
        if job_type == 'chart':
            substatus = 'chart_pending'
        elif job_type == 'analysis':
            substatus = 'analysis_pending'
        else:
            substatus = None

        with self.lock:
            self.jobs[job_id] = {
                'status': status,
                'job_type': job_type,
                'substatus': substatus,
                'chart_job_id': chart_job_id,  # For analysis jobs
                'filename': None,
                'svg_path': None,  # Store path instead of content for memory efficiency
                'error': None,
                # Analysis-specific fields
                'analysis_report': None,
                'analysis_format': None,
                'analysis_started_at': None,
                'analysis_completed_at': None,
                'analysis_progress': 0,
                'created_at': datetime.now(),
                'metadata': metadata or {}
            }
        app.logger.info(f"Job {job_id}: Created with status '{status}' and type '{job_type}'")

    def get(self, job_id):
        """
        Retrieve a job, removing it if expired.

        Args:
            job_id: Job identifier

        Returns:
            Job dict or None if not found or expired
        """
        with self.lock:
            job = self.jobs.get(job_id)
            if job and self._is_expired(job):
                del self.jobs[job_id]
                app.logger.info(f"Job {job_id}: Expired and removed from store")
                return None
            return job

    def update(self, job_id, updates):
        """
        Update job fields atomically within lock.

        Args:
            job_id: Job identifier
            updates: Dict of fields to update
        """
        with self.lock:
            if job_id in self.jobs:
                self.jobs[job_id].update(updates)
                if 'status' in updates:
                    app.logger.info(f"Job {job_id}: Status updated to '{updates['status']}'")
                if 'substatus' in updates:
                    app.logger.info(f"Job {job_id}: Substatus updated to '{updates['substatus']}'")

    def _is_expired(self, job):
        """Check if a job has exceeded retention time."""
        age = (datetime.now() - job['created_at']).total_seconds()
        return age > self.retention_seconds

    def cleanup_expired(self):
        """Remove all expired jobs from store."""
        with self.lock:
            now = datetime.now()
            expired_ids = [
                jid for jid, job in self.jobs.items()
                if (now - job['created_at']).total_seconds() > self.retention_seconds
            ]
            for jid in expired_ids:
                del self.jobs[jid]
            if expired_ids:
                app.logger.info(f"JobStore cleanup: Removed {len(expired_ids)} expired jobs")
            return len(expired_ids)

    def job_count(self):
        """Get current number of jobs in store."""
        with self.lock:
            return len(self.jobs)


# Initialize job store and executor
job_store = JobStore(retention_minutes=JOB_RETENTION_MINUTES)
executor = ThreadPoolExecutor(max_workers=5)


def shutdown_executor():
    """Gracefully shutdown thread pool executor."""
    app.logger.info("Shutting down thread pool executor...")
    executor.shutdown(wait=True)
    app.logger.info("Thread pool executor shut down complete")


atexit.register(shutdown_executor)


def cleanup_worker():
    """Periodically clean up expired jobs."""
    while True:
        time.sleep(300)  # Every 5 minutes
        try:
            removed = job_store.cleanup_expired()
            if removed > 0:
                app.logger.info(f"JobStore: Cleaned up {removed} expired jobs, {job_store.job_count()} remaining")
        except Exception as e:
            app.logger.error(f"Error in cleanup worker: {e}")


# Start cleanup worker as daemon thread
cleanup_thread = threading.Thread(target=cleanup_worker, daemon=True)
cleanup_thread.start()
app.logger.info("Cleanup worker thread started")


def generate_chart(validated_data, job_id=None):
    """
    Generate chart using the chart service.

    Wrapper around chart_service.generate_chart that maps exceptions
    and provides backward compatibility.

    Args:
        validated_data: Dictionary with validated birth data
        job_id: Optional job ID for unique filename generation

    Returns:
        Dictionary with keys 'filename' and 'svg_path'

    Raises:
        ValueError: For file size errors
        FileNotFoundError: If generated SVG file cannot be found
        RuntimeError: For chart generation errors
    """
    try:
        return chart_service.generate_chart(
            validated_data,
            output_dir=CHARTS_DIR,
            job_id=job_id,
            geonames_username=GEONAMES_USERNAME,
            max_svg_size=MAX_SVG_SIZE
        )
    except chart_service.ChartTooLargeError as e:
        raise ValueError(str(e)) from e
    except chart_service.ChartMissingError as e:
        raise FileNotFoundError(str(e)) from e
    except chart_service.ChartGenerationError as e:
        raise RuntimeError(str(e)) from e


def generate_chart_job(job_id, form_data):
    """
    Background worker to generate chart asynchronously.

    This function is executed in a thread pool and updates the job store
    with the generated chart or any errors that occur.

    Args:
        job_id: Unique identifier for this job
        form_data: Form data dictionary to validate and process
    """
    app.logger.info(f"Job {job_id}: Background processing started")

    try:
        # Update status to running within atomic operation
        job_store.update(job_id, {'status': 'running', 'substatus': 'chart_running'})

        # Validate input data
        validated = validate_birth_data(form_data)
        app.logger.info(f"Job {job_id}: Input validation successful")

        # Generate chart using shared logic with job_id for unique filename
        result = generate_chart(validated, job_id=job_id)
        app.logger.info(f"Job {job_id}: Chart generation successful")

        # Update job with completion status and results (store path, not content)
        job_store.update(job_id, {
            'status': 'done',
            'substatus': 'chart_done',
            'filename': result['filename'],
            'svg_path': result['svg_path'],
            'error': None
        })
        app.logger.info(f"Job {job_id}: Completed successfully")

    except ValueError as e:
        # Input validation error - expected and handled
        app.logger.warning(f"Job {job_id}: Validation error: {e}")
        job_store.update(job_id, {
            'status': 'error',
            'filename': None,
            'svg_path': None,
            'error': str(e)
        })
    except FileNotFoundError as e:
        # Generated file not found - likely chart generation failed
        app.logger.warning(f"Job {job_id}: File not found: {e}")
        job_store.update(job_id, {
            'status': 'error',
            'filename': None,
            'svg_path': None,
            'error': f"Chart generation failed: {str(e)}"
        })
    except Exception as e:
        # Unexpected error - log full traceback for debugging
        app.logger.exception(f"Job {job_id}: Unexpected error during chart generation")
        job_store.update(job_id, {
            'status': 'error',
            'filename': None,
            'svg_path': None,
            'error': f"Unexpected error: {str(e)}"
        })


# Analysis job runner with LLM integration
def generate_analysis_job(job_id, chart_job_id=None, analysis_options=None):
    """
    Background worker to perform analysis for an existing chart using LLM.

    Args:
        job_id: Unique identifier for the analysis job
        chart_job_id: Job id of an existing chart job to read data from
        analysis_options: Optional dict of analysis preferences (e.g., {'focus': 'relationships'})
    """
    app.logger.info(f"Analysis Job {job_id}: Background analysis started")

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

        app.logger.info(f"Analysis Job {job_id}: Attempting LLM analysis")
        job_store.update(job_id, {'analysis_progress': 20})

        # Call LLM analyzer
        report = llm_analyzer.analyze_chart(chart_data, analysis_options)

        app.logger.info(f"Analysis Job {job_id}: LLM analysis completed")
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

        app.logger.info(f"Analysis Job {job_id}: Completed successfully")

    except Exception as e:
        app.logger.exception(f"Analysis Job {job_id}: Error during analysis")
        job_store.update(job_id, {
            'status': 'error',
            'substatus': 'analysis_error',
            'error': str(e),
            'analysis_completed_at': datetime.now()
        })


@app.route('/', methods=['GET'])
def index():
    # Render the main form page
    return render_template('index.html')

@app.route('/submit', methods=['POST'])
def submit():
    """Start an asynchronous job and return JSON with job id and status URL."""
    try:
        # Validate form data
        validated = validate_birth_data(request.form)
    except ValueError as e:
        app.logger.info(f"Submit request rejected due to validation error: {e}")
        return jsonify({'error': str(e)}), 400  # Bad Request

    job_id = uuid.uuid4().hex

    # Persist sanitized form data as metadata with the job for later analysis use
    metadata = {}
    try:
        # Use the validated dict created earlier
        metadata = {k: validated[k] for k in ['name', 'year', 'month', 'day', 'hour', 'minute', 'city', 'region', 'country']}
    except Exception:
        metadata = {}

    job_store.add(job_id, status='pending', metadata=metadata)

    # Submit to thread pool executor instead of creating raw threads
    executor.submit(generate_chart_job, job_id, dict(request.form))

    status_url = url_for('status_page', job_id=job_id)
    app.logger.info(f"Job {job_id}: Queued for async processing, status URL: {status_url}")
    return jsonify({'job_id': job_id, 'status_url': status_url}), 202  # Accepted

@app.route('/status/<job_id>', methods=['GET'])
def status_page(job_id):
    # Render status page; JS on that page will poll /api/status/<job_id>
    return render_template('status.html', job_id=job_id)

@app.route('/api/status/<job_id>', methods=['GET'])
def api_status(job_id):
    """Get status of a job with atomic consistency and comprehensive metadata."""
    job = job_store.get(job_id)
    if not job:
        app.logger.info(f"Status request for unknown/expired job: {job_id}")
        return jsonify({'status': 'unknown', 'error': 'job id not found'}), 404

    # Build comprehensive response with all relevant job metadata
    resp = {
        'status': job['status'],
        'job_type': job.get('job_type', 'chart'),
        'substatus': job.get('substatus'),
        'created_at': job['created_at'].isoformat() if job['created_at'] else None,
        'error': job.get('error')
    }

    # Add chart-specific fields
    if job['status'] == 'done' and job.get('job_type') == 'chart':
        resp['filename'] = job['filename']
        resp['svg_available'] = bool(job.get('svg_path'))

    # Add analysis-specific fields
    if job.get('job_type') == 'analysis':
        resp['chart_job_id'] = job.get('chart_job_id')
        resp['analysis_progress'] = job.get('analysis_progress', 0)
        resp['analysis_format'] = job.get('analysis_format')
        resp['analysis_started_at'] = job['analysis_started_at'].isoformat() if job.get('analysis_started_at') else None
        resp['analysis_completed_at'] = job['analysis_completed_at'].isoformat() if job.get('analysis_completed_at') else None
        if job['status'] == 'done' and job.get('analysis_report'):
            # Return snippet of report (avoid sending entire report via API)
            report = job['analysis_report']
            resp['analysis_report_snippet'] = report[:500] + "..." if len(report) > 500 else report

    app.logger.debug(f"Job {job_id}: Status={job['status']}, Type={job.get('job_type')}")
    return jsonify(resp)

@app.route('/job_svg/<job_id>', methods=['GET'])
def job_svg(job_id):
    """Return the generated SVG for a completed job by streaming from disk."""
    job = job_store.get(job_id)
    if not job:
        app.logger.info(f"SVG request for unknown/expired job: {job_id}")
        return "Job not found", 404

    if job['status'] != 'done' or not job.get('svg_path'):
        app.logger.warning(f"SVG request for incomplete job: {job_id} (status={job['status']})")
        return "SVG not available", 404

    # Verify file still exists
    svg_path = job['svg_path']
    if not os.path.exists(svg_path):
        app.logger.warning(f"SVG file missing for completed job: {job_id} at {svg_path}")
        return "SVG file not found", 404

    # Ensure the SVG file is within the expected output directory (defense-in-depth)
    try:
        abs_path = os.path.abspath(svg_path)
        charts_dir_abs = os.path.abspath(CHARTS_DIR)
        if os.path.commonpath([charts_dir_abs, abs_path]) != charts_dir_abs:
            app.logger.warning(f"SVG request for job {job_id} attempted to access file outside charts dir: {abs_path}")
            return "SVG file not found", 404
    except Exception as e:
        app.logger.exception(f"Error validating svg path for job {job_id}: {e}")
        return "SVG file not found", 404

    app.logger.info(f"Job {job_id}: SVG delivered from {svg_path}")
    return send_file(svg_path, mimetype='image/svg+xml', as_attachment=False)

@app.route('/analyze', methods=['POST'])
def analyze():
    """
    Start an asynchronous analysis job for an existing chart.

    Expected POST data:
    - job_id: ID of the existing chart job to analyze
    - analysis_focus: (optional) user-specified focus areas

    Returns:
    - {job_id, status_url, analysis_url} with 202 Accepted
    """
    try:
        chart_job_id = request.form.get('job_id') or request.json.get('job_id') if request.is_json else None

        if not chart_job_id:
            return jsonify({'error': 'chart job_id is required'}), 400

        # Validate that chart job exists and is done
        chart_job = job_store.get(chart_job_id)
        if not chart_job:
            return jsonify({'error': 'chart job not found or expired'}), 404

        if chart_job.get('status') != 'done':
            return jsonify({'error': 'chart job must be completed before analysis'}), 400

        # Extract optional analysis options from request
        analysis_options = None
        if request.is_json:
            analysis_options = request.json.get('analysis_options')

        # Create new analysis job
        analysis_job_id = uuid.uuid4().hex
        job_store.add(analysis_job_id, status='pending', job_type='analysis', chart_job_id=chart_job_id)

        # Submit analysis to thread pool
        executor.submit(generate_analysis_job, analysis_job_id, chart_job_id, analysis_options)

        status_url = url_for('api_analysis_status', job_id=analysis_job_id)
        analysis_url = url_for('analysis_page', job_id=analysis_job_id)

        app.logger.info(f"Analysis Job {analysis_job_id}: Queued for chart {chart_job_id}")
        return jsonify({
            'job_id': analysis_job_id,
            'status_url': status_url,
            'analysis_url': analysis_url
        }), 202

    except Exception as e:
        app.logger.exception("Error in /analyze endpoint")
        return jsonify({'error': 'Internal server error'}), 500

@app.route('/api/analysis/<job_id>', methods=['GET'])
def api_analysis_status(job_id):
    """
    Get status and partial report content for an analysis job.

    Returns:
    {
        'status': 'pending|running|done|error',
        'chart_job_id': 'id of referenced chart',
        'chart_status': 'done|...',
        'report': '...' (snippet if running, full if done),
        'report_format': 'markdown',
        'analysis_progress': 0-100,
        'error': '...' (if error),
        'analysis_started_at': ISO timestamp,
        'analysis_completed_at': ISO timestamp
    }
    """
    job = job_store.get(job_id)
    if not job:
        app.logger.info(f"Analysis status request for unknown/expired job: {job_id}")
        return jsonify({'status': 'unknown', 'error': 'job not found'}), 404

    if job.get('job_type') != 'analysis':
        app.logger.warning(f"Analysis status request for non-analysis job: {job_id}")
        return jsonify({'status': 'error', 'error': 'not an analysis job'}), 400

    resp = {
        'status': job['status'],
        'job_id': job_id,
        'chart_job_id': job.get('chart_job_id'),
        'analysis_progress': job.get('analysis_progress', 0),
        'analysis_started_at': job['analysis_started_at'].isoformat() if job.get('analysis_started_at') else None,
        'analysis_completed_at': job['analysis_completed_at'].isoformat() if job.get('analysis_completed_at') else None,
        'error': job.get('error')
    }

    # Include chart status if chart job is available
    chart_job_id = job.get('chart_job_id')
    if chart_job_id:
        chart_job = job_store.get(chart_job_id)
        if chart_job:
            resp['chart_status'] = chart_job.get('status')
            resp['chart_filename'] = chart_job.get('filename')

    # Include report details
    if job.get('analysis_report'):
        report = job['analysis_report']
        resp['report_format'] = job.get('analysis_format', 'markdown')

        # For done jobs, include full report; for running, include snippet
        if job['status'] == 'done':
            resp['report'] = report
        else:
            resp['report'] = report[:300] + "..." if len(report) > 300 else report

    app.logger.debug(f"Analysis Job {job_id}: Status={job['status']}, Progress={job.get('analysis_progress')}%")
    return jsonify(resp)

@app.route('/analysis/<job_id>', methods=['GET'])
def analysis_page(job_id):
    """
    Render the analysis report page with embedded chart.

    Returns HTML page displaying the full analysis report and referenced chart.
    """
    job = job_store.get(job_id)
    if not job:
        return render_template('analysis.html', error='Analysis job not found', job_id=job_id)

    if job.get('job_type') != 'analysis':
        return render_template('analysis.html', error='Not an analysis job', job_id=job_id)

    # Prepare data for template
    chart_job_id = job.get('chart_job_id')
    chart_data = {}

    if chart_job_id:
        chart_job = job_store.get(chart_job_id)
        if chart_job:
            chart_data = {
                'chart_filename': chart_job.get('filename'),
                'chart_url': url_for('job_svg', job_id=chart_job_id) if chart_job.get('svg_path') else None,
                'chart_job_id': chart_job_id
            }

    # Prepare analysis data
    analysis_data = {
        'job_id': job_id,
        'status': job.get('status'),
        'report': job.get('analysis_report', ''),
        'report_format': job.get('analysis_format', 'markdown'),
        'created_at': job.get('created_at'),
        'completed_at': job.get('analysis_completed_at'),
        'error': job.get('error')
    }

    # Render and sanitize report HTML server-side when the libraries are available
    raw_report = analysis_data['report']
    rendered = ''
    if raw_report and markdown and bleach:
        try:
            html_report = markdown.markdown(raw_report)
            allowed_tags = list(bleach.sanitizer.ALLOWED_TAGS) + ['p', 'h1', 'h2', 'h3', 'pre', 'code', 'blockquote', 'img']
            rendered = bleach.clean(html_report, tags=allowed_tags, attributes=bleach.sanitizer.ALLOWED_ATTRIBUTES, strip=True)
        except Exception:
            app.logger.exception(f"Failed to render/sanitize analysis report for job {job_id}")
            rendered = ''

    analysis_data['rendered_report'] = rendered

    app.logger.info(f"Analysis page rendered for job {job_id}")
    return render_template('analysis.html',
                          job_id=job_id,
                          analysis=analysis_data,
                          chart=chart_data)

# Keep backward-compatible quick test route that synchronously generates (optional)
@app.route('/sync-generate', methods=['POST'])
def sync_generate():
    """Synchronous chart generation for debugging and testing."""
    chart_svg = None
    if request.method == 'POST':
        try:
            # Validate input data
            validated = validate_birth_data(request.form)
            app.logger.info(f"Sync-generate request for {validated['name']}")

            # Use shared chart generation logic
            result = generate_chart(validated)

            # Read SVG from disk path
            with open(result['svg_path'], 'r', encoding='utf-8') as f:
                chart_svg = Markup(f.read())
            app.logger.info(f"Sync-generate: Successfully generated chart for {validated['name']}")

        except ValueError as e:
            app.logger.warning(f"Sync-generate validation error: {e}")
            chart_svg = f"Error: {str(e)}"
        except FileNotFoundError as e:
            app.logger.warning(f"Sync-generate file error: {e}")
            chart_svg = f"Error: Chart generation failed: {str(e)}"
        except Exception as e:
            app.logger.exception("Sync-generate: Unexpected error")
            chart_svg = f"Error generating chart: {e}"

    return render_template('index.html', chart_svg=chart_svg)

if __name__ == '__main__':
    # Disable the reloader so a single process is used (breakpoints attach reliably)
    app.run(debug=FLASK_DEBUG, use_reloader=False)
