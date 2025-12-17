from flask import Flask, render_template, request, Markup
from kerykeion import AstrologicalSubject, KerykeionChartSVG
import os
import logging

app = Flask(__name__)
# Ensure INFO-level logs are emitted so app.logger.info calls appear in the console
app.logger.setLevel(logging.INFO)

@app.route('/', methods=['GET', 'POST'])
def index():
    chart_svg = None
    # Log every request to help debugging (visible in console)
    app.logger.info(f"index route called, method={request.method}")
    print(f"[DEBUG] index route called, method={request.method}")
    if request.method == 'POST':
        # 1. Collect Input from web form
        name = request.form.get('name')
        year = int(request.form.get('year'))
        month = int(request.form.get('month'))
        day = int(request.form.get('day'))
        hour = int(request.form.get('hour'))
        minute = int(request.form.get('minute'))
        city = request.form.get('city')
        region = request.form.get('region') # New field for State/Region
        country = request.form.get('country')

        # Fallback: if the hidden ISO country code wasn't set, use the typed country name
        if not country:
            country = request.form.get('country_name')

        try:
            # 2. Initialize Subject with GeoNames integration
            # region is passed to help narrow down the city location
            subject = AstrologicalSubject(
                name, year, month, day, hour, minute,
                city=city,
                nation=country,
                online=True,
                geonames_username="dirid51"
            )

            # 3. Generate the SVG
            chart_generator = KerykeionChartSVG(subject)
            chart_generator.makeSVG()

            # 4. Display the resulting file
            filename = f"{name.replace(' ', '_')}_chart.svg"
            # Kerykeion typically saves in the current directory or home
            if os.path.exists(filename):
                with open(filename, 'r') as f:
                    chart_svg = Markup(f.read())
        except Exception as e:
            chart_svg = f"Error generating chart: {e}"

    return render_template('index.html', chart_svg=chart_svg)

if __name__ == '__main__':
    # Disable the reloader so a single process is used (breakpoints attach reliably)
    app.run(debug=True, use_reloader=False)
