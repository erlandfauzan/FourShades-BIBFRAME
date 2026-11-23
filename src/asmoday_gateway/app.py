from pathlib import Path
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from rdflib import Graph, Namespace, RDF, RDFS
from src.naberius_etl.converter import marcxml_to_bibframe
from src.asmoday_gateway.relational_db import init_relational_db, query_works_sql
from src.ronova_reaper.reaper import reap_dead_links

BF = Namespace("http://id.loc.gov/ontologies/bibframe/")
PROV = Namespace("http://www.w3.org/ns/prov#")
CELESTIA = Namespace("https://celestia.teyvat.org/bibframe/")

DEFAULT_XML = Path("data/raw_marc/world_literature.xml")


def create_app(xml_path: Path = DEFAULT_XML) -> FastAPI:
    app = FastAPI(
        title="FourShades-BIBFRAME: Asmoday Dual-Engine Gateway",
        description="Dual-engine API comparing RDF/SPARQL vs. Relational SQL queries while enforcing HTTP 410 Gone tombstoning.",
        version="1.0.0",
    )

    app.state.rdf_graph = marcxml_to_bibframe(xml_path)
    app.state.sql_conn = init_relational_db(xml_path)

    @app.get("/health")
    def health_check():
        return {
            "status": "ok",
            "rdf_triples": len(app.state.rdf_graph),
        }

    @app.get("/api/v1/graph/works")
    def get_works_sparql(author: str | None = Query(default=None)):
        graph: Graph = app.state.rdf_graph
        sparql = """
        PREFIX bf: <http://id.loc.gov/ontologies/bibframe/>
        PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>

        SELECT ?work ?workTitle ?author ?ddc ?instance ?instTitle ?lang ?locator ?status
        WHERE {
            ?work a bf:Work ;
                  bf:title ?wTitleNode ;
                  bf:contribution ?agentNode ;
                  bf:classification ?ddcNode .
            ?wTitleNode bf:mainTitle ?workTitle .
            ?agentNode rdfs:label ?author .
            ?ddcNode bf:classificationPortion ?ddc .

            ?instance bf:instanceOf ?work ;
                      bf:title ?iTitleNode ;
                      bf:language ?lang .
            ?iTitleNode bf:mainTitle ?instTitle .

            OPTIONAL { ?instance bf:electronicLocator ?locator . }
            OPTIONAL { ?instance bf:status ?status . }
        }
        ORDER BY ASC(?workTitle) ASC(?instance)
        """
        results = graph.query(sparql)
        works_map: dict[str, dict] = {}

        for row in results:
            author_str = str(row.author)
            if author and author.lower() not in author_str.lower():
                continue

            work_uri = row.work
            wid = str(work_uri).split("/")[-1]
            if wid not in works_map:
                subjects = sorted(
                    str(graph.value(subj_uri, RDFS.label))
                    for subj_uri in graph.objects(work_uri, BF.subject)
                )
                works_map[wid] = {
                    "work_id": wid,
                    "title": str(row.workTitle),
                    "author": author_str,
                    "ddc": str(row.ddc),
                    "subjects": subjects,
                    "editions": [],
                }

            inst_id = str(row.instance).split("/")[-1]
            works_map[wid]["editions"].append(
                {
                    "instance_id": inst_id,
                    "title": str(row.instTitle),
                    "language": str(row.lang),
                    "electronic_locator": str(row.locator) if row.locator else None,
                    "status": str(row.status) if row.status else "active",
                }
            )

        works_list = list(works_map.values())
        return {
            "engine": "sparql",
            "total_works": len(works_list),
            "works": works_list,
        }

    @app.get("/api/v1/sql/works")
    def get_works_sql(author: str | None = Query(default=None)):
        works_list = query_works_sql(app.state.sql_conn, author_filter=author)
        return {
            "engine": "sqlite",
            "total_works": len(works_list),
            "works": works_list,
        }

    @app.get("/api/v1/instances/{instance_id}")
    def get_instance(instance_id: str):
        graph: Graph = app.state.rdf_graph
        inst_uri = CELESTIA[f"instance/{instance_id}"]

        if (inst_uri, RDF.type, BF.Instance) not in graph:
            raise HTTPException(status_code=404, detail=f"Instance '{instance_id}' not found.")

        status_val = graph.value(inst_uri, BF.status)
        status_str = str(status_val) if status_val else "active"

        title_node = graph.value(inst_uri, BF.title)
        title_str = str(graph.value(title_node, BF.mainTitle)) if title_node else "Untitled"
        locator_val = graph.value(inst_uri, BF.electronicLocator)

        if status_str == "deprecated":
            reason_val = graph.value(inst_uri, CELESTIA.reaperReason)
            inv_time = graph.value(inst_uri, PROV.invalidatedAtTime)
            return JSONResponse(
                status_code=410,
                content={
                    "error": "Gone",
                    "shade": "Ronova (Shade of Death)",
                    "instance_id": instance_id,
                    "title": title_str,
                    "status": "deprecated",
                    "reason": str(reason_val) if reason_val else "Link-rot detected",
                    "invalidated_at": str(inv_time) if inv_time else None,
                },
            )

        return {
            "instance_id": instance_id,
            "title": title_str,
            "language": str(graph.value(inst_uri, BF.language)),
            "electronic_locator": str(locator_val) if locator_val else None,
            "status": status_str,
        }

    @app.post("/api/v1/ronova/reap")
    async def trigger_ronova_reaper():
        report = await reap_dead_links(app.state.rdf_graph)
        cur = app.state.sql_conn.cursor()
        for rec in report["records"]:
            inst_id = rec["instance_uri"].split("/")[-1]
            cur.execute(
                "UPDATE instances SET status = ? WHERE instance_id = ?",
                (rec["status"], inst_id),
            )
        app.state.sql_conn.commit()
        return report

    return app


app = create_app()