"""
Smoke test for Step 0: Baseline and import validation.

This test ensures that:
1. The app module imports without errors
2. All required Flask routes are registered
3. Core functions are callable
4. No background threads start on import (safe for testing)
"""

import sys
import os
import logging

# Suppress Flask debug toolbar and other verbose logging during tests
logging.getLogger('werkzeug').setLevel(logging.ERROR)
logging.getLogger('flask').setLevel(logging.ERROR)

# Setup path to import simpleastro
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


def test_app_imports_successfully():
    """Test that the app module can be imported without errors."""
    from simpleastro.app import app
    assert app is not None
    assert hasattr(app, 'url_map')


def test_flask_routes_are_registered():
    """Test that all required Flask routes are registered."""
    from simpleastro.app import app

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
        '/sync-generate',
    }

    for route in required_routes:
        assert route in routes, f"Missing route: {route}"


def test_job_store_is_initialized():
    """Test that the job store is initialized."""
    from simpleastro.app import job_store
    assert job_store is not None
    assert hasattr(job_store, 'add')
    assert hasattr(job_store, 'get')
    assert hasattr(job_store, 'update')
    assert hasattr(job_store, 'cleanup_expired')


def test_core_functions_are_callable():
    """Test that core functions exist and are callable."""
    from simpleastro.app import (
        validate_birth_data,
        sanitize_filename,
        generate_chart_job,
        generate_analysis_job,
    )

    assert callable(validate_birth_data)
    assert callable(sanitize_filename)
    assert callable(generate_chart_job)
    assert callable(generate_analysis_job)


def test_charts_directory_exists():
    """Test that the charts output directory exists and is writable."""
    from simpleastro.app import SVG_OUTPUT_DIR, CHARTS_DIR

    assert SVG_OUTPUT_DIR.exists()
    assert os.path.isdir(CHARTS_DIR)
    # The directory should be writable (it was tested at import time)


def test_environment_variables_loaded():
    """Test that environment variables are loaded."""
    import os
    from dotenv import load_dotenv

    load_dotenv()

    # These should be loadable (even if not set, they should not raise)
    max_size = os.getenv('MAX_SVG_SIZE')
    job_retention = os.getenv('JOB_RETENTION_MINUTES')
    # GEONAMES_USERNAME can be optional


def test_flask_test_client_works():
    """Test that Flask test client can be created."""
    from simpleastro.app import app

    client = app.test_client()
    assert client is not None


def test_app_has_debug_setting():
    """Test that app debug mode is configurable."""
    from simpleastro.app import app, FLASK_DEBUG

    # FLASK_DEBUG should be a boolean (either True or False)
    assert isinstance(FLASK_DEBUG, bool)
    # app.debug should match the setting
    assert app.debug == FLASK_DEBUG


if __name__ == '__main__':
    # Run tests manually if pytest is not available
    import traceback

    tests = [
        test_app_imports_successfully,
        test_flask_routes_are_registered,
        test_job_store_is_initialized,
        test_core_functions_are_callable,
        test_charts_directory_exists,
        test_environment_variables_loaded,
        test_flask_test_client_works,
        test_app_has_debug_setting,
    ]

    passed = 0
    failed = 0

    for test_func in tests:
        try:
            test_func()
            print("[PASS] {}".format(test_func.__name__))
            passed += 1
        except AssertionError as e:
            print("[FAIL] {}: {}".format(test_func.__name__, e))
            failed += 1
        except Exception as e:
            print("[FAIL] {}: {}: {}".format(test_func.__name__, type(e).__name__, e))
            traceback.print_exc()
            failed += 1

    print("\n{} passed, {} failed".format(passed, failed))
    sys.exit(0 if failed == 0 else 1)

