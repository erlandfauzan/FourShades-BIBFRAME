import hashlib
from datetime import datetime, timezone
from rdflib import Graph, Literal, Namespace, URIRef, XSD

BF = Namespace("http://id.loc.gov/ontologies/bibframe/")
PROV = Namespace("http://www.w3.org/ns/prov#")
CELESTIA = Namespace("https://celestia.teyvat.org/bibframe/")


def resolve_work_uri(uniform_title: str, author: str) -> URIRef:
    """
    Generate a deterministic bf:Work URI from normalized uniform title and creator.
    Multiple editions or translations sharing the same uniform title and author
    converge onto a single canonical bf:Work node.
    """
    normalized_key = f"{uniform_title.strip().lower()}::{author.strip().lower()}"
    work_hash = hashlib.sha256(normalized_key.encode("utf-8")).hexdigest()[:12]
    return CELESTIA[f"work/{work_hash}"]


def attach_temporal_provenance(graph: Graph, entity_uri: URIRef, control_number: str) -> None:
    """
    Attach W3C PROV-O ingestion timestamp and source MARC21 control number
    for temporal auditability (Istaroth Time Lineage).
    """
    timestamp = datetime.now(timezone.utc).isoformat()
    graph.add((entity_uri, PROV.generatedAtTime, Literal(timestamp, datatype=XSD.dateTime)))
    graph.add((entity_uri, PROV.wasDerivedFrom, Literal(f"MARC21:{control_number}")))