from flask import Flask, render_template, request, Markup, jsonify, url_for, Response
from kerykeion import AstrologicalSubject, KerykeionChartSVG
import os
import logging
import sys
import threading
import uuid
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Get GeoNames username from environment variable
GEONAMES_USERNAME = os.getenv('GEONAMES_USERNAME')
if not GEONAMES_USERNAME:
    raise ValueError("GEONAMES_USERNAME environment variable not set. Please check your .env file.")

app = Flask(__name__)
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

def generate_chart_job(job_id, form_data):
    """Background worker to generate chart and store svg content in jobs dict."""
    with jobs_lock:
        jobs[job_id]['status'] = 'running'
    try:
        name = form_data.get('name')
        year = int(form_data.get('year'))
        month = int(form_data.get('month'))
        day = int(form_data.get('day'))
        hour = int(form_data.get('hour'))
        minute = int(form_data.get('minute'))
        city = form_data.get('city')
        region = form_data.get('region')
        country = form_data.get('country') or form_data.get('country_name')

        # 2. Initialize Subject with GeoNames integration
        subject = AstrologicalSubject(
            name, year, month, day, hour, minute,
            city=city,
            nation=country,
            online=True,
            geonames_username=GEONAMES_USERNAME
        )

        # 3. Generate the SVG
        chart_generator = KerykeionChartSVG(subject)
        chart_generator.makeSVG()

        # 4. Load the resulting file into memory and store in job
        filename = f"{name.replace(' ', '_')}_chart.svg"
        app.logger.info(f"Background job {job_id}: Looking for generated filename: {filename}")
        print(f"[DEBUG] Background job {job_id}: Looking for generated filename: {filename}")

        svg_content = None
        # Search current dir first, then home directory
        possible_paths = [os.path.abspath(filename), os.path.expanduser(os.path.join('~', filename))]
        for p in possible_paths:
            try:
                if os.path.exists(p):
                    with open(p, 'r', encoding='utf-8') as f:
                        svg_content = f.read()
                    filename = p
                    break
            except Exception:
                # ignore individual path errors
                pass

        if svg_content is None:
            raise FileNotFoundError(f"Generated SVG not found at any of: {possible_paths}")

        with jobs_lock:
            jobs[job_id]['status'] = 'done'
            jobs[job_id]['filename'] = filename
            jobs[job_id]['svg'] = svg_content
            jobs[job_id]['error'] = None
    except Exception as e:
        # Log full traceback
        app.logger.exception(f"Exception in background job {job_id}")
        print(f"[ERROR] Background job {job_id} exception: {e}")
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
    form = request.form
    job_id = uuid.uuid4().hex
    with jobs_lock:
        jobs[job_id] = {'status': 'pending', 'filename': None, 'svg': None, 'error': None}

    # Start background thread
    t = threading.Thread(target=generate_chart_job, args=(job_id, dict(form)), daemon=True)
    t.start()

    status_url = url_for('status_page', job_id=job_id)
    return jsonify({'job_id': job_id, 'status_url': status_url})

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
        name = request.form.get('name')
        year = int(request.form.get('year'))
        month = int(request.form.get('month'))
        day = int(request.form.get('day'))
        hour = int(request.form.get('hour'))
        minute = int(request.form.get('minute'))
        city = request.form.get('city')
        region = request.form.get('region')
        country = request.form.get('country') or request.form.get('country_name')

        try:
            subject = AstrologicalSubject(
                name, year, month, day, hour, minute,
                city=city,
                nation=country,
                online=True,
                geonames_username=GEONAMES_USERNAME
            )
            chart_generator = KerykeionChartSVG(subject)
            chart_generator.makeSVG()
            filename = f"{name.replace(' ', '_')}_chart.svg"
            app.logger.info(f"Looking for generated filename: {filename}")
            print(f"[DEBUG] Looking for generated filename: {filename}")
            if os.path.exists(filename):
                with open(filename, 'r', encoding='utf-8') as f:
                    chart_svg = Markup(f.read())
        except Exception as e:
            app.logger.exception("Exception while generating chart")
            print(f"[ERROR] Exception while generating chart: {e}")
            chart_svg = f"Error generating chart: {e}"
    return render_template('index.html', chart_svg=chart_svg)

if __name__ == '__main__':
    # Disable the reloader so a single process is used (breakpoints attach reliably)
    app.run(debug=True, use_reloader=False)
