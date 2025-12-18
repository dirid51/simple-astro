import atexit
import logging
import os
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, render_template, request, jsonify, url_for, Response
from kerykeion import AstrologicalSubject, KerykeionChartSVG
from markupsafe import Markup

# Load environment variables from .env file
load_dotenv()

# Get GeoNames username from environment variable
GEONAMES_USERNAME = os.getenv('GEONAMES_USERNAME')
if not GEONAMES_USERNAME:
    raise ValueError("GEONAMES_USERNAME environment variable not set. Please check your .env file.")

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
CHARTS_DIR = os.path.expanduser(os.path.join('~'))
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


class JobStore:
    """Thread-safe in-memory job store with automatic expiration (TTL)."""

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

    def add(self, job_id, status='pending'):
        """
        Add a new job to the store.

        Args:
            job_id: Unique job identifier
            status: Initial status (default: 'pending')
        """
        with self.lock:
            self.jobs[job_id] = {
                'status': status,
                'filename': None,
                'svg': None,
                'error': None,
                'created_at': datetime.now()
            }
        app.logger.info(f"Job {job_id}: Created with status '{status}'")

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


def generate_chart(validated_data):
    """
    Shared logic for chart generation.

    This function encapsulates the core chart generation process used by both
    async and sync routes to prevent code duplication.

    Args:
        validated_data: Dictionary with validated birth data

    Returns:
        Dictionary with keys 'filename' and 'svg_content'

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

        # The Kerykeion library generates files with pattern: {name} - Natal Chart.svg
        expected_filename = f"{validated_data['name']} - Natal Chart.svg"

        svg_path = os.path.join(CHARTS_DIR, expected_filename)
        if not os.path.exists(svg_path):
            raise FileNotFoundError(f"Generated SVG not found at: {svg_path}")

        # Check file size to prevent memory exhaustion
        svg_size = os.path.getsize(svg_path)
        if svg_size > MAX_SVG_SIZE:
            raise ValueError(f"SVG too large: {svg_size} bytes (max: {MAX_SVG_SIZE})")

        with open(svg_path, 'r', encoding='utf-8') as f:
            svg_content = f.read()

        return {
            'filename': expected_filename,
            'svg_content': svg_content
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
        job_store.update(job_id, {'status': 'running'})

        # Validate input data
        validated = validate_birth_data(form_data)
        app.logger.info(f"Job {job_id}: Input validation successful")

        # Generate chart using shared logic
        result = generate_chart(validated)
        app.logger.info(f"Job {job_id}: Chart generation successful")

        # Update job with completion status and results
        job_store.update(job_id, {
            'status': 'done',
            'filename': result['filename'],
            'svg': result['svg_content'],
            'error': None
        })
        app.logger.info(f"Job {job_id}: Completed successfully")

    except ValueError as e:
        # Input validation error - expected and handled
        app.logger.warning(f"Job {job_id}: Validation error: {e}")
        job_store.update(job_id, {
            'status': 'error',
            'filename': None,
            'svg': None,
            'error': str(e)
        })
    except FileNotFoundError as e:
        # Generated file not found - likely chart generation failed
        app.logger.warning(f"Job {job_id}: File not found: {e}")
        job_store.update(job_id, {
            'status': 'error',
            'filename': None,
            'svg': None,
            'error': f"Chart generation failed: {str(e)}"
        })
    except Exception as e:
        # Unexpected error - log full traceback for debugging
        app.logger.exception(f"Job {job_id}: Unexpected error during chart generation")
        job_store.update(job_id, {
            'status': 'error',
            'filename': None,
            'svg': None,
            'error': f"Unexpected error: {str(e)}"
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
    """Get status of a job with atomic consistency."""
    job = job_store.get(job_id)
    if not job:
        app.logger.info(f"Status request for unknown/expired job: {job_id}")
        return jsonify({'status': 'unknown', 'error': 'job id not found'}), 404

    # Build response within the lock to ensure consistency
    resp = {'status': job['status']}
    if job['status'] == 'done':
        resp['filename'] = job['filename']
        resp['svg'] = job['svg']
    if job['status'] == 'error':
        resp['error'] = job.get('error')

    app.logger.debug(f"Job {job_id}: Status={job['status']}")
    return jsonify(resp)

@app.route('/job_svg/<job_id>', methods=['GET'])
def job_svg(job_id):
    """Return the generated SVG for a completed job."""
    job = job_store.get(job_id)
    if not job:
        app.logger.info(f"SVG request for unknown/expired job: {job_id}")
        return "Job not found", 404
    if job['status'] != 'done' or not job.get('svg'):
        app.logger.warning(f"SVG request for incomplete job: {job_id} (status={job['status']})")
        return "SVG not available", 404
    app.logger.info(f"Job {job_id}: SVG delivered")
    return Response(job['svg'], mimetype='image/svg+xml')

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
            chart_svg = Markup(result['svg_content'])
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
