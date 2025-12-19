#!/usr/bin/env python3
"""
Helper script to generate a Kerykeion SVG for a subject.
This script is intended to be executed in a subprocess with its working
directory set to the desired output directory (CHARTS_DIR).

Usage:
    python _generate_svg.py <subject_name> <year> <month> <day> <hour> <minute> <city> <nation> <geonames_username> [<output_filename>]

If <output_filename> is provided, the script will attempt to rename the
generated file to that name inside the current working directory.
"""
import sys
import os
from pathlib import Path

from kerykeion import AstrologicalSubjectFactory, ChartDataFactory, ChartDrawer


def main(argv):
    try:
        from kerykeion import AstrologicalSubject, KerykeionChartSVG
    except Exception as e:
        print(f"Failed to import kerykeion: {e}", file=sys.stderr)
        return 2

    if len(argv) < 9:
        print("Insufficient arguments", file=sys.stderr)
        return 2

    subject_name = argv[0]
    year = int(argv[1])
    month = int(argv[2])
    day = int(argv[3])
    hour = int(argv[4])
    minute = int(argv[5])
    city = argv[6] or None
    nation = argv[7] or None
    geonames_username = argv[8] or None
    output_filename = argv[9] if len(argv) > 9 else None

    try:
        subject = AstrologicalSubjectFactory.from_birth_data(
            subject_name,
            year,
            month,
            day,
            hour,
            minute,
            city=city,
            nation=nation,
            online=True,
            geonames_username=geonames_username
        )

        chart_data = ChartDataFactory.create_natal_chart_data(subject)
        drawer = ChartDrawer(chart_data=chart_data)
        svg_string = drawer.generate_svg_string()

        output_dir = Path("charts")
        output_dir.mkdir(exist_ok=True)
        drawer.save_svg(output_path=output_dir)

        if output_filename:
            # Kerykeion typically writes files named "<subject_name> - Natal Chart.svg"
            expected = os.path.join(output_dir, f"{subject_name} - Natal Chart.svg")
            if os.path.exists(expected):
                try:
                    # Atomic rename where possible
                    os.replace(expected, output_filename)
                except Exception:
                    os.rename(expected, output_filename)
            else:
                # If expected filename not found, attempt to find any recent svg file
                svgs = [f for f in os.listdir('.') if f.lower().endswith('.svg')]
                if svgs:
                    # choose the most recently modified svg
                    svgs.sort(key=lambda p: os.path.getmtime(p), reverse=True)
                    try:
                        os.replace(svgs[0], output_filename)
                    except Exception:
                        os.rename(svgs[0], output_filename)
                else:
                    print('No svg file produced to rename', file=sys.stderr)
                    return 3

        return 0
    except Exception as e:
        print(f"Error generating SVG: {e}", file=sys.stderr)
        return 3


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))

