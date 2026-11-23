import re
from pathlib import Path
from pymarc import parse_xml_to_array
from rdflib import Graph, Literal, Namespace, URIRef, RDF, RDFS
from src.istaroth_lineage.lineage import resolve_work_uri, attach_temporal_provenance

BF = Namespace("http://id.loc.gov/ontologies/bibframe/")
CELESTIA = Namespace("https://celestia.teyvat.org/bibframe/")


def _get_subfield(record, tag: str, code: str, default: str = "") -> str:
    field = record.get(tag)
    if field and code in field:
        return field[code].strip(" :/,")
    return default


def marcxml_to_bibframe(xml_path: str | Path) -> Graph:
    """
    Transform a collection of MARCXML records into a BIBFRAME 2.0 RDF Knowledge Graph.
    """
    graph = Graph()
    graph.bind("bf", BF)
    graph.bind("celestia", CELESTIA)

    records = parse_xml_to_array(str(xml_path))

    for record in records:
        f001 = record.get("001")
        ctrl_num = f001.data.strip() if f001 and f001.data else "unknown"
        f008 = record.get("008")
        field_008 = f008.data if f008 and f008.data else ""
        lang_code = field_008[35:38] if len(field_008) >= 38 else "und"
        
        author = _get_subfield(record, "100", "a", "Unknown Creator")
        main_title = _get_subfield(record, "245", "a", "Untitled")
        sub_title = _get_subfield(record, "245", "b", "")
        uniform_title = _get_subfield(record, "240", "a", main_title)
        ddc_number = _get_subfield(record, "082", "a", "000")
        electronic_uri = _get_subfield(record, "856", "u", "")

        # 1. bf:Work entity (Deduplicated across editions via Istaroth lineage)
        work_uri = resolve_work_uri(uniform_title, author)
        if (work_uri, RDF.type, BF.Work) not in graph:
            graph.add((work_uri, RDF.type, BF.Work))
            work_title_node = CELESTIA[f"title/work-{ctrl_num}"]
            graph.add((work_title_node, RDF.type, BF.Title))
            graph.add((work_title_node, BF.mainTitle, Literal(uniform_title)))
            graph.add((work_uri, BF.title, work_title_node))

            # Dewey Decimal Classification (DDC)
            ddc_node = CELESTIA[f"ddc/{ddc_number.replace('.', '_')}"]
            graph.add((ddc_node, RDF.type, BF.ClassificationDdc))
            graph.add((ddc_node, BF.classificationPortion, Literal(ddc_number)))
            graph.add((work_uri, BF.classification, ddc_node))

            # Creator Agent (Sanitized slug for valid RDF URI)
            agent_slug = re.sub(r"[^a-z0-9]+", "_", author.lower()).strip("_")
            agent_uri = CELESTIA[f"agent/{agent_slug}"]
            graph.add((agent_uri, RDF.type, BF.Agent))
            graph.add((agent_uri, RDFS.label, Literal(author)))
            graph.add((work_uri, BF.contribution, agent_uri))

            # Topical Subjects (MARC 650)
            for idx, subj_field in enumerate(record.get_fields("650")):
                if "a" in subj_field:
                    topic_uri = CELESTIA[f"topic/{ctrl_num}-{idx}"]
                    graph.add((topic_uri, RDF.type, BF.Topic))
                    graph.add((topic_uri, RDFS.label, Literal(subj_field["a"].strip())))
                    graph.add((work_uri, BF.subject, topic_uri))

        # 2. bf:Instance entity (Specific publication manifestation)
        instance_uri = CELESTIA[f"instance/{ctrl_num}"]
        graph.add((instance_uri, RDF.type, BF.Instance))
        graph.add((instance_uri, BF.instanceOf, work_uri))
        graph.add((work_uri, BF.hasInstance, instance_uri))

        inst_title_node = CELESTIA[f"title/inst-{ctrl_num}"]
        graph.add((inst_title_node, RDF.type, BF.Title))
        full_title = f"{main_title}: {sub_title}" if sub_title else main_title
        graph.add((inst_title_node, BF.mainTitle, Literal(full_title)))
        graph.add((instance_uri, BF.title, inst_title_node))

        # Language code (ISO 639-2)
        graph.add((instance_uri, BF.language, Literal(lang_code)))

        # Electronic Locator (Target URI monitored by Ronova)
        if electronic_uri:
            graph.add((instance_uri, BF.electronicLocator, URIRef(electronic_uri)))

        # 3. bf:Item entity (Physical or digital copy)
        item_uri = CELESTIA[f"item/{ctrl_num}-c1"]
        graph.add((item_uri, RDF.type, BF.Item))
        graph.add((item_uri, BF.itemOf, instance_uri))

        attach_temporal_provenance(graph, instance_uri, ctrl_num)

    return graph


if __name__ == "__main__":
    input_file = Path("data/raw_marc/world_literature.xml")
    output_file = Path("data/output/world_literature_bibframe.ttl")

    rdf_graph = marcxml_to_bibframe(input_file)
    rdf_graph.serialize(destination=str(output_file), format="turtle")
    print(f"Conversion complete! Serialized {len(rdf_graph)} RDF triples to {output_file}")