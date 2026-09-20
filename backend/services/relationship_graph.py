"""Build a compact evidence-backed relationship graph for the resolved identity."""
from __future__ import annotations

import hashlib

from backend.models import ConnectedProfile, GraphEdge, GraphNode, RelationshipGraph, ReportField, SearchContext
from backend.services.platforms import canonical_url


def _id(prefix: str, value: str) -> str:
    digest = hashlib.sha1(value.encode("utf-8", "ignore")).hexdigest()[:10]
    return f"{prefix}-{digest}"


def build_relationship_graph(context: SearchContext, profiles: list[ConnectedProfile], fields: list[ReportField]) -> RelationshipGraph:
    nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []
    node_ids: set[str] = set()
    edge_ids: set[str] = set()

    def add_node(node: GraphNode) -> None:
        if node.id not in node_ids:
            node_ids.add(node.id)
            nodes.append(node)

    def add_edge(source: str, target: str, relation: str, status: str, source_urls: list[str]) -> None:
        edge_id = _id("edge", f"{source}|{target}|{relation}")
        if edge_id in edge_ids or source == target:
            return
        edge_ids.add(edge_id)
        edges.append(GraphEdge(
            id=edge_id,
            source=source,
            target=target,
            relation=relation,
            status=status,
            source_urls=list(dict.fromkeys(source_urls))[:8],
            evidence_count=len(set(source_urls)),
        ))

    identity_label = context.name or context.username or "Identity under review"
    identity_id = "identity-root"
    add_node(GraphNode(id=identity_id, label=identity_label, type="identity", status="supported"))

    field_map = {field.field: field for field in fields}
    for field_name, node_type, relation in (
        ("organization", "organization", "affiliated with"),
        ("role", "role", "role"),
        ("department", "department", "department"),
    ):
        value = getattr(context, field_name, None)
        report_field = field_map.get(field_name)
        if not value or not report_field or report_field.status not in {"supported", "partial"}:
            continue
        status = "supported" if report_field.status == "supported" else "partial"
        node_id = _id(node_type, value.casefold())
        add_node(GraphNode(id=node_id, label=value, type=node_type, status=status))
        urls = [e.source_url for e in report_field.evidence]
        add_edge(identity_id, node_id, relation, status, urls)

    profile_node_by_url: dict[str, str] = {}
    graph_profiles = [p for p in profiles if p.status in {"supported", "partially_supported", "conflicting"}]
    for item in graph_profiles[:10]:
        profile = item.profile
        status = "conflicting" if item.status == "conflicting" else "supported" if item.status == "supported" else "partial"
        label = profile.username and f"@{profile.username}" or profile.display_name or profile.profile_slug or profile.domain
        url = canonical_url(profile.url) or profile.url
        node_id = _id("profile", url)
        profile_node_by_url[url] = node_id
        add_node(GraphNode(id=node_id, label=f"{profile.platform}: {label}", type="profile", status=status, url=profile.url))
        add_edge(identity_id, node_id, "public trace", status, [e.source_url for e in item.evidence] or [profile.url])

    # Explicit profile links become graph edges only when the target is a discovered canonical profile.
    for item in graph_profiles[:10]:
        source_url = canonical_url(item.profile.url) or item.profile.url
        source_id = profile_node_by_url.get(source_url)
        if not source_id:
            continue
        for link in item.profile.external_profile_links:
            target_url = canonical_url(link.target_url) or link.target_url
            target_id = profile_node_by_url.get(target_url)
            if target_id:
                add_edge(source_id, target_id, link.relation_type.replace("_", " "), "supported" if link.evidence_type == "direct_page" else "partial", [link.evidence_origin])

    # Context nodes: keep the graph useful but bounded. Only supported/partial profiles feed them.
    remaining = 22 - len(nodes)
    entity_specs = [
        ("projects", "project", "project"),
        ("events", "event", "event"),
        ("publications", "publication", "publication"),
        ("education", "education", "education"),
        ("location", "location", "location"),
    ]
    entity_seen: set[tuple[str, str]] = set()
    for item in [p for p in graph_profiles if p.status in {"supported", "partially_supported"}]:
        profile_url = canonical_url(item.profile.url) or item.profile.url
        profile_id = profile_node_by_url.get(profile_url)
        if not profile_id:
            continue
        for attr, node_type, relation in entity_specs:
            for value in getattr(item.profile, attr, [])[:2]:
                if remaining <= 0:
                    break
                key = (node_type, value.casefold())
                node_id = _id(node_type, value.casefold())
                if key not in entity_seen:
                    entity_seen.add(key)
                    add_node(GraphNode(id=node_id, label=value, type=node_type, status="supported"))
                    remaining -= 1
                add_edge(profile_id, node_id, relation, "supported", [item.profile.url])
            if remaining <= 0:
                break
        if remaining <= 0:
            break

    supported_edges = sum(edge.status == "supported" for edge in edges)
    summary = f"{len(nodes)} evidence-backed node(s) and {len(edges)} relationship edge(s); {supported_edges} edge(s) are directly supported."
    return RelationshipGraph(nodes=nodes, edges=edges, summary=summary)
