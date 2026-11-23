import asyncio
from pathlib import Path
import httpx
from fastapi.testclient import TestClient
from pyshacl import validate
from rdflib import Graph
from src.asmoday_gateway.app import create_app
from src.ronova_reaper.reaper import reap_dead_links

SHACL_FILE = Path("data/shapes/bibframe_shapes.ttl")


def test_dual_engine_sparql_vs_sql_parity():
    """
    Parity Test: Verify that the SPARQL (/graph/works) and SQL (/sql/works)
    endpoints return 100% identical data structures and values.
    """
    app = create_app()
    client = TestClient(app)

    sparql_res = client.get("/api/v1/graph/works")
    sql_res = client.get("/api/v1/sql/works")

    assert sparql_res.status_code == 200
    assert sql_res.status_code == 200

    sparql_payload = sparql_res.json()
    sql_payload = sql_res.json()

    assert sparql_payload["total_works"] == 3
    assert sparql_payload["works"] == sql_payload["works"]


def test_ronova_tombstoning_returns_http_410_gone():
    """
    Integration & Contract Test:
    Once Ronova detects a 404 dead link on loc-jpn-002 (Snow Country English translation),
    Asmoday API must return HTTP 410 Gone while active records continue returning HTTP 200 OK.
    """
    app = create_app()
    api_client = TestClient(app)

    # Prior to Ronova audit, loc-jpn-002 is accessible (200 OK)
    before_res = api_client.get("/api/v1/instances/loc-jpn-002")
    assert before_res.status_code == 200

    # Mock HTTP transport for fast, deterministic offline execution
    def mock_handler(request: httpx.Request) -> httpx.Response:
        if "404" in str(request.url):
            return httpx.Response(404, request=request)
        return httpx.Response(200, request=request)

    mock_http = httpx.AsyncClient(transport=httpx.MockTransport(mock_handler))
    audit = asyncio.run(reap_dead_links(app.state.rdf_graph, client=mock_http))

    assert audit["deprecated"] == 1
    assert audit["active"] == 3

    # After Ronova tombstoning, loc-jpn-002 must return HTTP 410 Gone
    after_res = api_client.get("/api/v1/instances/loc-jpn-002")
    assert after_res.status_code == 410
    assert after_res.json()["status"] == "deprecated"
    assert after_res.json()["shade"] == "Ronova (Shade of Death)"

    # Healthy records remain HTTP 200 OK
    active_res = api_client.get("/api/v1/instances/loc-jpn-001")
    assert active_res.status_code == 200


def test_graph_remains_shacl_valid_after_ronova_reap():
    """Verify that status and provenance triples added by Ronova do not violate SHACL constraints."""
    app = create_app()

    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, request=request)

    mock_http = httpx.AsyncClient(transport=httpx.MockTransport(mock_handler))
    asyncio.run(reap_dead_links(app.state.rdf_graph, client=mock_http))

    shapes_graph = Graph().parse(str(SHACL_FILE), format="turtle")
    conforms, _, results_text = validate(
        data_graph=app.state.rdf_graph,
        shacl_graph=shapes_graph,
        inference="rdfs",
    )
    assert conforms, f"Graph failed SHACL validation after Ronova tombstoning:\n{results_text}"