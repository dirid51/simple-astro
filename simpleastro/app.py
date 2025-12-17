from flask import Flask, render_template, request, jsonify, url_for, Response
from markupsafe import Markup
from kerykeion import AstrologicalSubject, KerykeionChartSVG
import os
import logging
import sys
import threading
import uuid
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv

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

# Simple in-memory job store: job_id -> {status, filename, svg, error}
jobs = {}
jobs_lock = threading.Lock()

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

def generate_chart_job(job_id, form_data):
    """Background worker to generate chart and store svg content in jobs dict."""
    with jobs_lock:
        jobs[job_id]['status'] = 'running'
    try:
        # Validate input data
        validated = validate_birth_data(form_data)

        # Save current directory and change to charts directory
        original_dir = os.getcwd()
        os.chdir(CHARTS_DIR)

        try:
            # Initialize Subject with GeoNames integration
            subject = AstrologicalSubject(
                validated['name'], validated['year'], validated['month'],
                validated['day'], validated['hour'], validated['minute'],
                city=validated['city'],
                nation=validated['country'],
                online=True,
                geonames_username=GEONAMES_USERNAME
            )

            # Generate the SVG (will be saved to CHARTS_DIR)
            chart_generator = KerykeionChartSVG(subject)
            chart_generator.makeSVG()

            # Load the resulting file into memory and store in job
            # The Kerykeion library generates files with the pattern: {name} - Natal Chart.svg
            expected_filename = f"{validated['name']} - Natal Chart.svg"
            app.logger.info(f"Background job {job_id}: Looking for generated filename: {expected_filename}")

            svg_path = os.path.join(CHARTS_DIR, expected_filename)
            if not os.path.exists(svg_path):
                raise FileNotFoundError(f"Generated SVG not found at: {svg_path}")

            # Check file size to prevent memory exhaustion
            svg_size = os.path.getsize(svg_path)
            if svg_size > MAX_SVG_SIZE:
                raise ValueError(f"SVG too large: {svg_size} bytes (max: {MAX_SVG_SIZE})")

            with open(svg_path, 'r', encoding='utf-8') as f:
                svg_content = f.read()

            filename = expected_filename
            app.logger.info(f"Background job {job_id}: Successfully loaded SVG from: {filename}")

            with jobs_lock:
                jobs[job_id]['status'] = 'done'
                jobs[job_id]['filename'] = filename
                jobs[job_id]['svg'] = svg_content
                jobs[job_id]['error'] = None
        finally:
            # Restore original directory
            os.chdir(original_dir)
    except ValueError as e:
        # Input validation error
        app.logger.warning(f"Background job {job_id}: Validation error: {e}")
        with jobs_lock:
            jobs[job_id]['status'] = 'error'
            jobs[job_id]['svg'] = None
            jobs[job_id]['filename'] = None
            jobs[job_id]['error'] = str(e)
    except Exception as e:
        # Log full traceback for other errors
        app.logger.exception(f"Exception in background job {job_id}")
        with jobs_lock:
            jobs[job_id]['status'] = 'error'
            jobs[job_id]['svg'] = None
            jobs[job_id]['filename'] = None
            jobs[job_id]['error'] = str(e)

@app.route('/', methods=['GET'])
def index():
    # Render the main form page
    return render_template('index.html')

@app.route('/submit', methods=['POST'])
def submit():
    # Start an asynchronous job and return JSON with job id and status URL
    try:
        # Validate form data
        validated = validate_birth_data(request.form)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400  # Bad Request

    job_id = uuid.uuid4().hex
    with jobs_lock:
        jobs[job_id] = {'status': 'pending', 'filename': None, 'svg': None, 'error': None}

    # Start background thread
    t = threading.Thread(target=generate_chart_job, args=(job_id, dict(request.form)), daemon=True)
    t.start()

    status_url = url_for('status_page', job_id=job_id)
    return jsonify({'job_id': job_id, 'status_url': status_url}), 202  # Accepted

@app.route('/status/<job_id>', methods=['GET'])
def status_page(job_id):
    # Render status page; JS on that page will poll /api/status/<job_id>
    return render_template('status.html', job_id=job_id)

@app.route('/api/status/<job_id>', methods=['GET'])
def api_status(job_id):
    with jobs_lock:
        job = jobs.get(job_id)
    if not job:
        return jsonify({'status': 'unknown', 'error': 'job id not found'}), 404
    # Return status and any additional fields
    resp = {'status': job['status']}
    if job['status'] == 'done':
        resp['filename'] = job['filename']
        resp['svg'] = job['svg']
    if job['status'] == 'error':
        resp['error'] = job.get('error')
    return jsonify(resp)

@app.route('/job_svg/<job_id>', methods=['GET'])
def job_svg(job_id):
    """Return the generated SVG for a completed job."""
    with jobs_lock:
        job = jobs.get(job_id)
    if not job:
        return "Job not found", 404
    if job['status'] != 'done' or not job.get('svg'):
        return "SVG not available", 404
    return Response(job['svg'], mimetype='image/svg+xml')

# Keep backward-compatible quick test route that synchronously generates (optional)
@app.route('/sync-generate', methods=['POST'])
def sync_generate():
    # For debugging: run the old synchronous flow
    chart_svg = None
    if request.method == 'POST':
        try:
            # Validate input data
            validated = validate_birth_data(request.form)
        except ValueError as e:
            return render_template('index.html', chart_svg=f"Error: {str(e)}")

        # Save current directory and change to charts directory
        original_dir = os.getcwd()
        os.chdir(CHARTS_DIR)

        try:
            subject = AstrologicalSubject(
                validated['name'], validated['year'], validated['month'],
                validated['day'], validated['hour'], validated['minute'],
                city=validated['city'],
                nation=validated['country'],
                online=True,
                geonames_username=GEONAMES_USERNAME
            )
            chart_generator = KerykeionChartSVG(subject)
            chart_generator.makeSVG()
            expected_filename = f"{validated['name']} - Natal Chart.svg"
            app.logger.info(f"Synchronous chart generation: Looking for {expected_filename}")

            # Load from charts directory
            svg_path = os.path.join(CHARTS_DIR, expected_filename)
            if os.path.exists(svg_path):
                # Check file size
                svg_size = os.path.getsize(svg_path)
                if svg_size > MAX_SVG_SIZE:
                    chart_svg = f"Error: SVG too large ({svg_size} bytes)"
                else:
                    with open(svg_path, 'r', encoding='utf-8') as f:
                        chart_svg = Markup(f.read())
            else:
                chart_svg = f"Error: Generated SVG file not found at {svg_path}"
        except Exception as e:
            app.logger.exception("Exception while generating chart")
            chart_svg = f"Error generating chart: {e}"
        finally:
            # Restore original directory
            os.chdir(original_dir)
    return render_template('index.html', chart_svg=chart_svg)

if __name__ == '__main__':
    # Disable the reloader so a single process is used (breakpoints attach reliably)
    app.run(debug=FLASK_DEBUG, use_reloader=False)
