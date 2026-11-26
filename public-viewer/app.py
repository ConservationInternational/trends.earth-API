"""Independent, read-only public viewer. Data access remains in the API."""

from pathlib import Path
import re

from dash import Dash, html
from flask import abort, request

BASE = Path(__file__).parent
app = Dash(
    __name__,
    assets_folder=str(BASE / "dist" / "assets"),
    assets_ignore=r"bootstrap\.js|app\.js",
    external_stylesheets=["/assets/style.css"],
    title="Trends.Earth shared map",
    update_title=None,
    meta_tags=[
        {
            "name": "viewport",
            "content": "width=device-width, initial-scale=1, viewport-fit=cover",
        }
    ],
)
server = app.server
server.config["MAX_CONTENT_LENGTH"] = 4096
app.index_string = """<!DOCTYPE html>
<html lang="en"><head>
{%metas%}
<meta name="referrer" content="no-referrer">
<meta name="robots" content="noindex,nofollow,noarchive">
<title>{%title%}</title>
<script src="/assets/bootstrap.js"></script>
{%favicon%}{%css%}
</head><body>
{%app_entry%}
<noscript>JavaScript is required to authorize and open this shared map.</noscript>
<footer>{%config%}{%scripts%}{%renderer%}</footer>
</body></html>"""

# Same policy links as the API sign-in pages (gefapi/routes/oidc.py).
PRIVACY_POLICY_URL = "https://www.conservation.org/policies/privacy"
TERMS_OF_USE_URL = "https://www.conservation.org/policies/terms-of-use"
DISCLAIMER = (
    "The boundaries and names used, and the designations used, in Trends.Earth "
    "do not imply official endorsement or acceptance by Conservation International "
    "Foundation, or by its partner organizations and contributors. "
)

app.layout = html.Div(
    [
        html.A("Skip to map controls", href="#controls", className="skip-link"),
        html.Header(
            [
                html.Img(
                    src="/assets/trends_earth_logo.png",
                    alt="Trends.Earth",
                    className="brand-logo",
                ),
                html.H1("Shared map", id="title"),
                html.P("Powered by Trends.Earth", className="powered-by"),
            ]
        ),
        html.Main(
            [
                html.Aside(
                    [
                        html.Div(
                            html.Button(
                                "Show map",
                                id="toc-close",
                                type="button",
                                **{
                                    "aria-controls": "controls",
                                    "aria-expanded": "false",
                                },
                            ),
                            className="toc-bar",
                        ),
                        html.P(
                            "This is an unlisted snapshot. Anyone with the "
                            "original link can "
                            "view, forward, or save its content until access expires.",
                            className="notice",
                        ),
                        html.Div(
                            "Opening shared map...",
                            id="status",
                            role="status",
                            **{"aria-live": "polite"},
                        ),
                        html.Button("Retry loading map", id="retry", hidden=True),
                        html.Div(
                            [
                                html.Button("Show full extent", id="fit"),
                                html.Div(id="basemap-controls"),
                            ],
                            id="map-tools",
                            hidden=True,
                        ),
                        html.Section(
                            [
                                html.H2("Layers", id="layers-title"),
                                html.Div(id="layers"),
                            ],
                            **{"aria-labelledby": "layers-title"},
                        ),
                        html.Section(
                            [
                                html.H2(
                                    "Selected vector feature",
                                    id="selection-title",
                                    tabIndex=-1,
                                ),
                                html.P(
                                    "All embedded attributes are public "
                                    "to link recipients."
                                ),
                                html.Dl(id="attributes"),
                                html.Button("Clear selection", id="clear-selection"),
                            ],
                            id="selection",
                            hidden=True,
                            **{"aria-labelledby": "selection-title"},
                        ),
                    ],
                    id="controls",
                    tabIndex=-1,
                    **{"aria-label": "Map controls and legend"},
                ),
                html.Section(
                    [
                        html.Div(
                            id="map",
                            tabIndex=0,
                        ),
                        html.P(
                            "The map will appear after this link is authorized.",
                            id="map-placeholder",
                        ),
                        html.Button(
                            "Layers",
                            id="toc-open",
                            type="button",
                            **{"aria-controls": "controls", "aria-expanded": "false"},
                        ),
                        html.P(
                            "Unlisted snapshot: anyone with the link can view, "
                            "forward, or save it until access expires.",
                            id="mobile-notice",
                        ),
                        html.Div(
                            [
                                html.P(
                                    [
                                        html.A(
                                            "© Mapbox",
                                            href="https://www.mapbox.com/about/maps/",
                                            target="_blank",
                                            rel="noopener noreferrer",
                                        ),
                                        " ",
                                        html.A(
                                            "© OpenStreetMap",
                                            href="https://www.openstreetmap.org/copyright",
                                            target="_blank",
                                            rel="noopener noreferrer",
                                        ),
                                        " | ",
                                        html.Button(
                                            "Disclaimer",
                                            id="disclaimer-toggle",
                                            type="button",
                                            **{
                                                "aria-controls": "disclaimer-text",
                                                "aria-expanded": "false",
                                            },
                                        ),
                                        html.Span(
                                            [
                                                html.Span(
                                                    "Disclaimer: ",
                                                    className="disclaimer-label",
                                                ),
                                                DISCLAIMER,
                                                html.A(
                                                    "Privacy Policy",
                                                    href=PRIVACY_POLICY_URL,
                                                    target="_blank",
                                                    rel="noopener noreferrer",
                                                ),
                                                " | ",
                                                html.A(
                                                    "Terms of Use",
                                                    href=TERMS_OF_USE_URL,
                                                    target="_blank",
                                                    rel="noopener noreferrer",
                                                ),
                                            ],
                                            id="disclaimer-text",
                                        ),
                                    ]
                                ),
                                html.P(id="expiry"),
                            ],
                            id="map-disclaimer",
                        ),
                    ],
                    id="map-region",
                    **{"aria-label": "Shared geographic map"},
                ),
            ]
        ),
    ]
)


@server.before_request
def restrict_viewer_routes():
    """Expose only the UI and local dependency assets, never data callbacks."""
    if request.method not in ("GET", "HEAD"):
        abort(405)
    if (
        request.path not in ("/_dash-layout", "/_dash-dependencies", "/health")
        and not request.path.startswith(("/assets/", "/_dash-component-suites/"))
        and not re.fullmatch(r"/share/s/[A-Za-z0-9_-]{32,128}", request.path)
        and not re.fullmatch(r"/share/[0-9a-fA-F-]{36}", request.path)
    ):
        abort(404)


@server.after_request
def private_responses(response):
    response.headers.update(
        {
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer",
            "X-Robots-Tag": "noindex, nofollow, noarchive",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": (
                "default-src 'none'; script-src 'self' "
                + " ".join(app.csp_hashes())
                + "; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob: "
                "https://api.mapbox.com; connect-src 'self' https://api.mapbox.com; "
                "font-src 'self'; object-src 'none'; "
                "base-uri 'none'; frame-ancestors 'none'; form-action 'none'; "
                "worker-src 'none'"
            ),
        }
    )
    return response


@server.get("/health")
def health():
    return "OK", 200


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8050, debug=False)
