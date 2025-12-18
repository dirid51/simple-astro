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
from kerykeion import AstrologicalSubject, KerykeionChartSVG
from markupsafe import Markup

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

    def add(self, job_id, status='pending', job_type='chart', chart_job_id=None):
        """
        Add a new job to the store.

        Args:
            job_id: Unique job identifier
            status: Initial status (default: 'pending')
            job_type: One of 'chart', 'analysis' (default: 'chart')
            chart_job_id: For analysis jobs, the id of the referenced chart job
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
                'created_at': datetime.now()
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

def sanitize_filename(name, job_id):
    """
    Sanitize and uniquify a filename to prevent path traversal and collisions.

    Args:
        name: Original name from user input
        job_id: Unique job identifier for uniqueness

    Returns:
        Safe filename with .svg extension
    """
    # Remove path separators and control characters
    safe_name = re.sub(r'[^A-Za-z0-9 _\-]', '', name).strip()
    # Limit to 50 characters to keep overall filename reasonable
    safe_name = safe_name[:50] if safe_name else 'Chart'
    # Create unique filename with job_id to prevent collisions
    unique_filename = f"{safe_name} - Natal Chart - {job_id}.svg"
    return unique_filename

def validate_birth_data(form_data):
    """
    Validate and sanitize birth data from form.

    Args:
        form_data: Dictionary containing form fields

    Returns:
        Dictionary with validated birth data

    Raises:
        ValueError: If validation fails
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

        # Validate actual date
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


def generate_chart(validated_data, job_id=None):
    """
    Shared logic for chart generation.

    This function encapsulates the core chart generation process used by both
    async and sync routes to prevent code duplication.

    Args:
        validated_data: Dictionary with validated birth data
        job_id: Optional job ID for unique filename generation

    Returns:
        Dictionary with keys 'filename' and 'svg_path'

    Raises:
        ValueError: For validation or file size errors
        FileNotFoundError: If generated SVG file cannot be found
        Exception: For chart generation errors
    """
    # Save current directory and change to charts directory
    original_dir = os.getcwd()
    os.chdir(CHARTS_DIR)

    try:
        # Initialize Subject with GeoNames integration
        subject = AstrologicalSubject(
            validated_data['name'],
            validated_data['year'],
            validated_data['month'],
            validated_data['day'],
            validated_data['hour'],
            validated_data['minute'],
            city=validated_data['city'],
            nation=validated_data['country'],
            online=True,
            geonames_username=GEONAMES_USERNAME
        )

        # Generate the SVG (will be saved to CHARTS_DIR)
        chart_generator = KerykeionChartSVG(subject)
        chart_generator.makeSVG()

        # Sanitize and uniquify filename using job_id if provided
        if job_id:
            safe_filename = sanitize_filename(validated_data['name'], job_id)
        else:
            # Fallback for sync routes: use uuid if job_id not provided
            safe_filename = sanitize_filename(validated_data['name'], uuid.uuid4().hex)

        # The Kerykeion library generates files with pattern: {name} - Natal Chart.svg
        # Find the generated file (use most recent if exists due to Kerykeion's naming)
        kerykeion_filename = f"{validated_data['name']} - Natal Chart.svg"
        kerykeion_path = os.path.join(CHARTS_DIR, kerykeion_filename)

        if not os.path.exists(kerykeion_path):
            raise FileNotFoundError(f"Generated SVG not found at: {kerykeion_path}")

        # Rename to our safe filename
        safe_svg_path = os.path.join(CHARTS_DIR, safe_filename)
        os.rename(kerykeion_path, safe_svg_path)

        # Check file size to prevent memory exhaustion
        svg_size = os.path.getsize(safe_svg_path)
        if svg_size > MAX_SVG_SIZE:
            raise ValueError(f"SVG too large: {svg_size} bytes (max: {MAX_SVG_SIZE})")

        return {
            'filename': safe_filename,
            'svg_path': safe_svg_path
        }
    finally:
        # Restore original directory
        os.chdir(original_dir)

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


# New: Analysis job runner
def generate_analysis_job(job_id, chart_job_id=None, analysis_options=None):
    """
    Background worker to perform analysis for an existing chart.

    Args:
        job_id: Unique identifier for the analysis job (the job entry in JobStore)
        chart_job_id: Optional job id of an existing chart job to read SVG/data from.
        analysis_options: Optional dict of analysis preferences
    """
    app.logger.info(f"Analysis Job {job_id}: Background analysis started")

    try:
        # Mark analysis job as running
        job_store.update(job_id, {
            'status': 'running',
            'substatus': 'analysis_running',
            'analysis_started_at': datetime.now()
        })

        # Retrieve the chart job id from job metadata
        analysis_job = job_store.get(job_id)
        if not analysis_job:
            raise ValueError('Analysis job not found')

        stored_chart_job_id = analysis_job.get('chart_job_id') or chart_job_id
        if not stored_chart_job_id:
            raise ValueError('chart_job_id not provided and not found in job metadata')

        # Attempt to locate chart data
        chart_job = job_store.get(stored_chart_job_id)
        if not chart_job:
            raise ValueError(f'Referenced chart job not found or expired: {stored_chart_job_id}')

        if chart_job.get('status') != 'done' or not chart_job.get('svg_path'):
            raise ValueError('Referenced chart is not available (chart must be done)')

        # Placeholder analysis behavior: for now produce a minimal report
        # The full LLM-based implementation will be added in a later step.
        report = (
            f"Analysis report (placeholder) for job {job_id}\n"
            f"Chart filename: {chart_job.get('filename')}\n"
            f"Chart job id: {stored_chart_job_id}\n"
            f"Generated at: {datetime.now().isoformat()}\n"
            "\nFurther analysis will be produced by the LLM analyzer in a subsequent implementation step."
        )

        # Update job with analysis results
        job_store.update(job_id, {
            'analysis_report': report,
            'analysis_format': 'markdown',
            'analysis_progress': 100,
            'analysis_completed_at': datetime.now(),
            'substatus': 'analysis_done',
            'status': 'done'
        })

        app.logger.info(f"Analysis Job {job_id}: Completed (placeholder report)")

    except Exception as e:
        app.logger.exception(f"Analysis Job {job_id}: Unexpected error during analysis")
        job_store.update(job_id, {
            'status': 'error',
            'substatus': 'analysis_error',
            'error': str(e)
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
    job_store.add(job_id, status='pending')

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

    app.logger.info(f"Job {job_id}: SVG delivered from {svg_path}")
    return send_file(svg_path, mimetype='image/svg+xml', as_attachment=False)

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
