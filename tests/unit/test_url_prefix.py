from flask import Flask, jsonify, request, url_for

from pikaraoke.lib.url_prefix import (
    BasePathMiddleware,
    append_base_path_to_url,
    normalize_url_base_path,
)


def test_normalize_url_base_path_handles_root_and_missing_slashes():
    assert normalize_url_base_path(None) == ""
    assert normalize_url_base_path("") == ""
    assert normalize_url_base_path("/") == ""
    assert normalize_url_base_path("karaoke") == "/karaoke"
    assert normalize_url_base_path("/karaoke/") == "/karaoke"


def test_append_base_path_to_url_preserves_existing_non_root_path():
    assert (
        append_base_path_to_url("https://example.com", "/karaoke") == "https://example.com/karaoke"
    )
    assert (
        append_base_path_to_url("https://example.com/", "/karaoke") == "https://example.com/karaoke"
    )
    assert (
        append_base_path_to_url("https://example.com/karaoke", "/karaoke")
        == "https://example.com/karaoke"
    )
    assert (
        append_base_path_to_url("https://example.com/already-there", "/karaoke")
        == "https://example.com/already-there"
    )


def create_prefixed_test_app() -> Flask:
    app = Flask(__name__)
    app.wsgi_app = BasePathMiddleware(app.wsgi_app, "/karaoke")

    @app.route("/hello")
    def hello():
        return jsonify(
            {
                "script_root": request.script_root,
                "hello_url": url_for("hello"),
                "static_url": url_for("static", filename="app.js"),
            }
        )

    return app


def test_base_path_middleware_sets_script_root_for_stripped_proxy_requests():
    app = create_prefixed_test_app()
    client = app.test_client()

    response = client.get("/hello")

    assert response.status_code == 200
    assert response.get_json() == {
        "script_root": "/karaoke",
        "hello_url": "/karaoke/hello",
        "static_url": "/karaoke/static/app.js",
    }


def test_base_path_middleware_accepts_unstripped_prefixed_requests():
    app = create_prefixed_test_app()
    client = app.test_client()

    response = client.get("/karaoke/hello")

    assert response.status_code == 200
    assert response.get_json()["hello_url"] == "/karaoke/hello"


def test_static_file_urls_work_with_base_path():
    """Test that static file URLs are generated correctly with base path."""
    app = create_prefixed_test_app()
    client = app.test_client()

    response = client.get("/hello")
    data = response.get_json()
    
    # Verify that static URLs include the base path
    assert data["static_url"] == "/karaoke/static/app.js"
    
    # Test that the URL generation works correctly by checking the generated URL
    # The actual file access would require setting up static files in the test app
    # but we can verify the URL format is correct
    assert data["static_url"].startswith("/karaoke/")
    
    # Verify that our CSS file doesn't have hardcoded absolute paths
    # by reading it directly from the filesystem
    import os
    css_path = os.path.join(os.path.dirname(__file__), "..", "..", "pikaraoke", "static", "score.css")
    with open(css_path, 'r') as f:
        css_content = f.read()
    
    # The CSS should use relative paths, not absolute ones starting with /
    assert 'url("/static/' not in css_content, "CSS should not have hardcoded /static/ paths"
    assert 'url("/' not in css_content, "CSS should not have hardcoded absolute paths"
