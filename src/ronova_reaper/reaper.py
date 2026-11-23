import asyncio
from datetime import datetime, timezone
from pathlib import Path
import httpx
from rdflib import Graph, Literal, Namespace, XSD

BF = Namespace("http://id.loc.gov/ontologies/bibframe/")
PROV = Namespace("http://www.w3.org/ns/prov#")
CELESTIA = Namespace("https://celestia.teyvat.org/bibframe/")


async def _check_uri(client: httpx.AsyncClient, url: str) -> tuple[bool, str]:
    """Probe external URI health using HTTP HEAD with automatic fallback to GET."""
    try:
        response = await client.head(url)
        if response.status_code == 405:
            response = await client.get(url)

        if response.status_code >= 400:
            return False, f"HTTP {response.status_code}"
        return True, f"HTTP {response.status_code}"
    except httpx.RequestError as exc:
        return False, f"ConnectionError: {type(exc).__name__}"


async def reap_dead_links(
    graph: Graph,
    client: httpx.AsyncClient | None = None,
) -> dict:
    """
    Ronova (Shade of Death):
    Asynchronously audit all bf:electronicLocator URIs in the BIBFRAME graph.
    Dead links are tombstoned as 'deprecated' with PROV-O invalidation metadata
    while preserving structural graph integrity.
    """
    graph.bind("prov", PROV)
    graph.bind("celestia", CELESTIA)

    targets = list(graph.subject_objects(BF.electronicLocator))
    report = {
        "scanned_at": datetime.now(timezone.utc).isoformat(),
        "total_scanned": len(targets),
        "active": 0,
        "deprecated": 0,
        "records": [],
    }

    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=5.0, follow_redirects=True)

    try:
        for instance_uri, locator_uri in targets:
            url_str = str(locator_uri)
            is_alive, reason = await _check_uri(client, url_str)

            graph.remove((instance_uri, BF.status, None))
            graph.remove((instance_uri, CELESTIA.reaperReason, None))

            if is_alive:
                status_label = "active"
                report["active"] += 1
                graph.add((instance_uri, BF.status, Literal("active")))
            else:
                status_label = "deprecated"
                report["deprecated"] += 1
                now_iso = datetime.now(timezone.utc).isoformat()
                graph.add((instance_uri, BF.status, Literal("deprecated")))
                graph.add((instance_uri, PROV.invalidatedAtTime, Literal(now_iso, datatype=XSD.dateTime)))
                graph.add((instance_uri, CELESTIA.reaperReason, Literal(reason)))

            report["records"].append(
                {
                    "instance_uri": str(instance_uri),
                    "url": url_str,
                    "status": status_label,
                    "reason": reason,
                }
            )
    finally:
        if owns_client:
            await client.aclose()

    return report


if __name__ == "__main__":
    ttl_path = Path("data/output/world_literature_bibframe.ttl")
    if not ttl_path.exists():
        raise FileNotFoundError("Run converter.py first to generate world_literature_bibframe.ttl")

    g = Graph().parse(str(ttl_path), format="turtle")
    audit = asyncio.run(reap_dead_links(g))
    g.serialize(destination=str(ttl_path), format="turtle")
    print(f"Ronova Audit Complete: {audit['active']} active, {audit['deprecated']} deprecated.")