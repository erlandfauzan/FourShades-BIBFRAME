from pathlib import Path
from rdflib import Graph, Literal, Namespace, RDF
from pyshacl import validate
from src.naberius_etl.converter import marcxml_to_bibframe

BF = Namespace("http://id.loc.gov/ontologies/bibframe/")
CELESTIA = Namespace("https://celestia.teyvat.org/bibframe/")

SAMPLE_XML = Path("data/raw_marc/world_literature.xml")
SHACL_FILE = Path("data/shapes/bibframe_shapes.ttl")


def test_naberius_etl_conforms_to_shacl():
    """Verify that the MARCXML-to-BIBFRAME output satisfies all SHACL schema constraints."""
    data_graph = marcxml_to_bibframe(SAMPLE_XML)
    shapes_graph = Graph().parse(str(SHACL_FILE), format="turtle")

    conforms, _, results_text = validate(
        data_graph=data_graph,
        shacl_graph=shapes_graph,
        inference="rdfs",
    )
    assert conforms, f"RDF graph failed SHACL validation:\n{results_text}"


def test_istaroth_merges_editions_under_single_work():
    """
    Verify that 4 MARC records (2 editions of Kawabata's 'Yukiguni', 1 Kafka 'Die Verwandlung',
    and 1 Hemingway 'The Old Man and the Sea') resolve into 3 canonical bf:Work nodes
    and 4 bf:Instance nodes.
    """
    data_graph = marcxml_to_bibframe(SAMPLE_XML)

    works = list(data_graph.subjects(RDF.type, BF.Work))
    instances = list(data_graph.subjects(RDF.type, BF.Instance))

    assert len(works) == 3, f"Expected 3 unique Works, found {len(works)}"
    assert len(instances) == 4, f"Expected 4 Instances, found {len(instances)}"


def test_corrupt_orphan_instance_rejected_by_shacl():
    """
    Negative Test: Inject an orphaned bf:Instance with an invalid ISO language code.
    The SHACL quality gate must reject the corrupted graph.
    """
    corrupt_graph = Graph()
    corrupt_instance = CELESTIA["instance/corrupt-record-999"]
    corrupt_graph.add((corrupt_instance, RDF.type, BF.Instance))
    corrupt_graph.add((corrupt_instance, BF.language, Literal("INVALID_LANG")))

    shapes_graph = Graph().parse(str(SHACL_FILE), format="turtle")
    conforms, _, _ = validate(data_graph=corrupt_graph, shacl_graph=shapes_graph)

    assert conforms is False, "SHACL failed to reject orphaned/malformed instance!"